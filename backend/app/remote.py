"""Ejecución remota: workers con GPU que reclaman trabajos de una cola en la base (repo veyra-worker)."""

import datetime as dt
import hashlib
import secrets
import time
from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import GpuWorker, WorkerJob

DONE = ("done", "failed", "cancelled")


class RemoteUnavailable(RuntimeError):
    """Se pidió ejecutar en un worker y no hay ninguno en línea."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return "vw_" + secrets.token_urlsafe(32)


def online(w: GpuWorker) -> bool:
    return bool(w.active and w.last_seen and (_now() - w.last_seen).total_seconds() < settings.worker_offline_after)


def reap(db: Session) -> int:
    """Los trabajos en curso de un worker que dejó de dar señales se dan por fallidos (y pueden volver a lanzarse)."""
    cutoff = _now() - dt.timedelta(seconds=settings.worker_offline_after)
    stale = db.scalars(
        select(WorkerJob).join(GpuWorker, GpuWorker.id == WorkerJob.worker_id).where(
            WorkerJob.status == "running", (GpuWorker.last_seen < cutoff) | (GpuWorker.active.is_(False))
        )
    ).all()  # fmt: skip
    for j in stale:
        j.status, j.finished_at = "failed", _now()
        j.error = (j.error or "") + "el worker dejó de responder"
    if stale:
        db.commit()
    return len(stale)


def pick_worker(db: Session, kind: str, force: bool = False) -> GpuWorker | None:
    """None = ejecutar en esta máquina. Con gpu_mode=remote y sin worker en línea se lanza RemoteUnavailable.

    `force`: el trabajo solo puede ejecutarlo un worker (espacios de trabajo): se ignora gpu_mode.
    """
    mode = "remote" if force else settings.gpu_mode
    if mode == "local":
        return None
    ws = [w for w in db.scalars(select(GpuWorker).where(GpuWorker.active)) if online(w) and kind in (w.kinds or [])]
    if not ws:
        if mode == "remote":
            raise RemoteUnavailable("No hay ningún worker de GPU en línea que pueda hacer este trabajo")
        return None
    running = {
        w.id: len(
            db.scalars(select(WorkerJob.id).where(WorkerJob.worker_id == w.id, WorkerJob.status == "running")).all()
        )
        for w in ws
    }
    return min(ws, key=lambda w: running[w.id])


def online_workers(db: Session, kind: str) -> list[GpuWorker]:
    return [w for w in db.scalars(select(GpuWorker).where(GpuWorker.active)) if online(w) and kind in (w.kinds or [])]


def submit(kind: str, payload: dict, worker_id: int | None = None) -> int:
    with SessionLocal() as db:
        # `worker_id` queda en el trabajo solo cuando se reclama; aquí se guarda la preferencia dentro de la carga
        j = WorkerJob(kind=kind, payload={**payload, "_prefer": worker_id} if worker_id else payload, status="queued")
        db.add(j)
        db.commit()
        return j.id


def snapshot(j: WorkerJob) -> dict:
    return {
        "id": j.id,
        "status": j.status,
        "progress": j.progress or {},
        "result": j.result,
        "log": j.log or "",
        "error": j.error or "",
    }


def get(job_id: int) -> dict | None:
    with SessionLocal() as db:
        j = db.get(WorkerJob, job_id)
        return snapshot(j) if j else None


def request_cancel(job_id: int) -> None:
    with SessionLocal() as db:
        j = db.get(WorkerJob, job_id)
        if j is None or j.status in DONE:
            return
        if j.status == "queued":  # nadie lo tomó: se cancela sin más
            j.status, j.finished_at = "cancelled", _now()
        j.cancel = True
        db.commit()


def wait(
    job_id: int,
    on_update: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    queue_timeout: float | None = None,
    poll: float = 1.0,
    stop_when: Callable[[dict], bool] | None = None,
) -> dict:
    """Espera a que termine el trabajo (o a que `stop_when` se cumpla).

    Pasa el avance a `on_update` y propaga la cancelación."""
    t0, sent_cancel, last = time.time(), False, None
    while True:
        with SessionLocal() as db:
            reap(db)
        snap = get(job_id)
        if snap is None:
            return {
                "id": job_id,
                "status": "failed",
                "progress": {},
                "result": None,
                "log": "",
                "error": "el trabajo desapareció",
            }
        key = (snap["status"], str(snap["progress"]), len(snap["log"]))
        if on_update and key != last:
            on_update(snap)
        last = key
        if snap["status"] in DONE or (stop_when and stop_when(snap)):
            return snap
        if cancelled and not sent_cancel and cancelled():
            request_cancel(job_id)
            sent_cancel = True
        if queue_timeout is not None and snap["status"] == "queued" and time.time() - t0 > queue_timeout:
            request_cancel(job_id)
            return {**snap, "status": "failed", "error": "ningún worker tomó el trabajo a tiempo"}
        time.sleep(poll)


def recover() -> int:
    """Al arrancar la API nadie espera los trabajos que quedaron abiertos: se cancelan (el worker los detiene)."""
    with SessionLocal() as db:
        jobs = db.scalars(select(WorkerJob).where(WorkerJob.status.in_(["queued", "running"]))).all()
        for j in jobs:
            j.cancel = True
            if j.status == "queued":
                j.status, j.finished_at = "cancelled", _now()
        db.commit()
        return len(jobs)


def claim(db: Session, worker: GpuWorker, kinds: list[str]) -> WorkerJob | None:
    """Toma de forma atómica el trabajo más antiguo de esos tipos (dos workers nunca reciben el mismo)."""
    ids = db.scalars(
        select(WorkerJob.id)
        .where(WorkerJob.status == "queued", WorkerJob.kind.in_(kinds))
        .order_by(WorkerJob.id)
        .limit(20)
    ).all()
    for jid in ids:
        j = db.get(WorkerJob, jid)
        prefer = (j.payload or {}).get("_prefer")
        if prefer and prefer != worker.id:
            continue
        got = db.execute(
            update(WorkerJob)
            .where(WorkerJob.id == jid, WorkerJob.status == "queued")
            .values(status="running", worker_id=worker.id, claimed_at=_now())
        )
        db.commit()
        if got.rowcount:
            db.refresh(j)
            return j
    return None
