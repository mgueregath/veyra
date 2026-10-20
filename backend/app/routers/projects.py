from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, role_in
from ..models import Annotation, AnnotationBox, Assignment, Frame, Membership, Project, User, Video
from ..schemas import ProjectOut

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Project).order_by(Project.name)
    if not user.is_superuser:
        q = q.join(Membership, Membership.project_id == Project.id).where(Membership.user_id == user.id)
    out = []
    for p in db.scalars(q):
        n = db.scalar(select(func.count()).select_from(Frame).where(Frame.project_id == p.id))
        r = db.scalar(
            select(func.count())
            .select_from(Annotation)
            .join(Frame, Frame.id == Annotation.frame_id)
            .where(Frame.project_id == p.id, Annotation.user_id == user.id, Annotation.reviewed)
        )
        out.append(
            ProjectOut(
                id=p.id,
                slug=p.slug,
                name=p.name,
                role=role_in(db, user, p.id),
                config=p.config,
                n_frames=n,
                n_reviewed=r,
            )
        )
    return out


@router.get("/projects/{project_id}/stats")
def project_stats(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Resumen de TODO el proyecto para el usuario actual: por clase, a mano, sin etiquetar y borrados."""
    if not role_in(db, user, project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    n = db.scalar(select(func.count()).select_from(Frame).where(Frame.project_id == project_id))
    base = (
        select(AnnotationBox)
        .join(Annotation, Annotation.id == AnnotationBox.annotation_id)
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Frame.project_id == project_id, Annotation.user_id == user.id)
    )
    by_label: dict[str, int] = {}
    unlabeled = manual = deleted = 0
    for b in db.scalars(base):
        if b.deleted:
            deleted += 1
            continue
        manual += bool(b.manual)
        if b.label is None:
            unlabeled += 1
        else:
            by_label[b.label] = by_label.get(b.label, 0) + 1
    reviewed = db.scalar(
        select(func.count())
        .select_from(Annotation)
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Frame.project_id == project_id, Annotation.user_id == user.id, Annotation.reviewed)
    )
    assigned = db.scalar(
        select(func.count())
        .select_from(Assignment)
        .join(Frame, Frame.id == Assignment.frame_id)
        .where(Frame.project_id == project_id, Assignment.user_id == user.id)
    )
    assigned_done = db.scalar(
        select(func.count())
        .select_from(Assignment)
        .join(Frame, Frame.id == Assignment.frame_id)
        .join(Annotation, (Annotation.frame_id == Assignment.frame_id) & (Annotation.user_id == Assignment.user_id))
        .where(Frame.project_id == project_id, Assignment.user_id == user.id, Annotation.reviewed)
    )
    return {
        "frames": n,
        "reviewed": reviewed,
        "assigned": assigned,
        "assigned_done": assigned_done,
        "by_label": by_label,
        "manual": manual,
        "unlabeled": unlabeled,
        "deleted": deleted,
    }


@router.get("/projects/{project_id}/members")
def members(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Quién trabaja en el proyecto y con qué rol (para repartir trabajo y revisar)."""
    if role_in(db, user, project_id) not in ("admin", "reviewer"):
        if not db.get(Project, project_id) or not role_in(db, user, project_id):
            raise HTTPException(404, "Proyecto no encontrado")
        raise HTTPException(403, "Tu rol no permite ver el equipo")
    rows = db.execute(
        select(User.username, User.display_name, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.project_id == project_id, User.is_active)
        .order_by(User.username)
    ).all()
    return [{"username": u, "display_name": d, "role": r} for u, d, r in rows]


@router.get("/projects/{project_id}/videos")
def project_videos(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Videos del proyecto y si su archivo original está disponible (para elegir una fuente en vivo)."""
    import os

    if not db.get(Project, project_id) or not role_in(db, user, project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    rows = db.scalars(select(Video).where(Video.project_id == project_id).order_by(Video.name)).all()
    return [{"id": v.id, "name": v.name, "has_file": bool(v.path) and os.path.isfile(v.path)} for v in rows]
