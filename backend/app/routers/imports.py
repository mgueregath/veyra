import bisect
import datetime as dt
import io
import os
import shutil
import threading
import zipfile
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import imports as imp
from .. import usage
from .. import video as vid
from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import current_user, role_in
from ..models import Annotation, AnnotationBox, AuditLog, Frame, Project, Run, User, Video
from .videos import _LOCK, _real_path, _video_for

router = APIRouter(prefix="/api", tags=["import"])
ROLES = {"admin", "reviewer"}
MAX_ENTRIES = 5000
MAX_UNZIPPED = 4 * 1024**3
MAX_IMAGE_PIXELS = 100_000_000
MAX_SAMPLES = 20000  # corre en segundo plano: no depende del tiempo de la petición


def _project(db: Session, user: User, pid: int) -> Project:
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role not in ROLES:
        raise HTTPException(403, "Solo administración y revisión pueden cargar imágenes y videos")
    return p


def _uniq_video_name(db: Session, pid: int, base: str) -> str:
    name, n = base, 1
    while db.scalar(select(Video.id).where(Video.project_id == pid, Video.name == name)):
        n += 1
        name = f"{base}-{n}"
    return name


async def _expand(files: list[UploadFile]) -> dict[str, bytes]:
    """Todos los archivos por nombre (los .zip se abren; cada nombre conserva solo su parte final)."""
    out: dict[str, bytes] = {}
    for f in files:
        data = await f.read()
        name = f.filename or "archivo"
        if name.lower().endswith(".zip"):
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                raise HTTPException(422, f"«{name}» no es un zip válido") from None
            infos = [i for i in z.infolist() if not i.is_dir() and not PurePosixPath(i.filename).name.startswith(".")]
            if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
                raise HTTPException(422, f"«{name}» es demasiado grande (máximo {MAX_ENTRIES} archivos y 4 GB)")
            for i in infos:
                out.setdefault(PurePosixPath(i.filename).name, z.read(i))
        else:
            out.setdefault(PurePosixPath(name.replace("\\", "/")).name, data)
        if len(out) > MAX_ENTRIES:
            raise HTTPException(422, f"Demasiados archivos (máximo {MAX_ENTRIES} por carga)")
    return out


@router.post("/projects/{project_id}/import/images")
async def import_images(
    project_id: int,
    files: list[UploadFile] = File(...),
    reviewed: bool = Form(False),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Agrega imágenes al proyecto, con o sin etiquetas (YOLO o COCO), que quedan como trabajo de quien las carga."""
    p = _project(db, user, project_id)
    allf = await _expand(files)
    images = {n: d for n, d in allf.items() if PurePosixPath(n).suffix.lower() in imp.IMAGE_EXT}
    if not images:
        raise HTTPException(422, "No hay imágenes (se admiten jpg, png, bmp y webp, sueltas o dentro de un zip)")
    try:
        usage.check_quota(db, p)
        names = imp.parse_names(allf)
        classes = (p.config or {}).get("classes", [])
        resolve, warnings = imp.class_resolver(classes, names)
        coco: dict[str, list[dict]] = {}
        for n, d in allf.items():
            if n.lower().endswith(".json"):
                by_name = {str(c.get("name", "")).lower(): c["id"] for c in classes} | {
                    str(c["id"]).lower(): c["id"] for c in classes
                }

                def by_label(nm: str, _m=by_name):
                    got = _m.get(nm.lower())
                    if got is None and nm:
                        warnings.add(f"la clase «{nm}» no existe en el proyecto: sus cajas quedan sin etiquetar")
                    return got

                c, _ = imp.parse_coco(d, by_label)
                coco.update(c)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    v = Video(project_id=p.id, name=_uniq_video_name(db, p.id, f"subidas-{stamp}"), path=None, meta={"origin": "carga"})
    db.add(v)
    db.flush()
    rel_dir = f"uploads/{p.slug}/{v.name}"
    out_dir = Path(settings.media_root) / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    pos = (db.scalar(select(func.max(Frame.position)).where(Frame.project_id == p.id)) or 0) + 1
    res = {"images": 0, "with_labels": 0, "boxes": 0, "skipped": []}
    try:
        for idx, (name, data) in enumerate(sorted(images.items()), 1):
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is None or img.shape[0] * img.shape[1] > MAX_IMAGE_PIXELS:
                res["skipped"].append(f"{name}: no es una imagen legible")
                continue
            h, w = img.shape[:2]
            stem = PurePosixPath(name).stem
            if name in coco:
                boxes = coco[name]
            else:
                txt = allf.get(stem + ".txt")
                boxes = imp.parse_yolo(txt.decode("utf-8-sig", "replace"), w, h, resolve) if txt is not None else None
            ext = PurePosixPath(name).suffix.lower()
            fname = f"{idx:05d}_{imp.safe_name(stem)}{ext}"
            (out_dir / fname).write_bytes(data)
            fr = Frame(
                project_id=p.id,
                video_id=v.id,
                frame_index=idx,
                t=None,
                image_path=f"{rel_dir}/{fname}",
                width=w,
                height=h,
                stratum="carga",
                position=pos,
                meta={"origin": "carga", "original_name": name},
            )
            pos += 1
            db.add(fr)
            res["images"] += 1
            if boxes is not None:
                db.flush()
                ann = Annotation(frame_id=fr.id, user_id=user.id, reviewed=bool(reviewed))
                ann.boxes = [
                    AnnotationBox(
                        x=b["bbox"][0],
                        y=b["bbox"][1],
                        w=b["bbox"][2],
                        h=b["bbox"][3],
                        label=b["label"],
                        manual=True,
                        sources=["import"],
                        score=None,
                    )
                    for b in boxes
                ]
                db.add(ann)
                res["with_labels"] += 1
                res["boxes"] += len(boxes)
        if not res["images"]:
            raise HTTPException(422, "Ninguna imagen se pudo leer: " + "; ".join(res["skipped"][:3]))
        db.add(
            AuditLog(
                user_id=user.id,
                project_id=p.id,
                action="import.images",
                target=f"video:{v.id}",
                detail={k: res[k] for k in ("images", "with_labels", "boxes")},
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return {**res, "skipped": res["skipped"][:20], "warnings": sorted(warnings), "batch": v.name}


def plan_samples(pts: list[float], start: float, end: float | None, interval: float) -> list[int]:
    """Índices de los fotogramas más cercanos a start, start+interval, … hasta `end` (sin repetir)."""
    last = pts[-1] if end is None else min(end, pts[-1])
    if start > last:
        raise ValueError("el inicio queda después del final del video")
    if end is not None and end < start:
        raise ValueError("el final debe ser posterior al inicio")
    idxs: list[int] = []
    k = 0
    while start + k * interval <= last + 1e-9:
        t = start + k * interval
        i = bisect.bisect_left(pts, t)
        cands = [j for j in (i - 1, i) if 0 <= j < len(pts)]
        idx = min(cands, key=lambda j: abs(pts[j] - t))
        if not idxs or idxs[-1] != idx:
            idxs.append(idx)
        k += 1
        if k > 100_000:
            break
    return idxs


def _extract(db: Session, p: Project, v: Video, path: str, pts: list[float], idxs: list[int]) -> tuple[int, int]:
    """Agrega como imágenes los fotogramas `idxs` que aún no existan. Devuelve (nuevas, ya existentes)."""
    have = set(db.scalars(select(Frame.frame_index).where(Frame.video_id == v.id)))
    pos = (db.scalar(select(func.max(Frame.position)).where(Frame.project_id == p.id)) or 0) + 1
    (Path(settings.media_root) / v.name).mkdir(parents=True, exist_ok=True)
    made = 0
    for idx in idxs:
        if idx in have:
            continue
        with _LOCK:
            bgr = vid.extract_exact_frame(path, pts, idx)
        if bgr is None:
            continue
        h, w = bgr.shape[:2]
        rel = f"{v.name}/f{idx:06d}.jpg"
        cv2.imwrite(str(Path(settings.media_root) / rel), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        db.add(
            Frame(
                project_id=p.id,
                video_id=v.id,
                frame_index=idx,
                t=round(pts[idx], 3),
                image_path=rel,
                width=w,
                height=h,
                stratum="carga",
                position=pos,
                meta={"origin": "carga", "t_src": "pts"},
            )
        )
        pos += 1
        made += 1
    return made, len([i for i in idxs if i in have])


def _run_sampling(run_id: int, video_id: int, path: str, idxs: list[int]) -> None:
    """Extrae los fotogramas en segundo plano, de a 10, y deja el avance en la ejecución."""
    err = None
    made = 0
    try:
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            run.status = "running"
            db.commit()
            v = db.get(Video, video_id)
            p = db.get(Project, v.project_id)
            pts = v.meta["pts"]
            for i in range(0, len(idxs), 10):
                n, _ = _extract(db, p, v, path, pts, idxs[i : i + 10])
                made += n
                run = db.get(Run, run_id)
                run.metrics = {"progress": {"done": min(i + 10, len(idxs)), "total": len(idxs)}, "frames": made}
                db.commit()
    except Exception as e:  # el motivo queda en la ejecución
        err = str(e)
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        run.status = "failed" if err else "done"
        if err:
            run.log = err
        else:
            run.metrics = {"progress": {"done": len(idxs), "total": len(idxs)}, "frames": made}
        db.commit()


def _start_sampling(db: Session, p: Project, v: Video, path: str, idxs: list[int], user: User, label: str) -> Run:
    run = Run(
        project_id=p.id,
        kind="sampling",
        name=f"muestreo de «{v.name}» ({label})",
        params={"video_id": v.id, "frames": len(idxs)},
        status="queued",
        created_by=user.id,
        metrics={"progress": {"done": 0, "total": len(idxs)}, "frames": 0},
    )
    db.add(run)
    db.commit()
    threading.Thread(
        target=_run_sampling, args=(run.id, v.id, path, idxs), name=f"sampling-{run.id}", daemon=True
    ).start()
    return run


def _checked_plan(pts: list[float], start: float, end: float | None, interval: float) -> list[int]:
    try:
        idxs = plan_samples(pts, start, end, interval)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if len(idxs) > MAX_SAMPLES:
        raise HTTPException(
            422,
            f"Con ese intervalo salen {len(idxs)} imágenes (máximo {MAX_SAMPLES} por vez): amplíalo o acota el rango",
        )
    return idxs


@router.post("/projects/{project_id}/import/video")
def import_video(
    project_id: int,
    file: UploadFile = File(...),
    mode: str = Form("auto"),
    interval: float = Form(5.0),
    start: float = Form(0.0),
    end: float | None = Form(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Agrega un video: queda disponible para el reproductor, las ejecuciones y las capturas.

    mode=auto: se extrae un fotograma cada `interval` segundos entre `start` y `end` (por defecto, hasta el final).
    mode=manual: solo se crea la primera imagen; la persona toma las instantáneas reproduciendo el video.
    """
    p = _project(db, user, project_id)
    ext = PurePosixPath(file.filename or "").suffix.lower()
    if ext not in imp.VIDEO_EXT:
        raise HTTPException(422, "Formato no admitido (mp4, mov, m4v, avi, mkv o webm)")
    if mode not in ("auto", "manual"):
        raise HTTPException(422, "mode debe ser auto o manual")
    if mode == "auto" and (interval < 0.04 or start < 0 or (end is not None and end < 0)):
        raise HTTPException(
            422, "El intervalo debe ser de al menos 0,04 s y el inicio y el final no pueden ser negativos"
        )
    try:
        usage.check_quota(db, p)
    except ValueError as e:
        raise HTTPException(507, str(e)) from None
    name = _uniq_video_name(db, p.id, imp.safe_name(PurePosixPath(file.filename or "video").stem))
    vdir = (Path(settings.media_root) / "uploads" / p.slug / "videos").resolve()
    vdir.mkdir(parents=True, exist_ok=True)
    dest = vdir / f"{name}{ext}"
    frames_dir = Path(settings.media_root) / name
    frames_existed = frames_dir.exists()
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        try:
            info = vid.video_info(str(dest))
            pts = vid.video_pts(str(dest))
        except Exception:
            raise HTTPException(422, "El archivo no es un video legible") from None
        if not pts:
            raise HTTPException(422, "El video no tiene fotogramas")
        idxs = _checked_plan(pts, start, end, interval) if mode == "auto" else [0]
        v = Video(project_id=p.id, name=name, path=str(dest), meta={**info, "pts": pts, "origin": "carga"})
        db.add(v)
        db.flush()
        made, _ = _extract(db, p, v, str(dest), pts, idxs[:1])  # la primera ya, para que el video se pueda abrir
        if not made:
            raise HTTPException(422, "No se pudo extraer ningún fotograma del video")
        db.add(
            AuditLog(
                user_id=user.id,
                project_id=p.id,
                action="import.video",
                target=f"video:{v.id}",
                detail={"name": name, "frames": made, "mode": mode, "duration": info["dur"]},
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        dest.unlink(missing_ok=True)
        if not frames_existed:
            shutil.rmtree(frames_dir, ignore_errors=True)
        raise
    first = db.scalar(select(Frame.id).where(Frame.video_id == v.id).order_by(Frame.frame_index))
    run = _start_sampling(db, p, v, str(dest), idxs[1:], user, "automático") if len(idxs) > 1 else None
    return {
        "run_id": run.id if run else None,
        "planned": len(idxs),
        "video_id": v.id,
        "name": name,
        "frames": made,
        "duration": info["dur"],
        "fps": info["fps"],
        "first_frame_id": first,
        "mode": mode,
    }


class SampleIn(BaseModel):
    start: float = Field(0.0, ge=0)
    end: float | None = Field(None, ge=0)
    interval: float = Field(5.0, ge=0.04)


@router.post("/videos/{video_id}/sample")
def sample_video(video_id: int, body: SampleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Muestreo automático de un video ya cargado: un fotograma cada `interval` s entre `start` y `end`."""
    v = _video_for(db, video_id, user)
    if role_in(db, user, v.project_id) not in ROLES:
        raise HTTPException(403, "Solo administración y revisión pueden cargar imágenes y videos")
    p = db.get(Project, v.project_id)
    path = _real_path(v)
    if "pts" not in (v.meta or {}):
        v.meta = {**(v.meta or {}), "pts": vid.video_pts(path)}
    pts = v.meta["pts"]
    try:
        usage.check_quota(db, p)
    except ValueError as e:
        raise HTTPException(507, str(e)) from None
    idxs = _checked_plan(pts, body.start, body.end, body.interval)
    have = set(db.scalars(select(Frame.frame_index).where(Frame.video_id == v.id)))
    todo = [i for i in idxs if i not in have]
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="video.sample",
            target=f"video:{v.id}",
            detail={"frames": len(todo), **body.model_dump()},
        )
    )
    db.commit()
    run = _start_sampling(db, p, v, path, todo, user, "automático") if todo else None
    return {"video_id": v.id, "run_id": run.id if run else None, "frames": len(todo), "already": len(idxs) - len(todo)}


def _member(db: Session, user: User, pid: int) -> Project:
    p = db.get(Project, pid)
    if not p or not role_in(db, user, pid):
        raise HTTPException(404, "Proyecto no encontrado")
    return p


def _size(path: str | Path) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


@router.get("/projects/{project_id}/sources/videos")
def source_videos(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Videos del proyecto con archivo original: de ellos salen las imágenes por muestreo."""
    p = _member(db, user, project_id)
    counts = dict(
        db.execute(
            select(Frame.video_id, func.count(Frame.id))
            .where(Frame.project_id == p.id, Frame.deleted_at.is_(None))
            .group_by(Frame.video_id)
        ).all()
    )
    firsts = dict(
        db.execute(
            select(Frame.video_id, func.min(Frame.id)).where(Frame.project_id == p.id).group_by(Frame.video_id)
        ).all()
    )
    out = []
    for v in db.scalars(select(Video).where(Video.project_id == p.id, Video.path.is_not(None)).order_by(Video.name)):
        m = v.meta or {}
        out.append(
            {
                "id": v.id,
                "name": v.name,
                "has_file": os.path.isfile(v.path),
                "bytes": _size(v.path),
                "duration": m.get("dur"),
                "fps": m.get("fps"),
                "n_frames": counts.get(v.id, 0),
                "first_frame_id": firsts.get(v.id),
                "origin": "carga" if m.get("origin") == "carga" else "importado",
            }
        )
    return out


@router.get("/projects/{project_id}/sources/images")
def source_images(
    project_id: int,
    offset: int = 0,
    limit: int = 60,
    batch: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Imágenes que no salen de un video con archivo: cargadas, capturas en vivo o de videos sin original."""
    p = _member(db, user, project_id)
    limit = max(1, min(limit, 200))
    base = (
        select(Frame, Video)
        .join(Video, Video.id == Frame.video_id)
        .where(Frame.project_id == p.id, Frame.deleted_at.is_(None), Video.path.is_(None))
    )
    batches = db.execute(
        select(Video.name, func.count(Frame.id), Video.meta)
        .join(Frame, Frame.video_id == Video.id)
        .where(Frame.project_id == p.id, Frame.deleted_at.is_(None), Video.path.is_(None))
        .group_by(Video.id)
        .order_by(Video.id.desc())
    ).all()

    def kind(meta: dict | None, name: str) -> str:
        m = meta or {}
        return "en vivo" if "live_session" in m else "carga" if m.get("origin") == "carga" else "sin video original"

    q = base.where(Video.name == batch) if batch else base
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    media = Path(settings.media_root)
    items = []
    for f, v in db.execute(q.order_by(Frame.position).offset(max(0, offset)).limit(limit)).all():
        items.append(
            {
                "id": f.id,
                "name": (f.meta or {}).get("original_name") or Path(f.image_path).name,
                "batch": v.name,
                "origin": kind(v.meta, v.name),
                "width": f.width,
                "height": f.height,
                "bytes": _size(media / f.image_path),
                "position": f.position,
            }
        )
    return {
        "total": total,
        "batches": [{"name": n, "count": c, "origin": kind(m, n)} for n, c, m in batches],
        "items": items,
    }
