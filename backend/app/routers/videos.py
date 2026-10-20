import os
import re
import threading
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from .. import proposers, usage
from .. import video as vid
from ..config import settings
from ..db import get_db
from ..deps import WRITE_ROLES, current_user, role_in
from ..models import AuditLog, Frame, Project, Proposal, User, Video

router = APIRouter(prefix="/api", tags=["video"])
CHUNK = int(float(os.environ.get("LABELHUB_CHUNK_MB", 4)) * 1024 * 1024)  # tope por respuesta Range
_LOCK = threading.Lock()  # un solo fotograma/instantánea a la vez por proceso (ffmpeg es pesado)


def _video_for(db: Session, video_id: int, user: User, write: bool = False) -> Video:
    v = db.get(Video, video_id)
    role = role_in(db, user, v.project_id) if v else None
    if not v or not role:  # no se revela si existe
        raise HTTPException(404, "Video no encontrado")
    if write and role not in WRITE_ROLES:
        raise HTTPException(403, "Tu rol no permite modificar este proyecto")
    return v


def _real_path(v: Video) -> str:
    """La ruta sale SIEMPRE de la base de datos (nunca de la petición), así no hay recorrido de rutas."""
    if not v.path:
        raise HTTPException(404, "Este video no tiene archivo original")
    p = os.path.realpath(v.path if os.path.isabs(v.path) else Path(settings.media_root) / v.path)
    if not os.path.isfile(p):
        raise HTTPException(404, "Archivo de video no disponible")
    return p


def _pts(db: Session, v: Video) -> list[float]:
    if "pts" not in (v.meta or {}):
        pts = vid.video_pts(_real_path(v))
        v.meta = {**(v.meta or {}), "pts": pts}
        db.commit()
    return v.meta["pts"]


@router.get("/videos/{video_id}/info")
def video_info(video_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = _video_for(db, video_id, user)
    p = _real_path(v)
    if "fps" not in (v.meta or {}):
        v.meta = {**(v.meta or {}), **vid.video_info(p)}
        db.commit()
    return {"id": v.id, "name": v.name, "fps": v.meta["fps"], "dur": v.meta["dur"]}


@router.get("/videos/{video_id}/pts")
def video_pts(
    video_id: int, a: float = 0.0, b: float = 1e9, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """PTS reales de los fotogramas entre a y b (segundos)."""
    import bisect

    v = _video_for(db, video_id, user)
    ps = _pts(db, v)
    return {"pts": ps[bisect.bisect_left(ps, a) : bisect.bisect_right(ps, b)]}


def _range_response(path: str, rng: str | None) -> Response:
    size = os.path.getsize(path)
    a, b, code = 0, size - 1, 200
    if rng:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1) == "":  # sufijo: los últimos N bytes
            a, b = max(size - int(m.group(2)), 0), size - 1
        else:
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else size - 1
        if a >= size or b < a:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        b = min(b, size - 1, a + CHUNK - 1)  # el navegador pide el resto a medida que lo necesita
        code = 206
    n = b - a + 1

    def body():
        with open(path, "rb") as f:
            f.seek(a)
            left = n
            while left > 0:
                blk = f.read(min(256 * 1024, left))
                if not blk:
                    break
                left -= len(blk)
                yield blk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(n), "Cache-Control": "private, max-age=3600"}
    if code == 206:
        headers["Content-Range"] = f"bytes {a}-{b}/{size}"
    return StreamingResponse(body(), status_code=code, media_type="video/mp4", headers=headers)


@router.get("/videos/{video_id}/stream")
def video_stream(
    video_id: int, range: str | None = Header(None), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return _range_response(_real_path(_video_for(db, video_id, user)), range)


class SnapshotIn(BaseModel):
    t: float


@router.post("/frames/{frame_id}/snapshot")
def snapshot(frame_id: int, body: SnapshotIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Guarda como imagen nueva el fotograma del video cuyo PTS es `t` (justo después de la imagen de origen).

    Idempotente: si ese fotograma ya es una imagen del proyecto no se duplica.
    """
    src = db.scalar(select(Frame).where(Frame.id == frame_id).options(selectinload(Frame.video)))
    role = role_in(db, user, src.project_id) if src else None
    if not src or not role:
        raise HTTPException(404, "Imagen no encontrada")
    if role not in WRITE_ROLES:
        raise HTTPException(403, "Tu rol no permite modificar este proyecto")
    v = src.video
    path = _real_path(v)
    pts = _pts(db, v)
    idx = vid.nearest_frame(pts, body.t)
    if idx is None:
        raise HTTPException(400, "t no coincide con ningún fotograma del video")
    existing = db.scalar(select(Frame).where(Frame.video_id == v.id, Frame.frame_index == idx))
    if existing:
        return {"existing": True, "id": existing.id, "position": existing.position}
    try:
        usage.check_quota(db, db.get(Project, src.project_id))
    except ValueError as e:
        raise HTTPException(507, str(e)) from None
    with _LOCK:
        bgr = vid.extract_exact_frame(path, pts, idx)
    if bgr is None:
        raise HTTPException(500, "No se pudo extraer el fotograma")
    h, w = bgr.shape[:2]
    rel = f"{v.name}/f{idx:06d}.jpg"
    out = Path(settings.media_root) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    g = cv2.cvtColor(cv2.resize(bgr, (480, round(h * 480 / w))), cv2.COLOR_BGR2GRAY)
    meta = {
        "origin": "reproductor",
        "t_src": "pts",
        "sharp": round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1),
        "bright": round(float(g.mean()), 1),
    }
    # justo después de la foto de origen y de las instantáneas manuales que ya tuviera a continuación
    pos = src.position + 1
    nxt = db.scalars(
        select(Frame).where(Frame.project_id == src.project_id, Frame.position >= pos).order_by(Frame.position)
    ).all()
    for f in nxt:
        if f.video_id == v.id and f.stratum == "manual" and f.position == pos:
            pos += 1
        else:
            break
    db.execute(
        update(Frame)
        .where(Frame.project_id == src.project_id, Frame.position >= pos)
        .values(position=Frame.position + 1)
    )
    fr = Frame(
        project_id=src.project_id,
        video_id=v.id,
        frame_index=idx,
        t=round(pts[idx], 3),
        image_path=rel,
        width=w,
        height=h,
        stratum="manual",
        position=pos,
        meta=meta,
    )
    prop = proposers.load(db.get(Project, src.project_id).config)
    if prop:
        fr.proposals = [
            Proposal(
                x=b["bbox"][0],
                y=b["bbox"][1],
                w=b["bbox"][2],
                h=b["bbox"][3],
                sources=b.get("sources", []),
                score=b.get("score"),
            )
            for b in prop(bgr)
        ]
    db.add(fr)
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=src.project_id,
            action="frame.snapshot",
            target=f"video:{v.id}",
            detail={"frame_index": idx, "t": fr.t},
        )
    )
    db.commit()
    return {"existing": False, "id": fr.id, "position": fr.position}


@router.get("/frames/{frame_id}/thumb")
def frame_thumb(
    frame_id: int, h: int = Query(168, ge=32, le=480), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    f = db.get(Frame, frame_id)
    if not f or not role_in(db, user, f.project_id):
        raise HTTPException(404, "Imagen no encontrada")
    root = Path(settings.media_root).resolve()
    src = (root / f.image_path).resolve()
    if root not in src.parents or not src.is_file():
        raise HTTPException(404, "Imagen no disponible")
    tp = root / ".thumbs" / f"{h}" / Path(f.image_path).with_suffix(".jpg")
    if not tp.exists():
        im = cv2.imread(str(src))
        im = cv2.resize(im, (max(1, round(im.shape[1] * h / im.shape[0])), h), interpolation=cv2.INTER_AREA)
        tp.parent.mkdir(parents=True, exist_ok=True)
        tmp = tp.with_name(f"{tp.stem}.{threading.get_ident()}.tmp.jpg")
        cv2.imwrite(str(tmp), im, [cv2.IMWRITE_JPEG_QUALITY, 80])
        os.replace(tmp, tp)
    return FileResponse(tp, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
