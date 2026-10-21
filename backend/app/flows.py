"""Flujos de detección: modelos, filtros y procedimientos del proyecto combinados con OR (unión) o AND (coincidencia).

Un flujo es un árbol: las hojas son fuentes de detecciones y los grupos las combinan con OR / AND. Ejemplo,
«(modelo 3 OR manchas) AND procedimiento X»:

    {"op": "and", "inputs": [
        {"op": "or", "inputs": [{"source": "model:3"}, {"source": "baseline:appearance", "min_score": 4}]},
        {"source": "procedure:mi_filtro", "ref": "v1"}]}

Fuentes: `model:<id>` (modelo entrenado, en la GPU), `baseline:appearance` (detector de apariencia del
proyecto), `proposals:motion|blob` (propuestas ya guardadas en las imágenes del proyecto) y `procedure:<nombre>`
(función del espacio de trabajo del proyecto, versionada por git).
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Union

import cv2
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import inference, procedures, proposers
from .combine import combine
from .evaluation import Pred
from .models import Model, Project, Proposal

MAX_DEPTH, MAX_LEAVES = 4, 12
SOURCE = re.compile(r"^(model:[0-9]+|baseline:appearance|proposals:(motion|blob)|procedure:[A-Za-z0-9_.\-]{1,64})$")


class FlowError(ValueError):
    pass


class Leaf(BaseModel):
    source: str
    min_score: float = Field(0.0, ge=0)  # en la escala de cada fuente (modelos 0–1; apariencia y movimiento, otra)
    ref: str | None = Field(None, max_length=100)  # procedimientos: etiqueta o commit (por defecto, el último commit)
    params: dict = {}


class Group(BaseModel):
    op: Literal["or", "and"]
    inputs: list[Union["Group", Leaf]] = Field(min_length=1, max_length=MAX_LEAVES)
    iou: float = Field(0.3, gt=0, le=1)  # solape mínimo para fundir (OR) o confirmar (AND) cajas de fuentes distintas
    score: Literal["max", "mean", "min"] | None = None  # por defecto: OR máximo, AND media


Group.model_rebuild()
Node = Union[Group, Leaf]  # noqa: UP007


def parse(raw: dict) -> Node:
    try:
        node: Node = Group.model_validate(raw) if "op" in raw else Leaf.model_validate(raw)
    except ValidationError as e:
        err = e.errors()[0]
        raise FlowError(f"flujo inválido ({'.'.join(str(x) for x in err['loc'])}: {err['msg']})") from None
    return node


def leaves(node: Node) -> list[Leaf]:
    return [node] if isinstance(node, Leaf) else [x for c in node.inputs for x in leaves(c)]


def depth(node: Node) -> int:
    return 1 if isinstance(node, Leaf) else 1 + max(depth(c) for c in node.inputs)


def validate(db: Session, project: Project, raw: dict) -> Node:
    """Interpreta el flujo y comprueba límites y que los modelos existan en el proyecto."""
    node = parse(raw)
    ls = leaves(node)
    if len(ls) > MAX_LEAVES or depth(node) > MAX_DEPTH:
        raise FlowError(f"el flujo es demasiado grande (máximo {MAX_LEAVES} fuentes y {MAX_DEPTH} niveles)")
    for leaf in ls:
        if not SOURCE.match(leaf.source):
            raise FlowError(f"fuente desconocida: {leaf.source}")
        if leaf.source.startswith("model:"):
            m = db.get(Model, int(leaf.source[6:]))
            if not m or m.project_id != project.id:
                raise FlowError(f"el modelo de «{leaf.source}» no existe en este proyecto")
        if leaf.source == "baseline:appearance" and proposers.load(project.config) is None:
            raise FlowError("este proyecto no declara un detector de apariencia (config.proposer)")
    return node


def describe(node: Node, names: dict[str, str] | None = None) -> str:
    """Texto legible: «(Modelo A O manchas) Y filtro»."""
    names = names or {}
    if isinstance(node, Leaf):
        return names.get(node.source, node.source)
    sep = " O " if node.op == "or" else " Y "
    inner = sep.join(describe(c, names) for c in node.inputs)
    return f"({inner})" if len(node.inputs) > 1 else inner


@dataclass
class Item:
    path: str
    frame_id: int | None = None


def _appearance(project: Project, items: list[Item]) -> dict[str, list[Pred]]:
    propose = proposers.load(project.config)
    if propose is None:
        raise FlowError("este proyecto no declara un detector de apariencia")

    def one(it: Item) -> tuple[str, list[Pred]]:
        img = cv2.imread(it.path)
        if img is None:
            return it.path, []
        return it.path, [Pred(tuple(b["bbox"]), "", float(b.get("score", 1.0))) for b in propose(img)]

    with ThreadPoolExecutor(4) as ex:
        return dict(ex.map(one, items))


def _saved(db: Session, which: str, items: list[Item]) -> dict[str, list[Pred]]:
    by_frame = {it.frame_id: it.path for it in items if it.frame_id is not None}
    out: dict[str, list[Pred]] = {it.path: [] for it in items}
    if not by_frame:
        return out
    for p in db.scalars(select(Proposal).where(Proposal.frame_id.in_(list(by_frame)))):
        if which in (p.sources or []):
            out[by_frame[p.frame_id]].append(Pred((p.x, p.y, p.w, p.h), "", float(p.score or 0.0)))
    return out


def _leaf(db: Session, project: Project, leaf: Leaf, items: list[Item]) -> tuple[dict[str, list[Pred]], str]:
    """Detecciones de una fuente sobre todas las imágenes (ya filtradas por puntaje mínimo) y su traza."""
    paths = [i.path for i in items]
    trace = leaf.source
    if leaf.source.startswith("model:"):
        m = db.get(Model, int(leaf.source[6:]))
        res = inference.predict_paths(m, paths)
    elif leaf.source == "baseline:appearance":
        res = _appearance(project, items)
    elif leaf.source.startswith("proposals:"):
        res = _saved(db, leaf.source[10:], items)
    else:
        try:
            res, sha = procedures.run_procedure(db, project, leaf.source[10:], leaf.ref, leaf.params, paths)
        except procedures.ProcedureError as e:
            raise FlowError(str(e)) from None
        trace = f"{leaf.source}@{sha[:8]}" if sha else leaf.source
    return {p: [d for d in ds if d.score >= leaf.min_score] for p, ds in res.items()}, trace


def run(db: Session, project: Project, raw: dict, items: list[Item]) -> tuple[dict[str, list[Pred]], list[str]]:
    """Ejecuta el flujo sobre las imágenes. Devuelve las detecciones por ruta y la traza de lo que se ejecutó."""
    node = validate(db, project, raw)
    cache: dict[str, tuple[dict[str, list[Pred]], str]] = {}
    traces: list[str] = []

    def key(leaf: Leaf) -> str:
        return json.dumps(leaf.model_dump(), sort_keys=True)

    def ev(n: Node) -> dict[str, list[Pred]]:
        if isinstance(n, Leaf):
            k = key(n)
            if k not in cache:
                cache[k] = _leaf(db, project, n, items)
                traces.append(cache[k][1])
            return cache[k][0]
        parts = [ev(c) for c in n.inputs]
        return {i.path: combine(n.op, [p[i.path] for p in parts], n.iou, n.score) for i in items}

    return ev(node), traces


def classes_of(db: Session, project: Project, raw: dict) -> list[str]:
    """Clases que pueden salir del flujo: las de sus modelos o, si no hay, las positivas del proyecto."""
    out: list[str] = []
    for leaf in leaves(parse(raw)):
        if leaf.source.startswith("model:"):
            m = db.get(Model, int(leaf.source[6:]))
            out += [c for c in (m.classes if m else []) if c not in out]
    return out or [c["id"] for c in (project.config or {}).get("classes", []) if not c.get("negative")]
