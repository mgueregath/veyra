"""Detectores que se pueden evaluar: las líneas base (propuestas guardadas) y, más adelante, los modelos entrenados."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .evaluation import Pred
from .models import Project, Proposal
from .workflow import class_sets

BASELINES = {"appearance": "blob", "motion": "motion"}  # nombre → fuente de las propuestas


def parse_spec(spec: str) -> tuple[str, str]:
    kind, _, name = spec.partition(":")
    if kind not in ("baseline", "model", "flow") or not name:
        raise ValueError("detector inválido: use baseline:appearance, baseline:motion, model:<id> o flow:<id>")
    return kind, name


def baseline_predictions(db: Session, project: Project, frame_ids: list[int], name: str) -> dict[int, list[Pred]]:
    """Las propuestas de una fuente, como predicciones de la primera clase positiva.

    Se incluyen TODAS (sin umbral de puntaje) para poder trazar la curva precisión-recall; el umbral de uso se aplica
    al evaluar. Ojo: son además la fuente que vieron las personas al etiquetar, así que miden el detector, no el sesgo.
    """
    if name not in BASELINES:
        raise ValueError(f"línea base desconocida: {name} (opciones: {sorted(BASELINES)})")
    positive, _ = class_sets(project)
    if not positive:
        raise ValueError("el proyecto no tiene clases positivas")
    src = BASELINES[name]
    out: dict[int, list[Pred]] = {fid: [] for fid in frame_ids}
    if not frame_ids:
        return out
    for p in db.scalars(select(Proposal).where(Proposal.frame_id.in_(frame_ids))):
        if src in (p.sources or []):
            out[p.frame_id].append(Pred((p.x, p.y, p.w, p.h), positive[0], float(p.score or 0.0)))
    return out
