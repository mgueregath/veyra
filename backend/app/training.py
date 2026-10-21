"""Orquesta los entrenamientos: cola de un solo trabajo a la vez (una GPU), proceso aparte, registro y cancelación."""

import datetime as dt
import json
import os
import queue
import re
import signal
import subprocess
import threading
from pathlib import Path

from sqlalchemy import select

from . import remote
from .config import settings
from .datasets import export_version
from .db import SessionLocal
from .models import DatasetVersion, Model, Project, Run
from .workflow import class_sets

ARCHS = {"yolov8n": "yolov8n.pt", "yolov8s": "yolov8s.pt", "yolov8m": "yolov8m.pt"}  # Ultralytics 8.2: sin YOLO11
LOG_CAP = 60_000
_q: "queue.Queue[int]" = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_procs: dict[int, subprocess.Popen] = {}
_cancel: set[int] = set()


def resolve_weights(arch: str) -> str:
    """Pesos preentrenados: se busca el archivo en las carpetas configuradas; si no está, Ultralytics lo descarga."""
    fname = ARCHS[arch]
    for d in filter(None, settings.pretrained_dirs.split(":")):
        if (Path(d) / fname).is_file():
            return str(Path(d) / fname)
    return fname


def enqueue(run_id: int) -> None:
    global _thread
    _q.put(run_id)
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="labelhub-train", daemon=True)
            _thread.start()


def cancel(run_id: int) -> bool:
    _cancel.add(run_id)
    p = _procs.get(run_id)
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True


def recover_interrupted() -> int:
    """Al arrancar, los trabajos que quedaron «en curso» (la API se reinició) se marcan como interrumpidos."""
    with SessionLocal() as db:
        runs = db.scalars(
            select(Run).where(
                Run.status.in_(["queued", "running"]), Run.kind.in_(["training", "preannotation", "sampling"])
            )
        ).all()
        for r in runs:
            r.status, r.log = "failed", (r.log or "") + "\n[interrumpido: la API se reinició]"
            r.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        return len(runs)


def _loop() -> None:
    while True:
        run_id = _q.get()
        try:
            _execute(run_id)
        except Exception as e:  # nunca matar el hilo
            with SessionLocal() as db:
                r = db.get(Run, run_id)
                if r and r.status in ("queued", "running"):
                    r.status, r.log = "failed", (r.log or "") + f"\n[error interno: {e}]"
                    r.finished_at = dt.datetime.now(dt.timezone.utc)
                    db.commit()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", name)


def _execute(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        kind = run.kind if run else None
    if kind == "preannotation":
        from .assisted import execute_preannotation

        execute_preannotation(run_id, _cancel)
    else:
        _execute_training(run_id)


def _execute_training(run_id: int) -> None:
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None or run.status != "queued" or run_id in _cancel:
            if run and run_id in _cancel:
                run.status, run.finished_at = "cancelled", dt.datetime.now(dt.timezone.utc)
                db.commit()
            return
        run.status = "running"
        db.commit()
        project = db.get(Project, run.project_id)
        version = db.get(DatasetVersion, run.dataset_version_id)
        prm = run.params
        work = Path(settings.runs_root) / str(run.id)
        positive, _ = class_sets(project)
        export_version(db, version, project, work / "dataset", "yolo")
        out_weights = Path(settings.models_root) / project.slug / _safe(prm["model_name"]) / "best.pt"
        project_id, created_by, ver_id = project.id, run.created_by, version.id
    args = {
        "data": str(work / "dataset" / "data.yaml"),
        "weights": resolve_weights(prm["arch"]),
        "epochs": prm["epochs"],
        "imgsz": prm["imgsz"],
        "batch": prm["batch"],
        "project": str(work / "ultralytics"),
        "name": "train",
        "device": settings.device,
        "seed": prm.get("seed", 0),
        "out": str(out_weights),
    }
    cmd = [settings.train_python, str(settings.train_script)]
    for k, v in args.items():
        cmd += [f"--{k}", str(v)]
    if run_id in _cancel:  # se canceló mientras se exportaba el dataset: ni siquiera se lanza
        with SessionLocal() as db:
            r = db.get(Run, run_id)
            r.status, r.finished_at = "cancelled", dt.datetime.now(dt.timezone.utc)
            db.commit()
        _cancel.discard(run_id)
        return
    with SessionLocal() as db:
        worker = remote.pick_worker(db, "train")
        worker_id = worker.id if worker else None
    if worker_id is not None:
        log, progress, result, code = _train_remote(run_id, args, prm["arch"], worker_id)
    else:
        log, progress, result, code = _train_local(run_id, cmd)
    with SessionLocal() as db:
        r = db.get(Run, run_id)
        r.log = log[-LOG_CAP:]
        r.finished_at = dt.datetime.now(dt.timezone.utc)
        if run_id in _cancel:
            r.status = "cancelled"
        elif code == 0 and result:
            r.status = "done"
            r.metrics = {"progress": progress, **result}
            m = Model(
                project_id=project_id,
                name=_safe(prm["model_name"]),
                dataset_version_id=ver_id,
                run_id=run_id,
                path=result["weights"],
                arch=prm["arch"],
                classes=positive,
                params=prm,
                metrics={k: v for k, v in result.items() if k != "weights"},
                created_by=created_by,
            )
            db.add(m)
        else:
            r.status = "failed"
            r.log = (r.log or "") + f"\n[el proceso terminó con código {code}]"
        db.commit()
    _cancel.discard(run_id)


def _train_local(run_id: int, cmd: list[str]) -> tuple[str, dict | None, dict | None, int]:
    log, progress, result, last_flush = "", None, None, 0.0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    _procs[run_id] = proc
    if run_id in _cancel:  # la cancelación llegó entre la comprobación y el lanzamiento
        cancel(run_id)

    def flush(final: bool = False) -> None:
        with SessionLocal() as db:
            r = db.get(Run, run_id)
            r.log = log[-LOG_CAP:]
            r.metrics = {**(r.metrics or {}), "progress": progress}
            db.commit()

    import time

    for line in proc.stdout:  # type: ignore[union-attr]
        if line.startswith("PROGRESS "):
            progress = json.loads(line[9:])
        elif line.startswith("RESULT "):
            result = json.loads(line[7:])
        else:
            log += line
        if time.time() - last_flush > 2:
            flush()
            last_flush = time.time()
    code = proc.wait()
    _procs.pop(run_id, None)
    return log, progress, result, code


def _train_remote(run_id: int, args: dict, arch: str, worker_id: int) -> tuple[str, dict | None, dict | None, int]:
    """Lo mismo que en local, pero el proceso corre en un worker; las rutas viven en la carpeta compartida."""
    payload = {k: v for k, v in args.items() if k != "device"}
    payload["weights"] = ARCHS[arch]  # el worker busca los preentrenados en sus propias carpetas
    jid = remote.submit("train", payload, worker_id)

    def on_update(snap: dict) -> None:
        with SessionLocal() as db:
            r = db.get(Run, run_id)
            r.log = snap["log"][-LOG_CAP:]
            r.metrics = {**(r.metrics or {}), "progress": snap["progress"].get("progress")}
            db.commit()

    snap = remote.wait(jid, on_update, lambda: run_id in _cancel)
    if snap["status"] == "done" and snap["result"]:
        return snap["log"], snap["progress"].get("progress"), snap["result"], 0
    log = snap["log"] + (f"\n[{snap['error']}]" if snap["error"] else "")
    return log, snap["progress"].get("progress"), None, 1
