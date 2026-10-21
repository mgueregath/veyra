"""Flujos de detección guardados del proyecto y los procedimientos de su espacio de trabajo."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import flows, procedures
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, DetectionFlow, Model, Project, User

router = APIRouter(prefix="/api", tags=["flows"])
EDIT = {"admin", "reviewer"}


def _project(db: Session, user: User, pid: int, edit: bool = False) -> Project:
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if edit and role not in EDIT:
        raise HTTPException(403, "Solo administración y revisión pueden crear o cambiar flujos")
    return p


class FlowIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    spec: dict


def _names(db: Session, p: Project) -> dict[str, str]:
    names = {
        "baseline:appearance": "manchas (apariencia)",
        "proposals:motion": "movimiento",
        "proposals:blob": "manchas guardadas",
    }
    for m in db.scalars(select(Model).where(Model.project_id == p.id)):
        names[f"model:{m.id}"] = m.name
    return names


def _out(f: DetectionFlow, names: dict[str, str]) -> dict:
    try:
        text = flows.describe(flows.parse(f.spec), names)
    except flows.FlowError:
        text = "(flujo inválido)"
    return {"id": f.id, "name": f.name, "spec": f.spec, "text": text, "updated_at": f.updated_at.isoformat()}


def get_flow(db: Session, project: Project, flow_id: int) -> DetectionFlow:
    f = db.get(DetectionFlow, flow_id)
    if not f or f.project_id != project.id:
        raise HTTPException(404, "Flujo no encontrado")
    return f


def _checked(db: Session, p: Project, body: FlowIn) -> dict:
    try:
        node = flows.validate(db, p, body.spec)
    except flows.FlowError as e:
        raise HTTPException(422, str(e)) from None
    return node.model_dump(exclude_none=True)


@router.get("/projects/{project_id}/flows")
def list_flows(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id)
    names = _names(db, p)
    rows = db.scalars(select(DetectionFlow).where(DetectionFlow.project_id == p.id).order_by(DetectionFlow.name)).all()
    return [_out(f, names) for f in rows]


@router.post("/projects/{project_id}/flows")
def create_flow(project_id: int, body: FlowIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id, edit=True)
    spec = _checked(db, p, body)
    name = body.name.strip()
    if db.scalar(select(DetectionFlow.id).where(DetectionFlow.project_id == p.id, DetectionFlow.name == name)):
        raise HTTPException(409, "Ya hay un flujo con ese nombre")
    f = DetectionFlow(project_id=p.id, name=name, spec=spec, created_by=user.id)
    db.add(f)
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="flow.create", target=name))
    db.commit()
    return _out(f, _names(db, p))


@router.put("/projects/{project_id}/flows/{flow_id}")
def update_flow(
    project_id: int, flow_id: int, body: FlowIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = _project(db, user, project_id, edit=True)
    f = get_flow(db, p, flow_id)
    spec = _checked(db, p, body)
    name = body.name.strip()
    if db.scalar(
        select(DetectionFlow.id).where(
            DetectionFlow.project_id == p.id, DetectionFlow.name == name, DetectionFlow.id != f.id
        )
    ):
        raise HTTPException(409, "Ya hay un flujo con ese nombre")
    f.name, f.spec = name, spec
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="flow.update", target=name))
    db.commit()
    return _out(f, _names(db, p))


@router.delete("/projects/{project_id}/flows/{flow_id}")
def delete_flow(project_id: int, flow_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id, edit=True)
    f = get_flow(db, p, flow_id)
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="flow.delete", target=f.name))
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.get("/projects/{project_id}/procedures")
def list_procedures(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Procedimientos que declara el espacio de trabajo del proyecto (requiere un worker en línea)."""
    p = _project(db, user, project_id, edit=True)
    return procedures.list_procedures(db, p)
