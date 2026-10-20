from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import datasets as ds
from .. import training
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, DatasetVersion, Model, Project, Run, User

router = APIRouter(prefix="/api", tags=["models"])
ROLES = {"admin", "reviewer"}


def model_out(m: Model, db: Session) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "arch": m.arch,
        "classes": m.classes,
        "dataset_version_id": m.dataset_version_id,
        "metrics": m.metrics,
        "params": m.params,
        "run_id": m.run_id,
        "created_at": m.created_at.isoformat(),
        "created_by": db.get(User, m.created_by).username,
    }


@router.get("/projects/{project_id}/models")
def list_models(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Cualquier miembro puede ver los modelos y sus indicadores; entrenar y operar es de administración y revisión."""
    p = db.get(Project, project_id)
    if not p or not role_in(db, user, project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    return [
        model_out(m, db) for m in db.scalars(select(Model).where(Model.project_id == p.id).order_by(Model.id.desc()))
    ]


class TrainIn(BaseModel):
    dataset_version_id: int
    arch: str = "yolov8n"
    epochs: int = Field(50, ge=1, le=300)
    imgsz: int = Field(1280, ge=320, le=2048)
    batch: int = Field(8, ge=1, le=64)
    seed: int = 0
    model_name: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")


@router.post("/projects/{project_id}/trainings")
def start_training(project_id: int, body: TrainIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    role = role_in(db, user, project_id) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role not in ROLES:
        raise HTTPException(403, "Tu rol no permite entrenar")
    if body.arch not in training.ARCHS:
        raise HTTPException(422, f"Arquitectura desconocida (opciones: {sorted(training.ARCHS)})")
    v = db.get(DatasetVersion, body.dataset_version_id)
    if not v or v.project_id != p.id:
        raise HTTPException(404, "Versión de dataset no encontrada")
    st = ds.version_stats(db, v, p)["splits"]
    boxes = lambda s: sum(st[s]["boxes"].values())  # noqa: E731
    if not st["train"]["frames"] or boxes("train") == 0:
        raise HTTPException(422, "La partición train no tiene imágenes con cajas: no se puede entrenar")
    if not st["val"]["frames"]:
        raise HTTPException(
            422, "La partición val está vacía: no habría cómo elegir el mejor modelo (cambie el reparto o la semilla)"
        )
    run = Run(
        project_id=p.id,
        kind="training",
        name="",
        dataset_version_id=v.id,
        params={},
        status="queued",
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    name = body.model_name or f"{body.arch}-{v.name}-r{run.id}"
    if db.scalar(select(Model.id).where(Model.project_id == p.id, Model.name == name)):
        db.rollback()
        raise HTTPException(409, "Ya existe un modelo con ese nombre")
    run.name = f"entrenar {name}"
    run.params = {**body.model_dump(), "model_name": name}
    db.add(
        AuditLog(user_id=user.id, project_id=p.id, action="training.start", target=f"run:{run.id}", detail=run.params)
    )
    db.commit()
    training.enqueue(run.id)
    return {"run_id": run.id, "model_name": name, "status": "queued"}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    role = role_in(db, user, r.project_id) if r else None
    if not r or not role:
        raise HTTPException(404, "Ejecución no encontrada")
    if role not in ROLES:
        raise HTTPException(403, "Tu rol no permite cancelar")
    if r.status not in ("queued", "running"):
        raise HTTPException(409, f"La ejecución ya terminó ({r.status})")
    training.cancel(r.id)
    if r.status == "queued":
        r.status = "cancelled"
        db.commit()
    return {"ok": True}


@router.get("/models/{model_id}")
def get_model(model_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.get(Model, model_id)
    if not m or not role_in(db, user, m.project_id):
        raise HTTPException(404, "Modelo no encontrado")
    return model_out(m, db)
