"""Detector de apariencia del proyecto de piojos (manchas de tinte amarillo-rojizo y lisas).

Se carga solo si el proyecto lo declara en su configuración (`proposer`). Usa la copia incluida en
`plugins/lice_motion/appearance.py`; si existe la variable LICE_MOTION_SRC (carpeta `src` de cv-residue-diff) usa esa.
"""

import os
import sys

import cv2


def _appearance():
    src = os.environ.get("LICE_MOTION_SRC")
    if src:
        if src not in sys.path:
            sys.path.insert(0, src)
        from lice_motion import appearance

        return appearance
    from plugins.lice_motion import appearance

    return appearance


def propose(bgr, k: int = 8, min_score: float = 4.0):
    appearance = _appearance()
    h, w = bgr.shape[:2]
    sm = cv2.resize(bgr, (960, round(h * 960 / w)), interpolation=cv2.INTER_AREA)
    sc = w / 960
    return [
        {"bbox": [round(c * sc) for c in bb], "score": round(float(v), 2), "sources": ["blob"]}
        for _, _, v, bb in appearance.blob_proposals(sm, k=k, min_score=min_score)
    ]
