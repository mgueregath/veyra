"""Etiquetado asistido: preanotar con un modelo y priorizar las imágenes que más conviene etiquetar a mano."""

import datetime as dt

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from . import consensus
from .db import SessionLocal
from .inference import model_predictions
from .models import Annotation, Frame, Project, Proposal, Review, Run

BATCH = 48


def frames_in_scope(db: Session, project: Project, scope: str, frame_ids: list[int] | None) -> list[int]:
    q = select(Frame.id).where(Frame.project_id == project.id).order_by(Frame.position)
    if scope == "frame_ids":
        q = q.where(Frame.id.in_(frame_ids or []))
    elif scope == "unreviewed":  # nadie la revisó todavía: es donde la ayuda sirve
        q = q.where(
            ~exists().where(Annotation.frame_id == Frame.id, Annotation.reviewed),
            ~exists().where(Review.frame_id == Frame.id),
        )
    return list(db.scalars(q))


def execute_preannotation(run_id: int, cancelled: set[int]) -> None:
    """Corre el modelo por lotes y guarda sus cajas como propuestas (fuente «model»).

    Repetirlo reemplaza las propuestas anteriores del mismo modelo.
    """
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run.status != "queued" or run_id in cancelled:
            run.status, run.finished_at = "cancelled", dt.datetime.now(dt.timezone.utc)
            db.commit()
            cancelled.discard(run_id)
            return
        run.status = "running"
        db.commit()
        project = db.get(Project, run.project_id)
        prm = dict(run.params)
        ids = frames_in_scope(db, project, prm["scope"], prm.get("frame_ids"))
    total, boxes, tag, err = len(ids), 0, f"model:{prm['model_id']}", None
    try:
        for i in range(0, total, BATCH):
            if run_id in cancelled:
                break
            batch = ids[i : i + BATCH]
            with SessionLocal() as db:
                project = db.get(Project, prm["project_id"])
                preds = model_predictions(db, project, prm["model_id"], batch)
                db.execute(delete(Proposal).where(Proposal.frame_id.in_(batch), Proposal.sources.contains([tag])))
                for fid, plist in preds.items():
                    for p in plist:
                        if p.score >= prm["min_score"]:
                            x, y, w, h = p.bbox
                            db.add(
                                Proposal(
                                    frame_id=fid,
                                    x=x,
                                    y=y,
                                    w=max(1, w),
                                    h=max(1, h),
                                    sources=["model", tag],
                                    score=round(p.score, 4),
                                )
                            )
                            boxes += 1
                r = db.get(Run, run_id)
                r.metrics = {"progress": {"done": min(i + BATCH, total), "total": total}, "boxes": boxes}
                db.commit()
    except Exception as e:  # el motivo queda en el registro de la ejecución
        err = str(e)
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        r.finished_at = dt.datetime.now(dt.timezone.utc)
        if run_id in cancelled:
            r.status = "cancelled"
        elif err:
            r.status, r.log = "failed", err
        else:
            r.status = "done"
            r.metrics = {
                "progress": {"done": total, "total": total},
                "frames": total,
                "boxes": boxes,
                "model_id": prm["model_id"],
                "min_score": prm["min_score"],
            }
        db.commit()
    cancelled.discard(run_id)


def rank_uncertain(db: Session, project: Project, model_id: int, limit: int) -> list[dict]:
    """Imágenes sin revisar donde conviene que mire una persona: el modelo duda o discrepa del detector de apariencia.

    incertidumbre = 1 - |2s - 1| de la caja del modelo más dudosa (máxima en s = 0,5);
    discrepancia = cajas seguras del modelo (s ≥ 0,5) que el detector no ve
                   + cajas fuertes del detector (≥ 6) que el modelo no ve.
    """
    tag = f"model:{model_id}"
    ids = frames_in_scope(db, project, "unreviewed", None)
    by: dict[int, list[Proposal]] = {i: [] for i in ids}
    if ids:
        for p in db.scalars(select(Proposal).where(Proposal.frame_id.in_(ids))):
            by[p.frame_id].append(p)
    out = []
    for fid, props in by.items():
        model = [p for p in props if tag in (p.sources or [])]
        if not model:
            continue  # sin preanotación de este modelo no hay nada que comparar
        blob = [p for p in props if "blob" in (p.sources or []) and (p.score or 0) >= 6]
        box = lambda p: (p.x, p.y, p.w, p.h)  # noqa: E731
        unc = max(1 - abs(2 * (p.score or 0) - 1) for p in model)
        miss_model = sum(
            1 for b in blob if not any(consensus.iou(box(b), box(m)) >= 0.3 for m in model if (m.score or 0) >= 0.3)
        )
        miss_blob = sum(
            1 for m in model if (m.score or 0) >= 0.5 and not any(consensus.iou(box(m), box(b)) >= 0.3 for b in blob)
        )
        score = unc + 0.5 * (miss_model + miss_blob)
        reasons = []
        if unc >= 0.6:
            reasons.append("el modelo duda")
        if miss_blob:
            reasons.append("el modelo ve algo que el detector no")
        if miss_model:
            reasons.append("el detector ve algo que el modelo no")
        out.append({"frame_id": fid, "score": round(score, 4), "uncertainty": round(unc, 4), "reasons": reasons})
    return sorted(out, key=lambda d: -d["score"])[:limit]
