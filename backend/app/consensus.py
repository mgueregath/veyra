"""Lógica pura (sin base de datos) de acuerdo entre anotadores y de reparto por video."""

import hashlib

Box = tuple[int, int, int, int]
LabeledBox = tuple[Box, str]


def iou(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = min(ax2, bx2) - max(ax1, bx1), min(ay2, by2) - max(ay1, by1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def positives(boxes: list[LabeledBox], negative: set[str]) -> list[LabeledBox]:
    """Las cajas de clases negativas («ni piojo ni mosca») son fondo: no cuentan para el acuerdo."""
    return [(bb, lb) for bb, lb in boxes if lb not in negative]


def match(a: list[LabeledBox], b: list[LabeledBox], thr: float = 0.5) -> tuple[int, int, int]:
    """Empareja cajas por IoU (voraz, de mayor a menor) exigiendo la MISMA clase. Devuelve (tp, fp, fn)."""
    pairs = sorted(
        ((iou(x[0], y[0]), i, j) for i, x in enumerate(a) for j, y in enumerate(b) if x[1] == y[1]), reverse=True
    )
    ua, ub, tp = set(), set(), 0
    for v, i, j in pairs:
        if v < thr:
            break
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        tp += 1
    return tp, len(b) - tp, len(a) - tp


def f1(tp: int, fp: int, fn: int) -> float:
    return 1.0 if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)


def agree(a: list[LabeledBox], b: list[LabeledBox], negative: set[str], thr: float = 0.5) -> bool:
    tp, fp, fn = match(positives(a, negative), positives(b, negative), thr)
    return fp == 0 and fn == 0


def pair_f1(a: list[LabeledBox], b: list[LabeledBox], negative: set[str], thr: float = 0.5) -> float:
    return f1(*match(positives(a, negative), positives(b, negative), thr))


def split_for_video(video: str, seed: str, fractions: tuple[float, float, float] = (0.7, 0.15, 0.15)) -> str:
    """Partición (train/val/test) fija por video y semilla: no cambia entre versiones ni mezcla un video en dos."""
    h = int(hashlib.md5(f"{seed}:{video}".encode()).hexdigest()[:8], 16) / 0x100000000
    tr, va, _ = fractions
    return "train" if h < tr else "val" if h < tr + va else "test"
