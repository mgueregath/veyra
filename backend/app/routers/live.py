import datetime as dt
import os
import re
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import live, usage
from ..config import settings
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, Frame, LiveSession, Model, Project, Proposal, User, Video

router = APIRouter(prefix="/api", tags=["live"])
OPERATE = {"admin", "reviewer"}


class LiveIn(BaseModel):
    name: str | None = Field(None, max_length=128)
    source_kind: str = Field(pattern="^(video|url)$")
    video_id: int | None = None
    url: str | None = None
    model_id: int | None = None
    threshold: float = Field(0.25, ge=0, le=1)
    imgsz: int = Field(1280, ge=320, le=2048)
    loop: bool = True
    no_realtime: bool = False  # leer el archivo lo más rápido posible (medir rendimiento)


class ControlIn(BaseModel):
    threshold: float | None = Field(None, ge=0, le=1)
    paused: bool | None = None
    record: bool | None = None
    show_boxes: bool | None = None
    show_conf: bool | None = None
    show_classes: list[int] | None = None  # índices de las clases que se dibujan; null = todas


def _session(db: Session, user: User, sid: int, operate: bool = False) -> LiveSession:
    s = db.get(LiveSession, sid)
    role = role_in(db, user, s.project_id) if s else None
    if not s or not role:
        raise HTTPException(404, "Sesión no encontrada")
    if operate and role not in OPERATE:
        raise HTTPException(403, "Tu rol no permite operar sesiones en vivo")
    return s


def _refresh(db: Session, s: LiveSession) -> dict | None:
    """Estadísticas del proceso; si murió, la sesión se cierra (con lo último que se sabía)."""
    if s.status != "running":
        return None
    st = live.worker_stats(s) if live.alive(s) else None
    if st is None and not live.alive(s):
        s.status, s.ended_at = "stopped", dt.datetime.now(dt.timezone.utc)
        s.error = s.error or "el proceso terminó (¿terminó el archivo o falló la fuente?)"
        db.commit()
    return st


def _out(db: Session, s: LiveSession, stats: dict | None = None) -> dict:
    u = db.get(User, s.user_id)
    return {
        "id": s.id,
        "name": s.name,
        "status": s.status,
        "source_kind": s.source_kind,
        "source": live.mask_source(s.source, s.source_kind),
        "model_id": s.model_id,
        "params": s.params,
        "started_at": s.started_at.isoformat(),
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "user": u.username if u else None,
        "error": s.error,
        "summary": s.summary,
        "stats": stats,
    }


@router.post("/projects/{project_id}/live")
def start_session(project_id: int, body: LiveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    role = role_in(db, user, project_id) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role not in OPERATE:
        raise HTTPException(403, "Tu rol no permite iniciar sesiones en vivo")
    if body.source_kind == "video":
        v = db.get(Video, body.video_id or 0)
        if not v or v.project_id != p.id or not v.path or not os.path.isfile(v.path):
            raise HTTPException(422, "Elija un video del proyecto con archivo original disponible")
        source, label = os.path.realpath(v.path), v.name
    else:
        try:
            source = live.validate_url(body.url or "")
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        label = live.mask_source(source, "url")
    model = None
    if body.model_id is not None:
        model = db.get(Model, body.model_id)
        if not model or model.project_id != p.id:
            raise HTTPException(404, "Modelo no encontrado")
    if live.active_count() >= settings.live_max:
        raise HTTPException(
            409, f"Ya hay {settings.live_max} sesiones en vivo activas (cada una ocupa la GPU): detenga una"
        )
    s = LiveSession(
        project_id=p.id,
        user_id=user.id,
        model_id=model.id if model else None,
        name=body.name or f"{label}",
        source_kind=body.source_kind,
        source=source,
        status="starting",
        params={"threshold": body.threshold, "imgsz": body.imgsz, "loop": body.loop, "no_realtime": body.no_realtime},
    )
    db.add(s)
    db.flush()
    try:
        live.start_worker(
            s,
            model.path if model else None,
            model.classes if model else [],
            Path(settings.runs_root) / "live" / str(s.id),
        )
    except RuntimeError as e:
        s.status, s.error, s.ended_at = "failed", str(e), dt.datetime.now(dt.timezone.utc)
        db.commit()
        raise HTTPException(424, str(e)) from None
    s.status = "running"
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="live.start",
            target=f"live:{s.id}",
            detail={"kind": s.source_kind, "model_id": s.model_id},
        )
    )
    db.commit()
    return _out(db, s)


@router.get("/projects/{project_id}/live")
def list_sessions(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(Project, project_id) or not role_in(db, user, project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    rows = db.scalars(
        select(LiveSession).where(LiveSession.project_id == project_id).order_by(LiveSession.id.desc()).limit(50)
    ).all()
    return [_out(db, s, _refresh(db, s)) for s in rows]


@router.get("/live/{session_id}")
def get_session(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _session(db, user, session_id)
    return _out(db, s, _refresh(db, s))


@router.get("/live/{session_id}/stream")
def stream(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _session(db, user, session_id)
    if s.status != "running" or not live.alive(s):
        raise HTTPException(409, "La sesión no está activa")
    return StreamingResponse(
        live.stream_iter(s),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/live/{session_id}/control")
def control(session_id: int, body: ControlIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _session(db, user, session_id, operate=True)
    if s.status != "running" or not live.alive(s):
        raise HTTPException(409, "La sesión no está activa")
    msg = body.model_dump(exclude_unset=True)
    out = live.worker_control(s, msg)
    if "threshold" in msg:
        s.params = {**s.params, "threshold": out["threshold"]}
        db.commit()
    return out


@router.post("/live/{session_id}/save-frame")
def save_frame(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Guarda el fotograma actual (sin cajas dibujadas) como imagen del proyecto; lo detectado pasa a propuestas.

    Es el camino de vuelta al etiquetado: los falsos positivos y los piojos que el modelo pierde en vivo van a la cola.
    """
    s = _session(db, user, session_id, operate=True)
    if s.status != "running" or not live.alive(s):
        raise HTTPException(409, "La sesión no está activa")
    try:
        raw, dets = live.worker_frame(s)
    except Exception:
        raise HTTPException(503, "Todavía no hay un fotograma disponible") from None
    try:
        usage.check_quota(db, db.get(Project, s.project_id))
    except ValueError as e:
        raise HTTPException(507, str(e)) from None
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(502, "El fotograma recibido no es una imagen válida")
    h, w = img.shape[:2]
    v = db.scalar(select(Video).where(Video.project_id == s.project_id, Video.name == f"live-{s.id}"))
    if v is None:
        v = Video(
            project_id=s.project_id,
            name=f"live-{s.id}",
            path=None,
            meta={"live_session": s.id, "source": live.mask_source(s.source, s.source_kind)},
        )
        db.add(v)
        db.flush()
    idx = (db.scalar(select(func.max(Frame.frame_index)).where(Frame.video_id == v.id)) or 0) + 1
    rel = f"live/{s.id}/f{idx:06d}.jpg"
    out = Path(settings.media_root) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    t = (dt.datetime.now(dt.timezone.utc) - s.started_at).total_seconds()
    pos = (db.scalar(select(func.max(Frame.position)).where(Frame.project_id == s.project_id)) or 0) + 1
    f = Frame(
        project_id=s.project_id,
        video_id=v.id,
        frame_index=idx,
        t=round(t, 3),
        image_path=rel,
        width=w,
        height=h,
        stratum="live",
        position=pos,
        meta={"origin": "live", "t_src": "wall", "session": s.id},
    )
    tag = ["model", f"model:{s.model_id}"] if s.model_id else ["live"]
    f.proposals = [
        Proposal(
            x=round(d[0]),
            y=round(d[1]),
            w=max(1, round(d[2])),
            h=max(1, round(d[3])),
            sources=tag,
            score=round(d[4], 4),
        )
        for d in dets
    ]
    db.add(f)
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=s.project_id,
            action="live.save_frame",
            target=f"live:{s.id}",
            detail={"frame_index": idx, "boxes": len(dets)},
        )
    )
    db.commit()
    return {"frame_id": f.id, "position": f.position, "boxes": len(dets)}


@router.delete("/live/{session_id}")
def stop_session(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _session(db, user, session_id, operate=True)
    if s.status in ("running", "starting"):
        s.summary = live.worker_stats(s)
        live.stop_worker(s.id)
        s.status, s.ended_at = "stopped", dt.datetime.now(dt.timezone.utc)
        db.add(AuditLog(user_id=user.id, project_id=s.project_id, action="live.stop", target=f"live:{s.id}"))
        db.commit()
    return _out(db, s)


def _clips_dir(s: LiveSession) -> Path:
    return Path(settings.runs_root) / "live" / str(s.id)


@router.get("/live/{session_id}/clips")
def list_clips(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _session(db, user, session_id)
    d = _clips_dir(s)
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(d.glob("clip_*.mp4"))] if d.is_dir() else []


@router.get("/live/{session_id}/clips/{name}")
def download_clip(session_id: int, name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = _session(db, user, session_id)
    if not re.fullmatch(r"clip_\d+\.mp4", name):
        raise HTTPException(404, "Clip no encontrado")
    p = _clips_dir(s) / name
    if not p.is_file():
        raise HTTPException(404, "Clip no encontrado")
    return FileResponse(p, media_type="video/mp4", filename=name)
