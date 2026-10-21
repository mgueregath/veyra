from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import assisted, flows, training
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, Frame, Model, Project, Run, User
from .flows import get_flow

router = APIRouter(prefix="/api", tags=["assisted"])
ROLES = {"admin", "reviewer"}


class PreIn(BaseModel):
    model_id: int | None = None  # un modelo… o un flujo guardado (exactamente uno de los dos)
    flow_id: int | None = None
    scope: str = Field("unreviewed", pattern="^(unreviewed|all|frame_ids)$")
    frame_ids: list[int] | None = None
    min_score: float = Field(0.15, ge=0, le=1)  # solo para un modelo; en un flujo cada fuente trae su mínimo

    @model_validator(mode="after")
    def one_detector(self):
        if (self.model_id is None) == (self.flow_id is None):
            raise ValueError("indique un modelo (model_id) o un flujo (flow_id), no ambos")
        return self


def _project(db, user, pid):
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role not in ROLES:
        raise HTTPException(403, "Tu rol no permite esta acción")
    return p


@router.post("/projects/{project_id}/preannotations")
def preannotate(project_id: int, body: PreIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Lanza (en segundo plano, en la cola de la GPU) la preanotación de las imágenes con un modelo."""
    p = _project(db, user, project_id)
    label = ""
    if body.flow_id is not None:
        f = get_flow(db, p, body.flow_id)
        try:
            flows.validate(db, p, f.spec)
        except flows.FlowError as e:
            raise HTTPException(422, str(e)) from None
        label = f"flujo {f.name}"
    else:
        m = db.get(Model, body.model_id)
        if not m or m.project_id != p.id:
            raise HTTPException(404, "Modelo no encontrado")
        label = m.name
    if body.scope == "frame_ids":
        valid = set(
            db.scalars(
                select(Frame.id).where(
                    Frame.project_id == p.id, Frame.deleted_at.is_(None), Frame.id.in_(body.frame_ids or [])
                )
            )
        )
        if not valid:
            raise HTTPException(422, "Indique al menos una imagen del proyecto en frame_ids")
        body.frame_ids = sorted(valid)
    if not assisted.frames_in_scope(db, p, body.scope, body.frame_ids):
        raise HTTPException(422, "No hay imágenes en ese alcance (¿todas están revisadas?)")
    run = Run(
        project_id=p.id,
        kind="preannotation",
        name=f"preanotar con {label} ({body.scope})",
        params={**body.model_dump(), "project_id": p.id},
        status="queued",
        created_by=user.id,
    )
    db.add(run)
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="preannotation.start",
            detail={"model_id": body.model_id, "flow_id": body.flow_id, "scope": body.scope},
        )
    )
    db.commit()
    training.enqueue(run.id)
    return {"run_id": run.id, "status": "queued"}


@router.get("/projects/{project_id}/uncertain")
def uncertain(
    project_id: int,
    model_id: int,
    limit: int = Query(30, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    p = _project(db, user, project_id)
    m = db.get(Model, model_id)
    if not m or m.project_id != p.id:
        raise HTTPException(404, "Modelo no encontrado")
    items = assisted.rank_uncertain(db, p, model_id, limit)
    info = (
        {f.id: f for f in db.scalars(select(Frame).where(Frame.id.in_([i["frame_id"] for i in items])))}
        if items
        else {}
    )
    for i in items:
        f = info[i["frame_id"]]
        i.update(position=f.position, width=f.width, height=f.height, video_id=f.video_id)
    return items
