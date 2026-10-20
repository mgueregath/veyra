"""Detección con un modelo sobre una imagen o un conjunto de imágenes (del proyecto o subidas)."""

import json
import secrets
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import inference, usage
from ..config import settings
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, Frame, Model, Project, User

router = APIRouter(prefix="/api", tags=["detect"])
OPERATE = {"admin", "reviewer"}
MAX_IMAGES = 200
MAX_UPLOAD = 25 * 1024 * 1024
MIN_SCORE = 0.05  # por debajo no se devuelve nada: el navegador filtra con su propio umbral


class DetectIn(BaseModel):
    model_id: int
    first: int = Field(ge=1)  # número de imagen del proyecto (el que se ve en el editor)
    last: int | None = Field(None, ge=1)


def _root() -> Path:
    return Path(settings.runs_root) / "detect"


def _check(db: Session, user: User, pid: int, model_id: int) -> tuple[Project, Model]:
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role not in OPERATE:
        raise HTTPException(403, "Tu rol no permite correr modelos")
    m = db.get(Model, model_id)
    if not m or m.project_id != p.id:
        raise HTTPException(404, "Modelo no encontrado")
    return p, m


def _items(m: Model, batch: str, rows: list[dict], preds: dict[str, list]) -> dict:
    out = []
    for i, r in enumerate(rows):
        dets = [
            {"bbox": list(p.bbox), "label": p.label, "score": round(p.score, 4)}
            for p in preds.get(r["path"], [])
            if p.score >= MIN_SCORE
        ]
        dets.sort(key=lambda d: -d["score"])
        out.append(
            {
                "index": i,
                "name": r["name"],
                "frame_id": r.get("frame_id"),
                "width": r["width"],
                "height": r["height"],
                "image_url": f"/api/frames/{r['frame_id']}/image"
                if r.get("frame_id")
                else f"/api/detect/{batch}/image/{i}",
                "dets": dets,
            }
        )
    return {"id": batch, "model_id": m.id, "model": m.name, "classes": m.classes, "items": out}


def _save(batch: str, project_id: int, user: User, result: dict) -> None:
    d = _root() / batch
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps({"project_id": project_id, "user_id": user.id, **result}))


@router.post("/projects/{project_id}/detect")
def detect_project_images(
    project_id: int, body: DetectIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Corre el modelo sobre una imagen del proyecto (first = last) o sobre un rango de ellas."""
    p, m = _check(db, user, project_id, body.model_id)
    last = body.last if body.last is not None else body.first
    if last < body.first:
        raise HTTPException(422, "El final del rango no puede ser menor que el inicio")
    if last - body.first + 1 > MAX_IMAGES:
        raise HTTPException(422, f"Como máximo {MAX_IMAGES} imágenes por vez")
    frames = db.scalars(
        select(Frame)
        .where(Frame.project_id == p.id, Frame.position >= body.first - 1, Frame.position <= last - 1)
        .order_by(Frame.position)
    ).all()
    if not frames:
        raise HTTPException(404, "No hay imágenes con esos números en el proyecto")
    media = Path(settings.media_root).resolve()
    rows = [
        {
            "path": str(media / f.image_path),
            "name": f"Imagen {f.position + 1}",
            "frame_id": f.id,
            "width": f.width,
            "height": f.height,
        }
        for f in frames
    ]
    return _run(db, user, p, m, rows)


@router.post("/projects/{project_id}/detect/upload")
async def detect_uploaded_images(
    project_id: int,
    model_id: int = Form(...),
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Corre el modelo sobre imágenes subidas desde el equipo de la persona (no pasan a formar parte del proyecto)."""
    p, m = _check(db, user, project_id, model_id)
    if not files or len(files) > MAX_IMAGES:
        raise HTTPException(422, f"Suba entre 1 y {MAX_IMAGES} imágenes")
    try:
        usage.check_quota(db, p)
    except ValueError as e:
        raise HTTPException(507, str(e)) from None
    batch = secrets.token_hex(8)
    d = _root() / batch
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in files:
        raw = await f.read(MAX_UPLOAD + 1)
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, f"«{f.filename}» pesa más de {MAX_UPLOAD // 2**20} MB")
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(422, f"«{f.filename}» no es una imagen válida (use JPG, PNG o similar)")
        path = d / f"{len(rows)}.jpg"
        cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        rows.append(
            {
                "path": str(path),
                "name": (f.filename or f"imagen {len(rows) + 1}")[:80],
                "width": img.shape[1],
                "height": img.shape[0],
            }
        )
    return _run(db, user, p, m, rows, batch)


def _run(db: Session, user: User, p: Project, m: Model, rows: list[dict], batch: str | None = None) -> dict:
    batch = batch or secrets.token_hex(8)
    try:
        preds = inference.predict_paths(m, [r["path"] for r in rows])
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from None
    result = _items(m, batch, rows, preds)
    _save(batch, p.id, user, result)
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="detect.run",
            target=f"model:{m.id}",
            detail={"images": len(rows), "batch": batch},
        )
    )
    db.commit()
    return result


def _batch(db: Session, user: User, batch: str) -> tuple[Path, dict]:
    if not batch.isalnum():
        raise HTTPException(404, "Resultado no encontrado")
    f = _root() / batch / "result.json"
    if not f.is_file():
        raise HTTPException(404, "Resultado no encontrado")
    data = json.loads(f.read_text())
    if not role_in(db, user, data["project_id"]):
        raise HTTPException(404, "Resultado no encontrado")
    return f.parent, data


@router.get("/detect/{batch}")
def get_detection(batch: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, data = _batch(db, user, batch)
    return {k: v for k, v in data.items() if k not in ("project_id", "user_id")}


@router.get("/detect/{batch}/image/{index}")
def detection_image(batch: str, index: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d, _ = _batch(db, user, batch)
    f = d / f"{index}.jpg"
    if not f.is_file():
        raise HTTPException(404, "Imagen no encontrada")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
