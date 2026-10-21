import os
from pathlib import Path

import cv2
import pytest
from conftest import login

from app.models import Project

SAMPLE = Path("/home/uach/DEV/lice/cv-residue-diff/labeling/VID_20260911_172306716/f001227.jpg")


def test_plugin_runs_without_the_external_repo(monkeypatch):
    """La copia de appearance.py va dentro de Veyra: no hace falta LICE_MOTION_SRC (en producción no existe)."""
    if not SAMPLE.is_file():
        pytest.skip("imagen de muestra no disponible")
    monkeypatch.delenv("LICE_MOTION_SRC", raising=False)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from plugins import piojos_blob

    boxes = piojos_blob.propose(cv2.imread(str(SAMPLE)))
    assert boxes and all(b["sources"] == ["blob"] and len(b["bbox"]) == 4 and b["score"] >= 4.0 for b in boxes)


def test_snapshot_preannotates_with_the_project_detector(client, vworld, db, media, monkeypatch):
    """Al guardar una instantánea, las cajas del detector del proyecto quedan como propuestas de esa imagen."""
    monkeypatch.delenv("LICE_MOTION_SRC", raising=False)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from plugins.lice_motion import appearance

    monkeypatch.setattr(appearance, "blob_proposals", lambda bgr, k=8, min_score=4.0: [(0, 0, 6.5, (10, 20, 30, 40))])
    p = db.get(Project, vworld["project"].id)
    p.config = {**p.config, "proposer": {"module": "plugins.piojos_blob", "fn": "propose"}}
    db.commit()
    src = vworld["frames"][0]
    login(client, "ana")
    pts = client.get(f"/api/videos/{vworld['video'].id}/pts").json()["pts"]
    r = client.post(f"/api/frames/{src.id}/snapshot", json={"t": pts[40]}).json()
    props = client.get(f"/api/frames/{r['id']}").json()["proposals"]
    assert len(props) == 1 and props[0]["sources"] == ["blob"] and props[0]["score"] == 6.5
    assert props[0]["bbox"] == [2, 3, 5, 7]  # (10, 20, 30, 40) de la copia a 960 px, reescalado al ancho real de 160 px
    assert os.environ.get("LICE_MOTION_SRC") is None
