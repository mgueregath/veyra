"""Exportación y restauración completa de un proyecto (datos y etiquetas; imágenes y videos van por ruta)."""

import datetime as dt
import gzip
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Annotation,
    AnnotationBox,
    Assignment,
    DatasetItem,
    DatasetVersion,
    Frame,
    Membership,
    Project,
    Proposal,
    Review,
    User,
    Video,
)

FORMAT = 1


def dump_project(db: Session, project: Project) -> dict:
    users = {u.id: u.username for u in db.scalars(select(User))}
    videos = db.scalars(select(Video).where(Video.project_id == project.id).order_by(Video.id)).all()
    vname = {v.id: v.name for v in videos}
    frames = db.scalars(
        select(Frame)
        .options(selectinload(Frame.proposals))
        .where(Frame.project_id == project.id)
        .order_by(Frame.position, Frame.id)
    ).all()
    fkey = {f.id: [vname[f.video_id], f.frame_index] for f in frames}
    anns = db.scalars(
        select(Annotation)
        .options(selectinload(Annotation.boxes))
        .join(Frame, Frame.id == Annotation.frame_id)
        .where(Frame.project_id == project.id)
    ).all()
    pidx = {
        p.id: (f.id, i) for f in frames for i, p in enumerate(f.proposals)
    }  # propuesta → (imagen, posición en su lista)
    versions = db.scalars(
        select(DatasetVersion)
        .options(selectinload(DatasetVersion.items))
        .where(DatasetVersion.project_id == project.id)
        .order_by(DatasetVersion.id)
    ).all()
    return {
        "format": FORMAT,
        "project": {"slug": project.slug, "name": project.name, "config": project.config},
        "members": [
            {"user": users[m.user_id], "role": m.role}
            for m in db.scalars(select(Membership).where(Membership.project_id == project.id))
        ],
        "videos": [{"name": v.name, "path": v.path, "meta": v.meta} for v in videos],
        "frames": [
            {
                "video": vname[f.video_id],
                "frame_index": f.frame_index,
                "t": f.t,
                "image_path": f.image_path,
                "width": f.width,
                "height": f.height,
                "stratum": f.stratum,
                "position": f.position,
                "meta": f.meta,
                "deleted": f.deleted_at is not None,
                "proposals": [
                    {"bbox": [p.x, p.y, p.w, p.h], "sources": p.sources, "score": p.score} for p in f.proposals
                ],
            }
            for f in frames
        ],
        "annotations": [
            {
                "frame": fkey[a.frame_id],
                "user": users[a.user_id],
                "reviewed": a.reviewed,
                "boxes": [
                    {
                        "bbox": [b.x, b.y, b.w, b.h],
                        "label": b.label,
                        "manual": b.manual,
                        "orig_bbox": b.orig_bbox,
                        "sources": b.sources,
                        "score": b.score,
                        "deleted": b.deleted,
                        "proposal": pidx[b.proposal_id][1]
                        if b.proposal_id in pidx and pidx[b.proposal_id][0] == a.frame_id
                        else None,
                    }
                    for b in a.boxes
                ],
            }
            for a in anns
        ],
        "reviews": [
            {
                "frame": fkey[r.frame_id],
                "reviewer": users[r.reviewer_id],
                "final_user": users[r.final_user_id],
                "note": r.note,
            }
            for r in db.scalars(
                select(Review).join(Frame, Frame.id == Review.frame_id).where(Frame.project_id == project.id)
            )
        ],
        "assignments": [
            {"frame": fkey[a.frame_id], "user": users[a.user_id]}
            for a in db.scalars(
                select(Assignment).join(Frame, Frame.id == Assignment.frame_id).where(Frame.project_id == project.id)
            )
        ],
        "datasets": [
            {
                "name": v.name,
                "rules": v.rules,
                "created_by": users[v.created_by],
                "items": [
                    {
                        "frame": fkey[i.frame_id],
                        "split": i.split,
                        "source": i.source,
                        "boxes": i.boxes,
                        "discarded": i.discarded,
                    }
                    for i in v.items
                    if i.frame_id in fkey
                ],
            }
            for v in versions
        ],
    }


def dump_bytes(db: Session, project: Project) -> bytes:
    return gzip.compress(json.dumps(dump_project(db, project), ensure_ascii=False).encode())


def restore_project(db: Session, data: dict, slug: str | None = None, name: str | None = None) -> Project:
    """Crea un proyecto NUEVO con el contenido del volcado. Los usuarios deben existir (se omiten los que no)."""
    if data.get("format") != FORMAT:
        raise ValueError("formato de copia desconocido")
    pj = data["project"]
    slug = slug or pj["slug"]
    if db.scalar(select(Project.id).where(Project.slug == slug)):
        raise ValueError(f"ya existe un proyecto con el nombre corto «{slug}»")
    users = {u.username: u.id for u in db.scalars(select(User))}
    p = Project(slug=slug, name=name or pj["name"], config=pj["config"])
    db.add(p)
    db.flush()
    for m in data["members"]:
        if m["user"] in users:
            db.add(Membership(user_id=users[m["user"]], project_id=p.id, role=m["role"]))
    vids = {}
    for v in data["videos"]:
        row = Video(project_id=p.id, name=v["name"], path=v["path"], meta=v["meta"])
        db.add(row)
        db.flush()
        vids[v["name"]] = row.id
    fr: dict[tuple, Frame] = {}
    for f in data["frames"]:
        row = Frame(
            project_id=p.id,
            video_id=vids[f["video"]],
            frame_index=f["frame_index"],
            t=f["t"],
            image_path=f["image_path"],
            width=f["width"],
            height=f["height"],
            stratum=f["stratum"],
            position=f["position"],
            meta=f["meta"],
            deleted_at=dt.datetime.now(dt.timezone.utc) if f.get("deleted") else None,
        )
        row.proposals = [
            Proposal(
                x=q["bbox"][0], y=q["bbox"][1], w=q["bbox"][2], h=q["bbox"][3], sources=q["sources"], score=q["score"]
            )
            for q in f["proposals"]
        ]
        db.add(row)
        fr[(f["video"], f["frame_index"])] = row
    db.flush()
    for a in data["annotations"]:
        if a["user"] not in users:
            continue
        frame = fr[tuple(a["frame"])]
        ann = Annotation(frame_id=frame.id, user_id=users[a["user"]], reviewed=a["reviewed"])
        ann.boxes = [
            AnnotationBox(
                proposal_id=frame.proposals[b["proposal"]].id if b["proposal"] is not None else None,
                x=b["bbox"][0],
                y=b["bbox"][1],
                w=b["bbox"][2],
                h=b["bbox"][3],
                label=b["label"],
                manual=b["manual"],
                orig_bbox=b["orig_bbox"],
                sources=b["sources"],
                score=b["score"],
                deleted=b["deleted"],
            )
            for b in a["boxes"]
        ]
        db.add(ann)
    for r in data["reviews"]:
        if r["reviewer"] in users and r["final_user"] in users:
            db.add(
                Review(
                    frame_id=fr[tuple(r["frame"])].id,
                    reviewer_id=users[r["reviewer"]],
                    final_user_id=users[r["final_user"]],
                    note=r["note"],
                )
            )
    for a in data["assignments"]:
        if a["user"] in users:
            db.add(Assignment(frame_id=fr[tuple(a["frame"])].id, user_id=users[a["user"]]))
    for d in data["datasets"]:
        v = DatasetVersion(
            project_id=p.id,
            name=d["name"],
            rules=d["rules"],
            created_by=users.get(d["created_by"], next(iter(users.values()))),
        )
        v.items = [
            DatasetItem(
                frame_id=fr[tuple(i["frame"])].id,
                split=i["split"],
                source=i["source"],
                boxes=i["boxes"],
                discarded=i.get("discarded", []),
            )
            for i in d["items"]
        ]
        db.add(v)
    db.commit()
    return p


def load_bytes(raw: bytes) -> dict:
    return json.loads(gzip.decompress(raw))
