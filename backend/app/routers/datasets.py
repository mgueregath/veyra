import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import datasets as ds
from ..config import settings
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, DatasetVersion, Project, User
from ..schemas import DatasetIn, ExportIn
from ..workflow import STATUSES

router = APIRouter(prefix="/api", tags=["datasets"])
ALLOWED_INCLUDE = {"final", "agreed", "single"}


def _project(db, user, pid, roles=None):
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if roles and role not in roles:
        raise HTTPException(403, "Tu rol no permite esta acción")
    return p


def _version(db, user, vid, roles=None):
    v = db.get(DatasetVersion, vid)
    if not v:
        raise HTTPException(404, "Versión no encontrada")
    return v, _project(db, user, v.project_id, roles)


@router.post("/projects/{project_id}/datasets")
def create_dataset(project_id: int, body: DatasetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id, {"admin"})
    bad = set(body.include) - ALLOWED_INCLUDE
    if bad or not body.include:
        raise HTTPException(422, f"`include` solo admite {sorted(ALLOWED_INCLUDE)} (estados válidos: {STATUSES})")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", body.name):
        raise HTTPException(422, "El nombre solo puede tener letras, números, punto, guion y guion bajo")
    if any(f < 0 for f in body.split) or abs(sum(body.split) - 1) > 1e-6:
        raise HTTPException(422, "Las fracciones de train/val/test deben ser positivas y sumar 1")
    try:
        v = ds.create_version(db, p, body.name, body.include, body.split, body.seed, user.id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe una versión con ese nombre") from None
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="dataset.create",
            target=f"dataset:{v.id}",
            detail={"name": v.name, "items": len(v.items)},
        )
    )
    db.commit()
    return ds.version_stats(db, v, p)


@router.get("/projects/{project_id}/datasets")
def list_datasets(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id)
    return [
        {"id": v.id, "name": v.name, "n_items": len(v.items), "rules": v.rules, "created_at": v.created_at.isoformat()}
        for v in db.scalars(
            select(DatasetVersion).where(DatasetVersion.project_id == p.id).order_by(DatasetVersion.id.desc())
        )
    ]


@router.get("/datasets/{version_id}")
def dataset_detail(version_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v, p = _version(db, user, version_id)
    return ds.version_stats(db, v, p)


@router.post("/datasets/{version_id}/export")
def export_dataset(version_id: int, body: ExportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v, p = _version(db, user, version_id, {"admin", "reviewer"})
    out = settings.export_root / p.slug / (v.name + ("+descartadas" if body.include_discarded else ""))
    manifest = ds.export_version(db, v, p, out, body.format, body.include_discarded)
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="dataset.export",
            target=f"dataset:{v.id}",
            detail={"format": body.format, "include_discarded": body.include_discarded},
        )
    )
    db.commit()
    return {"path": str(out.resolve()), "manifest": manifest}
