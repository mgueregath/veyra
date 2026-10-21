"""Workers de GPU: gestión (superusuario) y protocolo que usa el worker (token propio, ver docs/WORKERS.md)."""

import datetime as dt
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import remote
from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import current_user
from ..models import AuditLog, GpuWorker, User, WorkerJob

router = APIRouter(prefix="/api", tags=["workers"])
KINDS = {"train", "predict", "live", "workspace"}
LOG_CAP = 60_000
LOST_GRACE = 15  # segundos: un trabajo recién reclamado puede no aparecer todavía en los latidos del worker


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- gestión (personas)
def superuser(user: User = Depends(current_user)) -> User:
    if not user.is_superuser:
        raise HTTPException(403, "Solo un administrador del sistema puede gestionar los workers de GPU")
    return user


class WorkerCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.\-]{2,64}$")


def _out(db: Session, w: GpuWorker) -> dict:
    running = db.scalar(
        select(func.count()).select_from(WorkerJob).where(WorkerJob.worker_id == w.id, WorkerJob.status == "running")
    )
    return {
        "id": w.id,
        "name": w.name,
        "active": w.active,
        "online": remote.online(w),
        "kinds": w.kinds,
        "max_batch": w.max_batch,
        "max_live": w.max_live,
        "capabilities": w.capabilities,
        "last_seen": w.last_seen.isoformat() if w.last_seen else None,
        "running_jobs": running,
    }


@router.post("/admin/workers")
def create_worker(body: WorkerCreate, user: User = Depends(superuser), db: Session = Depends(get_db)):
    """Da de alta un worker. El token se muestra UNA vez: en la base solo queda su huella."""
    if db.scalar(select(GpuWorker.id).where(GpuWorker.name == body.name)):
        raise HTTPException(409, "Ya hay un worker con ese nombre")
    token = remote.new_token()
    w = GpuWorker(name=body.name, token_hash=remote.hash_token(token), created_by=user.id)
    db.add(w)
    db.add(AuditLog(user_id=user.id, action="worker.create", target=body.name))
    db.commit()
    return {**_out(db, w), "token": token}


@router.get("/admin/workers")
def list_workers(user: User = Depends(superuser), db: Session = Depends(get_db)):
    remote.reap(db)
    return {
        "mode": settings.gpu_mode,
        "workers": [_out(db, w) for w in db.scalars(select(GpuWorker).order_by(GpuWorker.name))],
    }


@router.delete("/admin/workers/{worker_id}")
def revoke_worker(worker_id: int, user: User = Depends(superuser), db: Session = Depends(get_db)):
    w = db.get(GpuWorker, worker_id)
    if not w:
        raise HTTPException(404, "Worker no encontrado")
    w.active = False  # su token deja de valer; sus trabajos en curso se dan por perdidos
    db.add(AuditLog(user_id=user.id, action="worker.revoke", target=w.name))
    db.commit()
    remote.reap(db)
    return {"ok": True}


# ---------------------------------------------------------------- protocolo del worker
def current_worker(authorization: str = Header(""), db: Session = Depends(get_db)) -> GpuWorker:
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    w = db.scalar(select(GpuWorker).where(GpuWorker.token_hash == remote.hash_token(token))) if token else None
    if w is None or not w.active:
        raise HTTPException(401, "Token de worker inválido o revocado")
    w.last_seen = _now()
    db.commit()
    return w


class RegisterIn(BaseModel):
    name: str = ""
    kinds: list[str] = []
    max_batch: int = Field(1, ge=0, le=16)
    max_live: int = Field(1, ge=0, le=16)
    capabilities: dict = {}
    check_only: bool = False


@router.post("/worker/register")
def register(body: RegisterIn, w: GpuWorker = Depends(current_worker), db: Session = Depends(get_db)):
    if body.check_only:  # diagnóstico del worker: solo comprueba que el token vale
        return {"worker_id": w.id}
    w.kinds = [k for k in body.kinds if k in KINDS]
    w.max_batch, w.max_live, w.capabilities = body.max_batch, body.max_live, body.capabilities
    # al registrarse el worker acaba de arrancar: lo que figuraba en curso se perdió con su reinicio
    for j in db.scalars(select(WorkerJob).where(WorkerJob.worker_id == w.id, WorkerJob.status == "running")):
        j.status, j.finished_at, j.error = "failed", _now(), "el worker se reinició"
    db.commit()
    return {"worker_id": w.id}


class HeartbeatIn(BaseModel):
    running_jobs: list[int] = []
    free_batch: int = 0
    free_live: int = 0


@router.post("/worker/heartbeat")
def heartbeat(body: HeartbeatIn, w: GpuWorker = Depends(current_worker), db: Session = Depends(get_db)):
    mine = db.scalars(select(WorkerJob).where(WorkerJob.worker_id == w.id, WorkerJob.status == "running")).all()
    for j in mine:  # el worker ya no conoce un trabajo que la API creía en curso (no es un reinicio): se da por perdido
        if j.id not in body.running_jobs and j.claimed_at and (_now() - j.claimed_at).total_seconds() > LOST_GRACE:
            j.status, j.finished_at, j.error = "failed", _now(), "el worker perdió el trabajo"
    db.commit()
    return {"cancel_jobs": [j.id for j in mine if j.cancel and j.status == "running"]}


class ClaimIn(BaseModel):
    kinds: list[str]
    wait: float = Field(0, ge=0, le=60)


@router.post("/worker/jobs/claim")
def claim(body: ClaimIn, w: GpuWorker = Depends(current_worker)):
    """Entrega un trabajo; si no hay, retiene la petición hasta `wait` segundos (sondeo largo)."""
    kinds = [k for k in body.kinds if k in KINDS]
    deadline = time.time() + min(body.wait, 25)
    while True:
        with SessionLocal() as db:  # una sesión por vuelta: no se retiene una transacción mientras se espera
            worker = db.get(GpuWorker, w.id)
            if worker is None or not worker.active:  # revocado o borrado mientras esperaba
                raise HTTPException(401, "Token de worker inválido o revocado")
            worker.last_seen = _now()
            job = remote.claim(db, worker, kinds) if kinds else None
            if job:
                payload = {k: v for k, v in job.payload.items() if k != "_prefer"}
                return {"id": job.id, "kind": job.kind, "payload": payload}
            db.commit()
        if time.time() >= deadline:
            return Response(status_code=204)
        time.sleep(0.5)


def _my_job(db: Session, w: GpuWorker, job_id: int) -> WorkerJob:
    j = db.get(WorkerJob, job_id)
    if not j or j.worker_id != w.id:
        raise HTTPException(404, "Trabajo no encontrado")
    return j


@router.post("/worker/jobs/{job_id}/progress")
def progress(job_id: int, body: dict, w: GpuWorker = Depends(current_worker), db: Session = Depends(get_db)):
    j = _my_job(db, w, job_id)
    if j.status == "running":
        log = body.pop("log", None)
        j.progress = {**(j.progress or {}), **body}
        if log is not None:
            j.log = log[-LOG_CAP:]
        db.commit()
    return {"cancel": j.cancel}


class FinishIn(BaseModel):
    status: str = Field(pattern="^(done|failed|cancelled)$")
    result: dict | None = None
    error: str = ""
    log: str = ""


@router.post("/worker/jobs/{job_id}/finish")
def finish(job_id: int, body: FinishIn, w: GpuWorker = Depends(current_worker), db: Session = Depends(get_db)):
    j = _my_job(db, w, job_id)
    if j.status not in remote.DONE:  # si la API ya lo dio por perdido, el cierre del worker no lo cambia
        j.status, j.result, j.error = body.status, body.result, body.error or None
        j.log, j.finished_at = body.log[-LOG_CAP:], _now()
        db.commit()
    return {"ok": True}
