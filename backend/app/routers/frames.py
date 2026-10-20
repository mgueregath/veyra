from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import false, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..db import get_db
from ..deps import WRITE_ROLES, current_user, role_in
from ..models import Annotation, AnnotationBox, Assignment, AuditLog, Frame, Project, Review, User
from ..schemas import AnnotationIn, AnnotationOut, BoxOut, FrameItem, FrameOut, ProposalOut, ReviewOut
from ..workflow import FrameState, active_lock_holder, compute_states

router = APIRouter(prefix="/api", tags=["frames"])


def _my_annotation(db: Session, frame_id: int, user_id: int) -> Annotation | None:
    return db.scalar(
        select(Annotation)
        .options(selectinload(Annotation.boxes))
        .where(Annotation.frame_id == frame_id, Annotation.user_id == user_id)
    )


def _ann_out(a: Annotation | None) -> AnnotationOut | None:
    if a is None:
        return None
    return AnnotationOut(
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


def _frame_for(db: Session, frame_id: int, user: User, write=False) -> Frame:
    f = db.scalar(
        select(Frame).options(selectinload(Frame.proposals), selectinload(Frame.video)).where(Frame.id == frame_id)
    )
    role = role_in(db, user, f.project_id) if f else None
    if not f or not role:
        raise HTTPException(404, "Imagen no encontrada")
    if write and role not in WRITE_ROLES:
        raise HTTPException(403, "Tu rol no permite modificar este proyecto")
    return f


def _list_frames(project: Project, offset, limit, flt, user, db):
    states = compute_states(db, project)
    mine_reviewed = set(
        db.scalars(
            select(Annotation.frame_id)
            .join(Frame, Frame.id == Annotation.frame_id)
            .where(Frame.project_id == project.id, Annotation.user_id == user.id, Annotation.reviewed)
        )
    )
    assigned = set(db.scalars(select(Assignment.frame_id).where(Assignment.user_id == user.id)))
    q = (
        select(Frame, Annotation)
        .outerjoin(Annotation, (Annotation.frame_id == Frame.id) & (Annotation.user_id == user.id))
        .options(selectinload(Frame.video), selectinload(Annotation.boxes))
        .where(Frame.project_id == project.id)
        .order_by(Frame.position, Frame.id)
    )
    if flt == "mine_pending":
        q = q.where(Frame.id.not_in(mine_reviewed)) if mine_reviewed else q
    elif flt == "assigned_pending":
        q = q.where(Frame.id.in_(assigned - mine_reviewed) if assigned - mine_reviewed else false())
    elif flt == "pending":
        q = q.where(Frame.id.not_in(list(states))) if states else q
    elif flt in ("single", "agreed", "disagreement", "final"):
        ids = [i for i, s in states.items() if s.status == flt]
        q = q.where(Frame.id.in_(ids) if ids else false())
    elif flt == "needs_review":
        ids = [i for i, s in states.items() if s.status in ("single", "agreed", "disagreement")]
        q = q.where(Frame.id.in_(ids) if ids else false())
    items = []
    for f, a in db.execute(q.offset(offset).limit(limit)):
        counts: dict[str, int] = {}
        for b in a.boxes if a else []:
            if not b.deleted and b.label:
                counts[b.label] = counts.get(b.label, 0) + 1
        items.append(
            FrameItem(
                id=f.id,
                position=f.position,
                video=f.video.name,
                frame_index=f.frame_index,
                t=f.t,
                stratum=f.stratum,
                origin=(f.meta or {}).get("origin"),
                status=states.get(f.id, FrameState()).status,
                assigned=f.id in assigned,
                reviewed=bool(a and a.reviewed),
                n_labeled=counts,
            )
        )
    return items


@router.get("/frames/{frame_id}", response_model=FrameOut)
def get_frame(frame_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = _frame_for(db, frame_id, user)
    n_ann = db.scalar(select(func.count()).select_from(Annotation).where(Annotation.frame_id == f.id))
    project = db.get(Project, f.project_id)
    st = compute_states(db, project, [f.id]).get(f.id, FrameState())
    holder = active_lock_holder(db, f.id, exclude_user=user.id)
    rv = db.scalar(select(Review).where(Review.frame_id == f.id))
    review = None
    if rv:
        names = {
            u.id: u.username for u in db.scalars(select(User).where(User.id.in_([rv.reviewer_id, rv.final_user_id])))
        }
        review = ReviewOut(
            reviewer=names[rv.reviewer_id],
            final_user=names[rv.final_user_id],
            final_user_id=rv.final_user_id,
            note=rv.note,
        )
    return FrameOut(
        id=f.id,
        position=f.position,
        video=f.video.name,
        video_id=f.video_id,
        has_video=bool(f.video.path),
        frame_index=f.frame_index,
        t=f.t,
        width=f.width,
        height=f.height,
        stratum=f.stratum,
        n_annotators=n_ann,
        status=st.status,
        n_reviewed=st.n_reviewed,
        locked_by=db.get(User, holder).username if holder else None,
        assigned_to_me=db.scalar(
            select(Assignment.id).where(Assignment.frame_id == f.id, Assignment.user_id == user.id)
        )
        is not None,
        review=review,
        annotation=_ann_out(_my_annotation(db, f.id, user.id)),
        proposals=[
            ProposalOut(id=p.id, bbox=[p.x, p.y, p.w, p.h], sources=p.sources or [], score=p.score) for p in f.proposals
        ],
    )


@router.put("/frames/{frame_id}/annotation", response_model=AnnotationOut)
def save_annotation(
    frame_id: int, body: AnnotationIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Guarda el trabajo de ESTE usuario sobre el frame (reemplaza su conjunto de cajas)."""
    f = _frame_for(db, frame_id, user, write=True)
    config = db.get(Project, f.project_id).config or {}
    valid = {c["id"] for c in config.get("classes", [])}
    for b in body.boxes:
        if b.label is not None and valid and b.label not in valid:
            raise HTTPException(422, f"Clase desconocida: {b.label}")
        if b.bbox[2] <= 0 or b.bbox[3] <= 0:
            raise HTTPException(422, "Caja con tamaño no positivo")
    proposal_ids = {p.id for p in f.proposals}
    ann = _my_annotation(db, f.id, user.id)
    if ann is None:
        ann = Annotation(frame_id=f.id, user_id=user.id)
        db.add(ann)
    ann.reviewed = body.reviewed
    ann.boxes = [
        AnnotationBox(
            proposal_id=b.proposal_id if b.proposal_id in proposal_ids else None,
            x=b.bbox[0],
            y=b.bbox[1],
            w=b.bbox[2],
            h=b.bbox[3],
            label=b.label,
            manual=b.manual,
            orig_bbox=b.orig_bbox,
            sources=b.sources,
            score=b.score,
            deleted=b.deleted,
        )
        for b in body.boxes
    ]
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=f.project_id,
            action="annotation.save",
            target=f"frame:{f.id}",
            detail={"reviewed": body.reviewed, "boxes": len(body.boxes)},
        )
    )
    db.commit()
    return _ann_out(_my_annotation(db, f.id, user.id))


@router.get("/frames/{frame_id}/image")
def frame_image(frame_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = _frame_for(db, frame_id, user)
    root = Path(settings.media_root).resolve()
    path = (root / f.image_path).resolve()
    if root not in path.parents or not path.is_file():  # nunca fuera de media_root
        raise HTTPException(404, "Imagen no disponible")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/projects/{project_id}/frames", response_model=list[FrameItem])
def list_frames(
    project_id: int,
    offset: int = 0,
    limit: int = Query(100, ge=1, le=500),
    only_pending: bool = False,
    filter: str = Query(
        "all", pattern="^(all|mine_pending|assigned_pending|pending|single|agreed|disagreement|final|needs_review)$"
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or not role_in(db, user, project_id):
        raise HTTPException(404, "Proyecto no encontrado")
    return _list_frames(project, offset, limit, "mine_pending" if only_pending else filter, user, db)
