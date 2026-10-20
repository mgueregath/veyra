"""Uso de disco de un proyecto y cuota."""

import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Frame, Project, Video


def dir_size(p: Path) -> int:
    if not p.is_dir():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def project_usage(db: Session, project: Project) -> dict:
    media = Path(settings.media_root).resolve()
    images = 0
    n_img = 0
    for (rel,) in db.execute(select(Frame.image_path).where(Frame.project_id == project.id)):
        try:
            images += os.stat(media / rel).st_size
            n_img += 1
        except OSError:
            pass
    videos = 0
    seen = set()
    for v in db.scalars(select(Video).where(Video.project_id == project.id)):
        if v.path and v.path not in seen and os.path.isfile(v.path):
            seen.add(v.path)
            videos += os.path.getsize(v.path)
    slug = project.slug
    other = {
        "models": dir_size(Path(settings.models_root) / slug),
        "exports": dir_size(Path(settings.export_root) / slug),
    }
    quota_gb = (project.config or {}).get("quota_gb")
    # la cuota cuenta lo que ESTA plataforma escribe (imágenes, modelos, exportaciones); los videos son externos
    owned = images + sum(other.values())
    return {
        "images": {"files": n_img, "bytes": images},
        "videos": {"files": len(seen), "bytes": videos, "note": "originales externos: no cuentan en la cuota"},
        **{k: {"bytes": v} for k, v in other.items()},
        "owned_bytes": owned,
        "quota_bytes": int(quota_gb * 1e9) if quota_gb else None,
        "over_quota": bool(quota_gb) and owned > quota_gb * 1e9,
    }


def check_quota(db: Session, project: Project) -> None:
    """Lanza ValueError si el proyecto ya superó su cuota (se usa antes de escribir imágenes nuevas)."""
    u = project_usage(db, project)
    if u["over_quota"]:
        raise ValueError(
            f"El proyecto superó su cuota de disco ({u['quota_bytes'] / 1e9:.1f} GB): libere espacio o amplíela"
        )
