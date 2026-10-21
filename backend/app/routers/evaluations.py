import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import detectors
from ..db import get_db
from ..deps import current_user, role_in
from ..evaluation import Pred, evaluate
from ..models import AuditLog, DatasetVersion, Frame, Project, Run, User, Video
from ..workflow import class_sets

router = APIRouter(prefix="/api", tags=["evaluation"])
ROLES = {"admin", "reviewer"}


class EvalIn(BaseModel):
    dataset_version_id: int
    split: str = Field("test", pattern="^(train|val|test|all)$")
    detector: str = "baseline:appearance"
    score_threshold: float | None = None  # None = el que da el mejor F1 (se informa)
    iou: float = Field(0.5, gt=0, le=1)


def _project(db, user, pid, roles=None):
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if roles and role not in roles:
        raise HTTPException(403, "Tu rol no permite esta acción")
    return p


def run_evaluation(
    db: Session, project: Project, version: DatasetVersion, spec: str, split: str, score_thr, iou
) -> dict:
    positive, _ = class_sets(project)
    items = [i for i in version.items if split == "all" or i.split == split]
    if not items:
        raise HTTPException(422, f"La versión no tiene imágenes en la partición «{split}»")
    ids = [i.frame_id for i in items]
    names = {fid: vn for fid, vn in db.execute(select(Frame.id, Video.name).join(Video).where(Frame.id.in_(ids)))}
    gt = {i.frame_id: [(tuple(b["bbox"]), b["label"]) for b in i.boxes if b["label"] in positive] for i in items}
    try:
        kind, name = detectors.parse_spec(spec)
        if kind == "baseline":
            preds = detectors.baseline_predictions(db, project, ids, name)
        elif kind == "flow":
            from ..assisted import flow_predictions
            from ..models import DetectionFlow

            f = db.get(DetectionFlow, int(name))
            if not f or f.project_id != project.id:
                raise ValueError(f"el flujo {name} no existe en este proyecto")
            raw = flow_predictions(db, project, f.spec, ids)
            # las fuentes sin clase (apariencia, movimiento, procedimientos) se evalúan como la primera clase positiva
            preds = {fid: [Pred(p.bbox, p.label or positive[0], p.score) for p in ps] for fid, ps in raw.items()}
        else:
            from ..inference import model_predictions

            preds = model_predictions(db, project, int(name), ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None
    m = evaluate(gt, preds, positive, names, iou_thr=iou, score_thr=score_thr)
    dims = {f.id: (f.width, f.height) for f in db.scalars(select(Frame).where(Frame.id.in_(ids)))}
    for ex in m["examples"]["false_positives"] + m["examples"]["false_negatives"]:
        ex["w"], ex["h"] = dims[ex["frame_id"]]
        ex["video"] = names[ex["frame_id"]]
    m["split"], m["detector"], m["dataset"] = split, spec, version.name
    return m


@router.post("/projects/{project_id}/evaluations")
def create_evaluation(project_id: int, body: EvalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id, ROLES)
    v = db.get(DatasetVersion, body.dataset_version_id)
    if not v or v.project_id != p.id:
        raise HTTPException(404, "Versión de dataset no encontrada")
    run = Run(
        project_id=p.id,
        kind="evaluation",
        name=f"{body.detector} · {v.name} · {body.split}",
        dataset_version_id=v.id,
        params=body.model_dump(),
        status="running",
        created_by=user.id,
    )
    db.add(run)
    db.commit()
    try:
        run.metrics = run_evaluation(db, p, v, body.detector, body.split, body.score_threshold, body.iou)
        run.status = "done"
    except HTTPException as e:
        run.status, run.log = "failed", str(e.detail)
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        raise
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    db.add(
        AuditLog(
            user_id=user.id,
            project_id=p.id,
            action="evaluation.run",
            target=f"run:{run.id}",
            detail={"detector": body.detector},
        )
    )
    db.commit()
    return _out(run, db, full=True)


def _summary(run: Run) -> dict:
    m = run.metrics or {}
    micro = m.get("micro") or {}
    return {
        "map": m.get("map"),
        "precision": micro.get("precision"),
        "recall": micro.get("recall"),
        "f1": micro.get("f1"),
        "score_threshold": m.get("score_threshold"),
        "count_mae": m.get("count_mae"),
        "split": m.get("split"),
        "dataset": m.get("dataset"),
        "progress": m.get("progress"),  # entrenamientos: época actual y métricas de validación
        "map50": m.get("map50"),
        "boxes": m.get("boxes"),  # preanotaciones: cajas propuestas
    }


def _out(run: Run, db: Session, full=False) -> dict:
    d = {
        "id": run.id,
        "kind": run.kind,
        "name": run.name,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "created_by": db.get(User, run.created_by).username,
        "params": run.params,
        "summary": _summary(run),
    }
    if full:
        d["metrics"], d["log"] = run.metrics, run.log
    return d


@router.get("/projects/{project_id}/runs")
def list_runs(
    project_id: int, kind: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = _project(db, user, project_id, ROLES)
    q = select(Run).where(Run.project_id == p.id).order_by(Run.id.desc()).limit(200)
    if kind:
        q = q.where(Run.kind == kind)
    return [_out(r, db) for r in db.scalars(q)]


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "Ejecución no encontrada")
    _project(db, user, r.project_id, ROLES)
    return _out(r, db, full=True)
