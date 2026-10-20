"""Importa los JSON de la herramienta anterior (frames.json + labels.json).

Solo LEE los archivos y es idempotente: se puede repetir sin duplicar datos.
"""

import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Annotation, AnnotationBox, Frame, Membership, Project, Proposal, User, Video

DEFAULT_CONFIG = {
    "classes": [
        {"id": "louse", "name": "Piojo", "key": "l", "digit": "1", "color": "#3fb950"},
        {"id": "fly", "name": "Mosca", "key": "m", "digit": "2", "color": "#b388ff"},
        {"id": "not_louse", "name": "Ni piojo ni mosca", "key": "n", "color": "#f85149", "negative": True},
    ],
    "box_sources": {
        "motion": {"short": "mov", "name": "Movimiento"},
        "blob": {"short": "mancha", "name": "Mancha (apariencia)"},
        "model": {"short": "modelo", "name": "Modelo"},
        "manual": {"short": "manual", "name": "Dibujada a mano"},
    },
}
FRAME_META_KEYS = ("t_src", "t_mse", "sharp", "bright", "t_unc")


def ensure_project(db: Session, slug: str, name: str, config: dict | None = None) -> Project:
    p = db.scalar(select(Project).where(Project.slug == slug))
    if p is None:
        p = Project(slug=slug, name=name, config=config or DEFAULT_CONFIG)
        db.add(p)
        db.flush()
    return p


def ensure_user(db: Session, username: str, password_hash: str | None = None, **kw) -> User:
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        u = User(username=username, password_hash=password_hash, **kw)
        db.add(u)
        db.flush()
    return u


def ensure_member(db: Session, user: User, project: Project, role: str) -> None:
    m = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.project_id == project.id))
    if m is None:
        db.add(Membership(user_id=user.id, project_id=project.id, role=role))
    else:
        m.role = role


def _box(b: dict, proposal_id: int | None, deleted: bool) -> AnnotationBox:
    x, y, w, h = b["bbox"]
    return AnnotationBox(
        proposal_id=proposal_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=b.get("label"),
        manual=bool(b.get("manual")),
        orig_bbox=b.get("bbox0"),
        sources=b.get("sources") or [],
        score=b.get("score"),
        deleted=deleted,
    )


def import_legacy(
    db: Session,
    legacy_dir: Path,
    project_slug: str = "piojos",
    project_name: str = "Piojos en lana",
    videos_dir: Path | None = None,
    author: str = "legacy",
) -> dict:
    legacy_dir = Path(legacy_dir)
    frames = json.loads((legacy_dir / "frames.json").read_text())
    labels = json.loads((legacy_dir / "labels.json").read_text())
    project = ensure_project(db, project_slug, project_name)
    user = ensure_user(db, author, display_name="Etiquetas importadas")
    ensure_member(db, user, project, "labeler")

    videos: dict[str, Video] = {v.name: v for v in db.scalars(select(Video).where(Video.project_id == project.id))}
    stats = {"frames": 0, "proposals": 0, "annotations": 0, "boxes": 0, "deleted": 0, "reviewed": 0}
    for pos, fr in enumerate(frames):
        v = videos.get(fr["video"])
        if v is None:
            vpath = None
            if videos_dir and (Path(videos_dir) / f"{fr['video']}.mp4").exists():
                vpath = os.path.realpath(Path(videos_dir) / f"{fr['video']}.mp4")  # el original, no el enlace
            v = Video(project_id=project.id, name=fr["video"], path=vpath, meta={})
            db.add(v)
            db.flush()
            videos[v.name] = v
        f = db.scalar(select(Frame).where(Frame.video_id == v.id, Frame.frame_index == fr["frame"]))
        if f is None:
            f = Frame(
                project_id=project.id,
                video_id=v.id,
                frame_index=fr["frame"],
                image_path=fr["image"],
                width=fr["w"],
                height=fr["h"],
            )
            db.add(f)
        f.t, f.stratum, f.position, f.image_path = fr.get("t"), fr.get("stratum"), pos, fr["image"]
        f.meta = {k: fr[k] for k in FRAME_META_KEYS if k in fr}
        db.flush()
        if not f.proposals:
            for b in fr["boxes"]:
                x, y, w, h = b["bbox"]
                f.proposals.append(Proposal(x=x, y=y, w=w, h=h, sources=b.get("sources") or [], score=b.get("score")))
            db.flush()
            stats["proposals"] += len(f.proposals)
        stats["frames"] += 1

        lab = labels.get(f"{fr['video']}:{fr['frame']}")
        if lab is None:
            continue
        free = {}  # propuestas aún sin usar, por caja (hay cajas idénticas repetidas)
        for p in f.proposals:
            free.setdefault((p.x, p.y, p.w, p.h), []).append(p.id)

        def match(b, free=free):
            ids = free.get(tuple(b.get("bbox0") or b["bbox"]))
            return ids.pop(0) if ids else None

        ann = db.scalar(select(Annotation).where(Annotation.frame_id == f.id, Annotation.user_id == user.id))
        if ann is None:
            ann = Annotation(frame_id=f.id, user_id=user.id)
            db.add(ann)
        ann.reviewed = bool(lab.get("reviewed"))
        ann.boxes = [_box(b, None if b.get("manual") else match(b), False) for b in lab.get("boxes", [])] + [
            _box(b, None if b.get("manual") else match(b), True) for b in lab.get("deleted", [])
        ]
        stats["annotations"] += 1
        stats["reviewed"] += ann.reviewed
        stats["boxes"] += len(lab.get("boxes", []))
        stats["deleted"] += len(lab.get("deleted", []))
    db.commit()
    return stats


def copy_annotations(db: Session, source: str, target: str, project_slug: str) -> dict:
    """Copia las anotaciones de un usuario a otro (p. ej. de «legacy» a quien seguirá etiquetando).

    No pisa lo que el destino ya tenga en una imagen. La copia deja intacto el origen.
    """
    src = db.scalar(select(User).where(User.username == source))
    dst = db.scalar(select(User).where(User.username == target))
    project = db.scalar(select(Project).where(Project.slug == project_slug))
    if not (src and dst and project):
        raise ValueError("usuario o proyecto inexistente")
    done = {a.frame_id for a in db.scalars(select(Annotation).where(Annotation.user_id == dst.id))}
    n = skipped = 0
    for a in db.scalars(
        select(Annotation)
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Annotation.user_id == src.id, Frame.project_id == project.id)
    ):
        if a.frame_id in done:
            skipped += 1
            continue
        na = Annotation(frame_id=a.frame_id, user_id=dst.id, reviewed=a.reviewed)
        na.boxes = [
            AnnotationBox(
                proposal_id=b.proposal_id,
                x=b.x,
                y=b.y,
                w=b.w,
                h=b.h,
                label=b.label,
                manual=b.manual,
                orig_bbox=b.orig_bbox,
                sources=b.sources,
                score=b.score,
                deleted=b.deleted,
            )
            for b in a.boxes
        ]
        db.add(na)
        n += 1
    db.commit()
    return {"copied": n, "skipped": skipped}
