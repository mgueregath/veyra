"""Procedimientos del espacio de trabajo de un proyecto.

Son funciones de visión del repositorio git del espacio (descritas en su `veyra.yaml`) que un worker ejecuta en un
contenedor aislado (sin red, con el repositorio y los datos en solo lectura). Se usan como una fuente más de los flujos.
"""

import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from . import remote
from .config import settings
from .evaluation import Pred
from .models import Project


class ProcedureError(RuntimeError):
    pass


def available(db: Session) -> bool:
    return bool(remote.online_workers(db, "procedure"))


def list_procedures(db: Session, project: Project) -> dict:
    """Procedimientos que declara el espacio del proyecto (última versión confirmada)."""
    if not available(db):
        return {"available": False, "procedures": [], "ref": None, "error": None}
    worker = remote.pick_worker(db, "procedure", force=True)
    jid = remote.submit("procedure", {"action": "list", "slug": project.slug}, worker.id)
    snap = remote.wait(jid, queue_timeout=45, poll=0.4)
    if snap["status"] != "done":
        return {"available": True, "procedures": [], "ref": None, "error": snap["error"] or snap["log"][-300:]}
    res = snap["result"] or {}
    return {
        "available": True,
        "procedures": res.get("procedures", []),
        "ref": res.get("ref"),
        "error": res.get("error"),
    }


def run_procedure(
    db: Session, project: Project, name: str, ref: str | None, params: dict, paths: list[str]
) -> tuple[dict[str, list[Pred]], str]:
    """Corre el procedimiento sobre las imágenes: detecciones por ruta y el commit exacto que se ejecutó."""
    try:
        worker = remote.pick_worker(db, "procedure", force=True)
    except remote.RemoteUnavailable:
        raise ProcedureError(
            "No hay ningún worker en línea que ejecute procedimientos del espacio de trabajo"
        ) from None
    out = Path(settings.runs_root) / "_flows" / uuid.uuid4().hex
    out.mkdir(parents=True, exist_ok=True)
    try:
        jid = remote.submit(
            "procedure",
            {
                "action": "run",
                "slug": project.slug,
                "name": name,
                "ref": ref or "HEAD",
                "params": params,
                "images": paths,
                "out_dir": str(out),
            },
            worker.id,
        )
        snap = remote.wait(jid, queue_timeout=settings.worker_queue_timeout, poll=0.5)
        if snap["status"] != "done":
            raise ProcedureError(f"falló el procedimiento «{name}»: " + (snap["error"] or snap["log"][-500:]))
        raw = json.loads((out / "result.json").read_text())
    finally:
        shutil.rmtree(out, ignore_errors=True)
    results = {
        path: [
            Pred(tuple(int(round(v)) for v in d["bbox"]), str(d.get("label") or ""), float(d.get("score", 1.0)))
            for d in dets
        ]
        for path, dets in raw["results"].items()
    }
    return {p: results.get(p, []) for p in paths}, raw.get("ref", "")
