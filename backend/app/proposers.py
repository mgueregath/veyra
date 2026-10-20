"""Preanotación al guardar instantáneas: cada proyecto puede declarar un detector propio en su configuración.

    "proposer": {"module": "plugins.piojos_blob", "fn": "propose"}

La función recibe la imagen BGR y devuelve una lista de {"bbox": [x, y, w, h], "score": float, "sources": [str]}.
Sin `proposer` las instantáneas se guardan sin cajas propuestas.
"""

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load(config: dict):
    spec = (config or {}).get("proposer")
    if not spec:
        return None
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return getattr(importlib.import_module(spec["module"]), spec.get("fn", "propose"))
