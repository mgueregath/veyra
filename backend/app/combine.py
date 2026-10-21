"""Combinación booleana de detecciones de varias fuentes. Lógica pura (sin base de datos).

- OR (unión): todas las detecciones de todas las fuentes; las que se solapan (IoU ≥ umbral) se funden en una.
- AND (coincidencia): solo las detecciones que TODAS las fuentes confirman (una caja de cada una, con IoU ≥ umbral).

Las cajas son (x, y, w, h). Dos cajas solo se funden o coinciden si sus clases son compatibles: iguales, o alguna sin
clase ("") (los detectores de apariencia y movimiento no distinguen clases). La clase resultante es la de la
detección con puntaje más alto que tenga una. El puntaje combinado es el máximo, la media o el mínimo de los fundidos.
"""

from collections.abc import Callable

from .consensus import iou
from .evaluation import Pred

SCORE = {"max": max, "min": min, "mean": lambda xs: sum(xs) / len(xs)}


def compatible(a: str, b: str) -> bool:
    return not a or not b or a == b


def _merge(group: list[Pred], score: Callable) -> Pred:
    """Una caja que resume al grupo: promedio ponderado por puntaje; clase de la más segura que tenga una."""
    w = [max(p.score, 1e-6) for p in group]
    tot = sum(w)
    box = tuple(round(sum(p.bbox[i] * wi for p, wi in zip(group, w, strict=True)) / tot) for i in range(4))
    label = next((p.label for p in sorted(group, key=lambda p: -p.score) if p.label), "")
    return Pred((box[0], box[1], max(1, box[2]), max(1, box[3])), label, float(score([p.score for p in group])))


def combine_or(inputs: list[list[Pred]], thr: float = 0.3, score: str = "max") -> list[Pred]:
    """Unión: agrupa por solape (de mayor a menor puntaje) y funde cada grupo en una detección."""
    allp = sorted((p for lst in inputs for p in lst), key=lambda p: -p.score)
    groups: list[list[Pred]] = []
    for p in allp:
        for g in groups:
            if compatible(g[0].label, p.label) and iou(g[0].bbox, p.bbox) >= thr:
                g.append(p)
                break
        else:
            groups.append([p])
    return sorted((_merge(g, SCORE[score]) for g in groups), key=lambda p: -p.score)


def combine_and(inputs: list[list[Pred]], thr: float = 0.3, score: str = "mean") -> list[Pred]:
    """Coincidencia: sale solo lo que cada fuente confirma con una caja suya (cada caja se usa una sola vez)."""
    if not inputs:
        return []
    if len(inputs) == 1:
        return list(inputs[0])
    used = [set() for _ in inputs]
    out = []
    for i, anchor in sorted(((i, p) for i, lst in enumerate(inputs) for p in lst), key=lambda x: -x[1].score):
        if id(anchor) in used[i]:
            continue
        group, picks = [anchor], [(i, anchor)]
        for j, lst in enumerate(inputs):
            if j == i:
                continue
            cands = [
                q
                for q in lst
                if id(q) not in used[j] and compatible(anchor.label, q.label) and iou(anchor.bbox, q.bbox) >= thr
            ]
            if not cands:
                break
            q = max(cands, key=lambda q: iou(anchor.bbox, q.bbox))
            group.append(q)
            picks.append((j, q))
        else:
            for j, q in picks:
                used[j].add(id(q))
            out.append(_merge(group, SCORE[score]))
    return sorted(out, key=lambda p: -p.score)


def combine(op: str, inputs: list[list[Pred]], thr: float = 0.3, score: str | None = None) -> list[Pred]:
    if op == "or":
        return combine_or(inputs, thr, score or "max")
    if op == "and":
        return combine_and(inputs, thr, score or "mean")
    raise ValueError(f"operación desconocida: {op}")
