import random

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session, selectinload

from .. import consensus
from ..db import get_db
from ..deps import WRITE_ROLES, current_user, role_in
from ..models import Annotation, Assignment, AuditLog, Frame, Project, Review, User
from ..schemas import AssignIn, BoxOut, QueueIn, ReviewIn, UserAnnotationOut
from ..workflow import acquire_lock, active_lock_holder, class_sets, compute_states, labeled, release_lock

router = APIRouter(prefix="/api", tags=["workflow"])
REVIEW_ROLES = {"admin", "reviewer"}


def _project(db: Session, user: User, project_id: int, roles: set[str] | None = None) -> Project:
    p = db.get(Project, project_id)
    role = role_in(db, user, project_id) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if roles is not None and role not in roles:
        raise HTTPException(403, "Tu rol no permite esta acción")
    return p


def _frame(db: Session, user: User, frame_id: int, roles: set[str] | None = None) -> Frame:
    f = db.get(Frame, frame_id)
    role = role_in(db, user, f.project_id) if f else None
    if not f or not role:
        raise HTTPException(404, "Imagen no encontrada")
    if roles is not None and role not in roles:
        raise HTTPException(403, "Tu rol no permite esta acción")
    return f


# ---------- cola ----------
@router.post("/projects/{project_id}/queue/next")
def queue_next(
    project_id: int, body: QueueIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Entrega la siguiente imagen por etiquetar y la bloquea para esta persona (atómico: nadie más la recibe)."""
    _project(db, user, project_id, WRITE_ROLES)
    scope = body.scope if body else "assigned"
    mine_done = exists().where(
        and_(Annotation.frame_id == Frame.id, Annotation.user_id == user.id, Annotation.reviewed)
    )
    has_review = exists().where(Review.frame_id == Frame.id)
    mine = exists().where(and_(Assignment.frame_id == Frame.id, Assignment.user_id == user.id))
    others = exists().where(and_(Assignment.frame_id == Frame.id, Assignment.user_id != user.id))
    q = select(Frame.id, Frame.position).where(Frame.project_id == project_id, ~mine_done, ~has_review)
    if scope == "assigned":
        q = q.where(mine)
    else:  # las propias primero y luego las que no son de nadie más
        q = q.where(mine | ~others)
    q = q.order_by(~mine, Frame.position).limit(40)
    for fid, pos in db.execute(q).all():
        if acquire_lock(db, fid, user.id):
            return {"frame_id": fid, "position": pos}
    return {"frame_id": None, "position": None}


@router.post("/frames/{frame_id}/lock")
def lock_frame(frame_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Renueva el bloqueo mientras la imagen está abierta. Nunca falla: si es de otra persona solo lo informa."""
    f = _frame(db, user, frame_id, WRITE_ROLES)
    if acquire_lock(db, f.id, user.id):
        return {"ok": True, "holder": None}
    holder = active_lock_holder(db, f.id, exclude_user=user.id)
    return {"ok": False, "holder": db.get(User, holder).username if holder else None}


# ---------- asignaciones ----------
@router.post("/projects/{project_id}/assignments")
def assign(project_id: int, body: AssignIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Reparte imágenes entre usuarios (round-robin con semilla) y, opcionalmente, un % a una 2.ª persona."""
    p = _project(db, user, project_id, {"admin"})
    from ..models import Membership

    users = []
    for name in body.users:
        u = db.scalar(select(User).where(User.username == name))
        if not u or not db.scalar(
            select(Membership.id).where(Membership.user_id == u.id, Membership.project_id == p.id)
        ):
            raise HTTPException(422, f"El usuario {name} no pertenece al proyecto")
        users.append(u)
    if body.frame_ids is not None:
        valid = set(db.scalars(select(Frame.id).where(Frame.project_id == p.id, Frame.id.in_(body.frame_ids))))
        pool = [i for i in body.frame_ids if i in valid]
    else:
        pool = list(
            db.scalars(
                select(Frame.id)
                .where(
                    Frame.project_id == p.id,
                    ~exists().where(Assignment.frame_id == Frame.id),
                    ~exists().where(and_(Annotation.frame_id == Frame.id, Annotation.reviewed)),
                )
                .order_by(Frame.position)
            )
        )
    rng = random.Random(body.seed)
    rng.shuffle(pool)
    existing = (
        {(a.frame_id, a.user_id) for a in db.scalars(select(Assignment).where(Assignment.frame_id.in_(pool)))}
        if pool
        else set()
    )
    n_new = 0
    for k, fid in enumerate(pool):
        if (fid, users[k % len(users)].id) not in existing:
            db.add(Assignment(frame_id=fid, user_id=users[k % len(users)].id))
            n_new += 1
    overlap = rng.sample(range(len(pool)), round(len(pool) * body.overlap_pct / 100)) if len(users) > 1 else []
    for k in overlap:
        second = users[(k + 1) % len(users)]
        if (pool[k], second.id) not in existing:
            db.add(Assignment(frame_id=pool[k], user_id=second.id))
            n_new += 1
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="assignments.create",
            detail={"frames": len(pool), "new": n_new, "overlap": len(overlap)},
        )
    )
    db.commit()
    return {"frames": len(pool), "created": n_new, "overlap": len(overlap)}


@router.get("/projects/{project_id}/assignments")
def assignments_summary(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id, REVIEW_ROLES)
    done = exists().where(
        and_(Annotation.frame_id == Assignment.frame_id, Annotation.user_id == Assignment.user_id, Annotation.reviewed)
    )
    rows = db.execute(
        select(User.username, func.count(Assignment.id), func.count(Assignment.id).filter(done))
        .join(Assignment, Assignment.user_id == User.id)
        .join(Frame, Frame.id == Assignment.frame_id)
        .where(Frame.project_id == p.id)
        .group_by(User.username)
        .order_by(User.username)
    ).all()
    return [{"user": u, "assigned": a, "done": d} for u, a, d in rows]


# ---------- revisión ----------
@router.get("/frames/{frame_id}/annotations", response_model=list[UserAnnotationOut])
def frame_annotations(frame_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Lo que hizo cada persona con esta imagen (para el revisor)."""
    f = _frame(db, user, frame_id, REVIEW_ROLES)
    anns = db.scalars(
        select(Annotation)
        .options(selectinload(Annotation.boxes))
        .where(Annotation.frame_id == f.id)
        .order_by(Annotation.id)
    ).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([a.user_id for a in anns])))} if anns else {}
    names = {i: u.username for i, u in users.items()}
    return [
        UserAnnotationOut(
            user_id=a.user_id,
            user=names[a.user_id],
            display_name=users[a.user_id].display_name,
            reviewed=a.reviewed,
            boxes=[
                BoxOut(
                    id=b.id,
                    proposal_id=b.proposal_id,
                    bbox=[b.x, b.y, b.w, b.h],
                    label=b.label,
                    manual=b.manual,
                    orig_bbox=b.orig_bbox,
                    sources=b.sources or [],
                    score=b.score,
                    deleted=b.deleted,
                )
                for b in a.boxes
            ],
        )
        for a in anns
    ]


@router.post("/frames/{frame_id}/review")
def review_frame(frame_id: int, body: ReviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """El revisor fija cuál anotación es la final (la de otra persona o la suya, tras corregirla)."""
    f = _frame(db, user, frame_id, REVIEW_ROLES)
    if not db.scalar(
        select(Annotation.id).where(Annotation.frame_id == f.id, Annotation.user_id == body.final_user_id)
    ):
        raise HTTPException(422, "Ese usuario no tiene anotación en esta imagen")
    rv = db.scalar(select(Review).where(Review.frame_id == f.id))
    if rv is None:
        rv = Review(frame_id=f.id, reviewer_id=user.id, final_user_id=body.final_user_id, note=body.note)
        db.add(rv)
    else:
        rv.reviewer_id, rv.final_user_id, rv.note = user.id, body.final_user_id, body.note
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=f.project_id,
            action="review.set",
            target=f"frame:{f.id}",
            detail={"final_user_id": body.final_user_id},
        )
    )
    db.commit()
    release_lock(db, f.id, user.id)
    return {"status": "final"}


@router.delete("/frames/{frame_id}/review")
def undo_review(frame_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = _frame(db, user, frame_id, REVIEW_ROLES)
    rv = db.scalar(select(Review).where(Review.frame_id == f.id))
    if rv:
        db.delete(rv)
        db.add(AuditLog(user_id=user.id, project_id=f.project_id, action="review.undo", target=f"frame:{f.id}"))
        db.commit()
    return {"ok": True}


# ---------- calidad ----------
@router.get("/projects/{project_id}/quality")
def quality(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Estado del etiquetado, trabajo por persona y acuerdo entre anotadores."""
    p = _project(db, user, project_id, REVIEW_ROLES)
    _, negative = class_sets(p)
    states = compute_states(db, p)
    status_counts = {s: 0 for s in ("pending", "single", "agreed", "disagreement", "final")}
    total = db.scalar(select(func.count()).select_from(Frame).where(Frame.project_id == p.id))
    for st in states.values():
        status_counts[st.status] += 1
    status_counts["pending"] = total - sum(v for k, v in status_counts.items() if k != "pending")
    anns = db.scalars(
        select(Annotation)
        .options(selectinload(Annotation.boxes))
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Frame.project_id == p.id, Annotation.reviewed)
    ).all()
    names = (
        {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({a.user_id for a in anns})))}
        if anns
        else {}
    )
    per_user: dict[str, dict] = {}
    by_frame: dict[int, list[Annotation]] = {}
    for a in anns:
        u = per_user.setdefault(names[a.user_id], {"user": names[a.user_id], "reviewed": 0, "boxes": {}})
        u["reviewed"] += 1
        for lb, _ in ((b.label, 1) for b in a.boxes if not b.deleted and b.label):
            u["boxes"][lb] = u["boxes"].get(lb, 0) + 1
        by_frame.setdefault(a.frame_id, []).append(a)
    f1s = []
    for lst in by_frame.values():
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                f1s.append(consensus.pair_f1(labeled(lst[i]), labeled(lst[j]), negative))
    overlap = sum(1 for lst in by_frame.values() if len(lst) >= 2)
    return {
        "frames": total,
        "status": status_counts,
        "users": sorted(per_user.values(), key=lambda x: x["user"]),
        "agreement": {
            "frames_with_overlap": overlap,
            "mean_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
            "agree_rate": round(status_counts["agreed"] / overlap, 4) if overlap else None,
        },
    }
