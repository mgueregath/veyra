"""Evaluación de detectores sobre un conjunto de imágenes con etiquetas finales. Lógica pura (sin base de datos).

Convenciones: las cajas son (x, y, w, h); una predicción acierta si su IoU con una caja real de la MISMA clase es
≥ umbral (cada caja real se empareja una sola vez, de mayor a menor puntaje). El AP es el área bajo la curva
precisión-recall con la envolvente monótona (todos los puntos). Las imágenes sin cajas reales cuentan: cualquier
predicción en ellas es falso positivo.
"""

from dataclasses import dataclass

from .consensus import iou

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Pred:
    bbox: Box
    label: str
    score: float


def _match(gt: dict[int, list[Box]], preds: list[tuple[int, Pred]], thr: float) -> list[tuple[int, Pred, bool]]:
    """Recorre las predicciones por puntaje descendente y marca cuáles aciertan (TP) o no (FP)."""
    used: dict[int, set[int]] = {}
    out = []
    for fid, p in sorted(preds, key=lambda x: -x[1].score):
        best, bi = 0.0, -1
        for i, g in enumerate(gt.get(fid, [])):
            if i in used.get(fid, set()):
                continue
            v = iou(p.bbox, g)
            if v > best:
                best, bi = v, i
        ok = bi >= 0 and best >= thr
        if ok:
            used.setdefault(fid, set()).add(bi)
        out.append((fid, p, ok))
    return out


def average_precision(tp_flags: list[bool], n_gt: int) -> float:
    """AP con envolvente monótona; `tp_flags` ya ordenado por puntaje descendente."""
    if n_gt == 0:
        return float("nan")
    tp = fp = 0
    rec, prec = [0.0], [1.0]
    for ok in tp_flags:
        tp += ok
        fp += not ok
        rec.append(tp / n_gt)
        prec.append(tp / (tp + fp))
    for i in range(len(prec) - 2, -1, -1):
        prec[i] = max(prec[i], prec[i + 1])
    return sum((rec[i] - rec[i - 1]) * prec[i] for i in range(1, len(rec)))


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def evaluate(
    gt: dict[int, list[tuple[Box, str]]],
    preds: dict[int, list[Pred]],
    classes: list[str],
    videos: dict[int, str] | None = None,
    iou_thr: float = 0.5,
    score_thr: float | None = None,
    max_examples: int = 12,
) -> dict:
    """Métricas globales y por clase, conteo por video y ejemplos de errores.

    `gt`: id de imagen → [(caja, clase)] (solo hace falta incluir las clases positivas). `score_thr`: umbral de uso;
    si es None se elige el que da el mejor F1 en este mismo conjunto (se informa cuál, para no confundirlo con un
    umbral fijo).
    """
    per_class, tp_all, fp_all, fn_all = {}, 0, 0, 0
    all_matches: list[tuple[int, Pred, bool]] = []
    gt_by_class = {c: {fid: [b for b, lb in boxes if lb == c] for fid, boxes in gt.items()} for c in classes}
    aps = []
    for c in classes:
        cp = [(fid, p) for fid, lst in preds.items() for p in lst if p.label == c]
        m = _match(gt_by_class[c], cp, iou_thr)
        n_gt = sum(len(v) for v in gt_by_class[c].values())
        ap = average_precision([ok for _, _, ok in m], n_gt)
        per_class[c] = {"gt": n_gt, "predictions": len(cp), "ap": None if ap != ap else ap}
        if ap == ap:
            aps.append(ap)
        all_matches += m
    # umbral de uso
    thr = score_thr
    chosen = "fijo"
    n_gt_total = sum(c["gt"] for c in per_class.values())
    if thr is None:
        chosen = "mejor F1 en este conjunto"
        best, thr = -1.0, 0.0
        for cand in sorted({p.score for _, p, _ in all_matches}):
            tp = sum(1 for _, p, ok in all_matches if ok and p.score >= cand)
            fp = sum(1 for _, p, ok in all_matches if not ok and p.score >= cand)
            f = prf(tp, fp, n_gt_total - tp)["f1"]
            if f > best:
                best, thr = f, cand
    kept = [(fid, p, ok) for fid, p, ok in all_matches if p.score >= thr]
    for c in classes:
        tp = sum(1 for _, p, ok in kept if ok and p.label == c)
        fp = sum(1 for _, p, ok in kept if not ok and p.label == c)
        per_class[c].update(prf(tp, fp, per_class[c]["gt"] - tp))
        tp_all, fp_all, fn_all = tp_all + tp, fp_all + fp, fn_all + per_class[c]["gt"] - tp
    # por video: cuántos hay de verdad y cuántos detecta (el uso real es CONTAR)
    by_video: dict[str, dict] = {}
    for fid in set(gt) | set(preds):
        v = (videos or {}).get(fid, "?")
        d = by_video.setdefault(v, {"video": v, "frames": 0, "gt": 0, "pred": 0})
        d["frames"] += 1
        d["gt"] += sum(len(x) for x in (gt_by_class[c].get(fid, []) for c in classes))
    for fid, _p, _ in kept:
        by_video.setdefault(
            (videos or {}).get(fid, "?"), {"video": (videos or {}).get(fid, "?"), "frames": 0, "gt": 0, "pred": 0}
        )["pred"] += 1
    vids = sorted(by_video.values(), key=lambda d: d["video"])
    mae = sum(abs(d["gt"] - d["pred"]) for d in vids) / len(vids) if vids else None
    # ejemplos de errores: los falsos positivos más seguros y los falsos negativos
    fps = sorted([(fid, p) for fid, p, ok in kept if not ok], key=lambda x: -x[1].score)[:max_examples]
    matched_gt = {
        (fid, g)
        for fid, p, ok in all_matches
        if ok and p.score >= thr
        for g in [_gt_of(gt_by_class[p.label], fid, p, iou_thr)]
    }
    fns = []
    for c in classes:
        for fid, boxes in gt_by_class[c].items():
            for g in boxes:
                if (fid, g) not in matched_gt:
                    fns.append({"frame_id": fid, "bbox": list(g), "label": c})
    micro = prf(tp_all, fp_all, fn_all)
    return {
        "iou": iou_thr,
        "score_threshold": thr,
        "threshold_choice": chosen,
        "map": sum(aps) / len(aps) if aps else None,
        "micro": micro,
        "per_class": per_class,
        "by_video": vids,
        "count_mae": mae,
        "n_frames": len(set(gt) | set(preds)),
        "examples": {
            "false_positives": [
                {"frame_id": fid, "bbox": list(p.bbox), "label": p.label, "score": p.score} for fid, p in fps
            ],
            "false_negatives": fns[:max_examples],
        },
    }


def _gt_of(gt_c: dict[int, list[Box]], fid: int, p: Pred, thr: float) -> Box:
    """La caja real con la que aciertó la predicción `p` (la de mayor IoU de su imagen)."""
    return max(gt_c.get(fid, []), key=lambda g: iou(p.bbox, g))
