import copy
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, role_in
from ..legacy_import import DEFAULT_CONFIG
from ..models import (
    Annotation,
    AnnotationBox,
    Assignment,
    AuditLog,
    Frame,
    Membership,
    Project,
    User,
    Video,
)
from ..schemas import ClassIn, ProjectCreate, ProjectOut, ProjectUpdate
from ..workflow import compute_states, project_mode

router = APIRouter(prefix="/api", tags=["projects"])


def _out(db: Session, user: User, p: Project) -> ProjectOut:
    n = db.scalar(select(func.count()).select_from(Frame).where(Frame.project_id == p.id, Frame.deleted_at.is_(None)))
    if project_mode(p) == "collab":  # colaborativa: el avance es del proyecto, no de cada persona
        r = len(compute_states(db, p))
    else:
        r = db.scalar(
            select(func.count())
            .select_from(Annotation)
            .join(Frame, Frame.id == Annotation.frame_id)
            .where(
                Frame.project_id == p.id, Frame.deleted_at.is_(None), Annotation.user_id == user.id, Annotation.reviewed
            )
        )
    cover = db.scalar(
        select(Frame.id).where(Frame.project_id == p.id, Frame.deleted_at.is_(None)).order_by(Frame.id).limit(1)
    )
    return ProjectOut(
        id=p.id,
        slug=p.slug,
        name=p.name,
        role=role_in(db, user, p.id),
        config=p.config,
        n_frames=n,
        n_reviewed=r,
        cover_frame_id=cover,
    )


def _slug(db: Session, name: str) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:48] or "proyecto"
    slug, i = base, 2
    while db.scalar(select(Project.id).where(Project.slug == slug)):
        slug, i = f"{base}-{i}", i + 1
    return slug


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Crea un proyecto con las clases por defecto; quien lo crea queda como su administrador."""
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "El nombre no puede estar vacío")
    if db.scalar(select(Project.id).where(func.lower(Project.name) == name.lower())):
        raise HTTPException(409, "Ya existe un proyecto con ese nombre")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["description"] = (body.description or "").strip()
    config["mode"] = body.mode
    p = Project(slug=_slug(db, name), name=name, config=config)
    db.add(p)
    db.flush()
    db.add(Membership(user_id=user.id, project_id=p.id, role="admin"))
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="project.create", target=p.slug))
    db.commit()
    return _out(db, user, p)


def _class_id(name: str, taken: set[str]) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:24] or "clase"
    cid, i = base, 2
    while cid in taken:
        cid, i = f"{base}_{i}", i + 1
    return cid


def _merge_classes(db: Session, p: Project, new: list[ClassIn]) -> list[dict]:
    """Valida la lista de clases. Las existentes conservan su id; quitar una que ya tiene cajas se rechaza."""
    old = {c["id"]: c for c in (p.config or {}).get("classes", [])}
    if not any(not c.negative for c in new):
        raise HTTPException(422, "Debe haber al menos una clase que no sea negativa")
    names = [c.name.strip().lower() for c in new]
    if len(set(names)) != len(names) or "" in names:
        raise HTTPException(422, "Los nombres de las etiquetas no pueden repetirse ni estar vacíos")
    shortcuts = [x for c in new for x in (c.digit, c.key and c.key.lower()) if x]
    if len(set(shortcuts)) != len(shortcuts):
        raise HTTPException(422, "Los atajos de las etiquetas no pueden repetirse")
    kept = {c.id for c in new if c.id}
    if kept - set(old):
        raise HTTPException(422, "Identificador de etiqueta desconocido")
    gone = set(old) - kept
    if gone:
        used = db.scalars(
            select(AnnotationBox.label)
            .join(Annotation, Annotation.id == AnnotationBox.annotation_id)
            .join(Frame, Frame.id == Annotation.frame_id)
            .where(Frame.project_id == p.id, AnnotationBox.label.in_(gone))
            .distinct()
        ).all()
        if used:
            raise HTTPException(409, f"No se puede quitar la etiqueta «{old[used[0]]['name']}»: ya hay cajas con ella")
    taken = set(old) | kept
    out = []
    for c in new:
        cid = c.id or _class_id(c.name, taken)
        taken.add(cid)
        d = {"id": cid, "name": c.name.strip(), "color": c.color.lower()}
        if c.key:
            d["key"] = c.key.lower()
        if c.digit:
            d["digit"] = c.digit
        if c.negative:
            d["negative"] = True
        out.append(d)
    return out


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, body: ProjectUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = db.get(Project, project_id)
    role = role_in(db, user, project_id) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role != "admin":
        raise HTTPException(403, "Solo un administrador del proyecto puede hacer esto")
    config = dict(p.config or {})
    detail: dict = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "El nombre no puede estar vacío")
        if db.scalar(select(Project.id).where(func.lower(Project.name) == name.lower(), Project.id != p.id)):
            raise HTTPException(409, "Ya existe un proyecto con ese nombre")
        p.name = name
        detail["name"] = name
    if body.description is not None:
        config["description"] = body.description.strip()
        detail["description"] = True
    if body.mode is not None and body.mode != config.get("mode", "review"):
        config["mode"] = body.mode
        detail["mode"] = body.mode
    if body.classes is not None:
        config["classes"] = _merge_classes(db, p, body.classes)
        detail["classes"] = [c["id"] for c in config["classes"]]
    p.config = config  # reasignar: JSONB no detecta cambios dentro del dict
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="project.update", target=p.slug, detail=detail))
    db.commit()
    return _out(db, user, p)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Project).order_by(Project.name)
    if not user.is_superuser:
        q = q.join(Membership, Membership.project_id == Project.id).where(Membership.user_id == user.id)
    out = []
    for p in db.scalars(q):
        out.append(_out(db, user, p))
    return out


@router.get("/projects/{project_id}/stats")
def project_stats(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Resumen de TODO el proyecto para el usuario actual: por clase, a mano, sin etiquetar y borrados."""
    if not role_in(db, user, project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    n = db.scalar(
        select(func.count()).select_from(Frame).where(Frame.project_id == project_id, Frame.deleted_at.is_(None))
    )
    base = (
        select(AnnotationBox)
        .join(Annotation, Annotation.id == AnnotationBox.annotation_id)
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Frame.project_id == project_id, Frame.deleted_at.is_(None), Annotation.user_id == user.id)
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
    project = db.get(Project, project_id)
    done = set(compute_states(db, project)) if project_mode(project) == "collab" else None
    reviewed = (
        len(done)
        if done is not None
        else db.scalar(
            select(func.count())
            .select_from(Annotation)
            .join(Frame, Frame.id == Annotation.frame_id)
            .where(
                Frame.project_id == project_id,
                Frame.deleted_at.is_(None),
                Annotation.user_id == user.id,
                Annotation.reviewed,
            )
        )
    )
    assigned = db.scalar(
        select(func.count())
        .select_from(Assignment)
        .join(Frame, Frame.id == Assignment.frame_id)
        .where(Frame.project_id == project_id, Frame.deleted_at.is_(None), Assignment.user_id == user.id)
    )
    if done is not None:
        mine = set(
            db.scalars(
                select(Assignment.frame_id)
                .join(Frame, Frame.id == Assignment.frame_id)
                .where(Frame.project_id == project_id, Frame.deleted_at.is_(None), Assignment.user_id == user.id)
            )
        )
        assigned_done = len(mine & done)
    else:
        assigned_done = db.scalar(
            select(func.count())
            .select_from(Assignment)
            .join(Frame, Frame.id == Assignment.frame_id)
            .join(Annotation, (Annotation.frame_id == Assignment.frame_id) & (Annotation.user_id == Assignment.user_id))
            .where(
                Frame.project_id == project_id,
                Frame.deleted_at.is_(None),
                Assignment.user_id == user.id,
                Annotation.reviewed,
            )
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
