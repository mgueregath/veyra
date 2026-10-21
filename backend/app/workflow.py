"""Flujo de trabajo: estado de cada imagen, etiquetas finales, bloqueos de la cola."""

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from . import consensus
from .models import Annotation, FrameLock, Project, Review

LOCK_TTL = 180  # segundos; el visor renueva el bloqueo mientras la imagen está abierta

# pending: nadie la revisó · single: una persona · agreed: varias y coinciden
# disagreement: varias y NO coinciden (necesita revisor) · final: el revisor decidió
STATUSES = ("pending", "single", "agreed", "disagreement", "final")


# review: cada persona etiqueta y un revisor fija la versión final
# collab: todos editan las mismas etiquetas y la última modificación es la final
MODES = ("review", "collab")


def project_mode(project: Project) -> str:
    mode = (project.config or {}).get("mode", "review")
    return mode if mode in MODES else "review"


def latest_annotations(
    db: Session, project: Project, frame_ids: list[int] | None = None, boxes: bool = False
) -> dict[int, Annotation]:
    """Modalidad colaborativa: la versión vigente de cada imagen es la última anotación modificada, de quien sea."""
    from .models import Frame

    q = (
        select(Annotation)
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Frame.project_id == project.id, Frame.deleted_at.is_(None))
        .order_by(Annotation.frame_id, Annotation.updated_at, Annotation.id)
    )
    if boxes:
        q = q.options(selectinload(Annotation.boxes))
    if frame_ids is not None:
        q = q.where(Annotation.frame_id.in_(frame_ids))
    return {a.frame_id: a for a in db.scalars(q)}  # al ir ordenadas, la última de cada imagen es la que queda


def class_sets(project: Project) -> tuple[list[str], set[str]]:
    """(clases positivas en orden, ids de clases negativas)."""
    classes = (project.config or {}).get("classes", [])
    return [c["id"] for c in classes if not c.get("negative")], {c["id"] for c in classes if c.get("negative")}


def discarded(ann: Annotation) -> list[dict]:
    """Cajas borradas por la persona (borrado suave): detecciones del modelo o del detector que se rechazaron."""
    return [{"bbox": [b.x, b.y, b.w, b.h], "label": b.label} for b in ann.boxes if b.deleted]


def labeled(ann: Annotation) -> list[consensus.LabeledBox]:
    """Cajas vivas con etiqueta (las borradas y las sin etiquetar no cuentan)."""
    return [((b.x, b.y, b.w, b.h), b.label) for b in ann.boxes if not b.deleted and b.label is not None]


@dataclass
class FrameState:
    status: str = "pending"
    n_reviewed: int = 0
    reviewers: list[int] = field(default_factory=list)  # usuarios con anotación revisada
    final_user_id: int | None = None


def compute_states(db: Session, project: Project, frame_ids: list[int] | None = None) -> dict[int, FrameState]:
    """Estado de las imágenes que tienen alguna anotación revisada o una revisión (el resto es `pending`)."""
    from .models import Frame

    if project_mode(project) == "collab":
        # hay una sola versión vigente: la más reciente. Si está marcada como revisada, la imagen es final.
        return {
            fid: FrameState(status="final", n_reviewed=1, reviewers=[a.user_id], final_user_id=a.user_id)
            for fid, a in latest_annotations(db, project, frame_ids).items()
            if a.reviewed
        }
    _, negative = class_sets(project)
    q = (
        select(Annotation)
        .join(Frame, Frame.id == Annotation.frame_id)
        .options(selectinload(Annotation.boxes))
        .where(Frame.project_id == project.id, Frame.deleted_at.is_(None), Annotation.reviewed)
        .order_by(Annotation.frame_id, Annotation.id)
    )
    if frame_ids is not None:
        q = q.where(Annotation.frame_id.in_(frame_ids))
    by_frame: dict[int, list[Annotation]] = {}
    for a in db.scalars(q):
        by_frame.setdefault(a.frame_id, []).append(a)
    rq = (
        select(Review)
        .join(Frame, Frame.id == Review.frame_id)
        .where(Frame.project_id == project.id, Frame.deleted_at.is_(None))
    )
    if frame_ids is not None:
        rq = rq.where(Review.frame_id.in_(frame_ids))
    reviews = {r.frame_id: r for r in db.scalars(rq)}
    out: dict[int, FrameState] = {}
    for fid in set(by_frame) | set(reviews):
        anns = by_frame.get(fid, [])
        st = FrameState(n_reviewed=len(anns), reviewers=[a.user_id for a in anns])
        if fid in reviews:
            st.status, st.final_user_id = "final", reviews[fid].final_user_id
        elif len(anns) == 1:
            st.status, st.final_user_id = "single", anns[0].user_id
        elif len(anns) >= 2:
            first = labeled(anns[0])
            ok = all(consensus.agree(first, labeled(a), negative) for a in anns[1:])
            st.status = "agreed" if ok else "disagreement"
            st.final_user_id = anns[0].user_id if ok else None
        out[fid] = st
    return out


def final_annotation(db: Session, frame_id: int, state: FrameState) -> Annotation | None:
    if state.final_user_id is None:
        return None
    return db.scalar(
        select(Annotation)
        .options(selectinload(Annotation.boxes))
        .where(Annotation.frame_id == frame_id, Annotation.user_id == state.final_user_id)
    )


def final_boxes(db: Session, frame_id: int, state: FrameState) -> list[dict] | None:
    """Etiquetas finales de la imagen según su estado, o None si todavía no hay (pending o desacuerdo)."""
    ann = final_annotation(db, frame_id, state)
    if ann is None:
        return None
    return [{"bbox": list(bb), "label": lb} for bb, lb in labeled(ann)]


# ---------- bloqueos ----------
def acquire_lock(db: Session, frame_id: int, user_id: int, ttl: int = LOCK_TTL) -> bool:
    """Toma (o renueva) el bloqueo de forma ATÓMICA: solo se logra si está libre, caducado o ya es del usuario."""
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl)
    stmt = (
        pg_insert(FrameLock)
        .values(frame_id=frame_id, user_id=user_id, until=until)
        .on_conflict_do_update(
            index_elements=[FrameLock.frame_id],
            set_={"user_id": user_id, "until": until},
            where=or_(FrameLock.user_id == user_id, FrameLock.until < func.now()),
        )
        .returning(FrameLock.frame_id)
    )
    ok = db.execute(stmt).first() is not None
    db.commit()
    return ok


def release_lock(db: Session, frame_id: int, user_id: int) -> None:
    lock = db.get(FrameLock, frame_id)
    if lock and lock.user_id == user_id:
        db.delete(lock)
        db.commit()


def active_lock_holder(db: Session, frame_id: int, exclude_user: int | None = None) -> int | None:
    lock = db.get(FrameLock, frame_id)
    if lock and lock.until > dt.datetime.now(dt.timezone.utc) and lock.user_id != exclude_user:
        return lock.user_id
    return None
