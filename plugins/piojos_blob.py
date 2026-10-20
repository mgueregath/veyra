"""Detector de apariencia del proyecto de piojos (cv-residue-diff/src/lice_motion/appearance.py).

Se carga solo si el proyecto lo declara en su configuración. Necesita la variable LICE_MOTION_SRC apuntando a la
carpeta `src` de cv-residue-diff.
"""

import os
import sys

import cv2


def propose(bgr, k: int = 8, min_score: float = 4.0):
    src = os.environ.get("LICE_MOTION_SRC")
    if not src:
        return []
    if src not in sys.path:
        sys.path.insert(0, src)
    from lice_motion import appearance

    h, w = bgr.shape[:2]
    sm = cv2.resize(bgr, (960, round(h * 960 / w)), interpolation=cv2.INTER_AREA)
    sc = w / 960
    return [
        {"bbox": [round(c * sc) for c in bb], "score": round(float(v), 2), "sources": ["blob"]}
        for _, _, v, bb in appearance.blob_proposals(sm, k=k, min_score=min_score)
    ]
