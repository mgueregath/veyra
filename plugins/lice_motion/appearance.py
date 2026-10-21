"""Apariencia por croma: los piojos (cuerpo pálido-amarillo, cabeza marrón) se apartan del color neutro de la lana.

Se usa para (1) sembrar candidatos que luego se activan con un poco de movimiento y (2) dar a cada
candidato el bbox del insecto completo, no solo de la parte que se mueve.
"""
import cv2
import numpy as np


def chroma_map(bgr_small, use_l=False):
    """Distancia Lab al fondo local. El fondo (mediana) se calcula a media resolución porque es suave;
    la distancia, a la resolución de trabajo, para no diluir el pico de un objeto pequeño."""
    h, w = bgr_small.shape[:2]
    lab = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2LAB)
    half = cv2.resize(lab, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.medianBlur(half, 31), (w, h), interpolation=cv2.INTER_LINEAR)
    diff = lab.astype(np.float32) - bg.astype(np.float32)
    d = np.linalg.norm(diff if use_l else diff[..., 1:], axis=2)   # sin L: las motas negras no cuentan
    return cv2.GaussianBlur(d, (0, 0), 1.0)


def grow_bbox(chroma, x, y, frac=0.3, floor=4.0, win=110, pad=0.1, max_side=170):
    """BBox (x, y, w, h) de la mancha de croma que contiene a (x, y); None si no hay o es demasiado grande."""
    H, W = chroma.shape
    x0, x1, y0, y1 = max(x - win, 0), min(x + win + 1, W), max(y - win, 0), min(y + win + 1, H)
    sub = chroma[y0:y1, x0:x1]
    peak = float(sub[max(y - y0 - 6, 0):y - y0 + 7, max(x - x0 - 6, 0):x - x0 + 7].max())
    m = (sub > max(floor, frac * peak)).astype(np.uint8)
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    k = lab[y - y0, x - x0]
    if k == 0:
        return None
    bx, by, bw, bh, _ = stats[k]
    if max(bw, bh) > max_side:
        return None
    px, py = int(bw * pad), int(bh * pad)
    nx0, ny0 = max(bx + x0 - px, 0), max(by + y0 - py, 0)
    nx1, ny1 = min(bx + x0 + bw + px, W), min(by + y0 + bh + py, H)
    return nx0, ny0, nx1 - nx0, ny1 - ny0


def union(a, b):
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return x0, y0, x1 - x0, y1 - y0


def lab_L(bgr_small):
    return cv2.cvtColor(bgr_small, cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32)


def box_stats(L, chroma, bbox):
    """(lmin, ring, chroma_max) del bbox: luminosidad mínima (p3) dentro, mediana en el anillo, croma máximo."""
    H, W = L.shape
    bx, by, bw, bh = bbox
    x0, y0, x1, y1 = bx, by, min(bx + bw, W), min(by + bh, H)
    ins = L[y0:y1, x0:x1]
    ex0, ey0, ex1, ey1 = max(x0 - bw // 2, 0), max(y0 - bh // 2, 0), min(x1 + bw // 2, W), min(y1 + bh // 2, H)
    big = L[ey0:ey1, ex0:ex1]
    m = np.ones(big.shape, bool)
    m[y0 - ey0:y1 - ey0, x0 - ex0:x1 - ex0] = False
    ring = float(np.median(big[m])) if m.any() else float(np.median(ins))
    return float(np.percentile(ins, 3)), ring, float(chroma[y0:y1, x0:x1].max())


def blob_proposals(bgr_small, k=10, sigma=12, nms=45, width=540, min_score=0.0):
    """Proponente de apariencia (sin movimiento): manchas con tinte amarillo-rojizo (b*, a*) y lisas (poca textura).

    Se calcula a ancho fijo `width` para que sigma signifique lo mismo en cualquier resolución.
    Devuelve [(x, y, score, (bx, by, bw, bh))] en las coordenadas de la imagen de entrada, solo con score >= min_score.
    Con etiquetas de 27 frames: score >= 6 da ~62 % de precisión y ~97 % de recall (2 cajas/frame).
    """
    h0, w0 = bgr_small.shape[:2]
    s = width / w0
    sm = cv2.resize(bgr_small, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(sm, cv2.COLOR_BGR2LAB).astype(np.float32)

    def dog(c):
        return cv2.GaussianBlur(c, (0, 0), sigma) - cv2.GaussianBlur(c, (0, 0), sigma * 1.6)

    g = lab[..., 0]
    tex = cv2.GaussianBlur(np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)), (0, 0), 10)

    def z(m):
        return (m - m.mean()) / (m.std() + 1e-6)

    score = z(dog(lab[..., 2])) + z(dog(lab[..., 1])) - 0.5 * z(tex)
    mx = cv2.dilate(score, np.ones((2 * nms + 1, 2 * nms + 1), np.uint8))
    ys, xs = np.nonzero(score >= mx)
    order = np.argsort(-score[ys, xs])
    order = [i for i in order if score[ys[i], xs[i]] >= min_score][:k]
    H, W = score.shape
    out = []
    for i in order:
        x, y, v = int(xs[i]), int(ys[i]), float(score[ys[i], xs[i]])
        m = (score > 0.5 * v).astype(np.uint8)
        _, lab_cc = cv2.connectedComponents(m)
        comp = lab_cc == lab_cc[y, x]
        yy, xx = np.nonzero(comp)
        x0, x1, y0, y1 = xx.min(), xx.max() + 1, yy.min(), yy.max() + 1
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        bw, bh = max((x1 - x0) * 1.2, 40), max((y1 - y0) * 1.2, 40)
        bw, bh = min(bw, 4 * sigma * 3), min(bh, 4 * sigma * 3)
        bx, by = max(cx - bw / 2, 0), max(cy - bh / 2, 0)
        bw, bh = min(bw, W - bx), min(bh, H - by)
        out.append((x / s, y / s, v, (bx / s, by / s, bw / s, bh / s)))
    return out
