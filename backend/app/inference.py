"""Predicciones de un modelo entrenado, para evaluarlo (proceso aparte con PyTorch; ver workers/predict_yolo.py)."""

import json
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .evaluation import Pred
from .models import Frame, Model, Project


def predict_paths(m: Model, paths: list[str]) -> dict[str, list[Pred]]:
    """Corre el modelo sobre archivos de imagen (en un proceso aparte) y devuelve sus cajas por ruta."""
    with tempfile.TemporaryDirectory() as tmp:
        lst, out = Path(tmp) / "images.json", Path(tmp) / "pred.json"
        lst.write_text(json.dumps(paths))
        cmd = [
            settings.train_python,
            str(settings.predict_script),
            "--weights",
            m.path,
            "--images",
            str(lst),
            "--out",
            str(out),
            "--imgsz",
            str(m.params.get("imgsz", 1280)),
            "--device",
            settings.device,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError("falló la inferencia: " + (r.stderr or r.stdout)[-400:])
        raw = json.loads(out.read_text())
    cls = m.classes
    return {
        path: [
            Pred((round(x), round(y), round(w), round(h)), cls[k], s)
            for x, y, w, h, s, k in raw.get(path, [])
            if k < len(cls)
        ]
        for path in paths
    }


def model_predictions(db: Session, project: Project, model_id: int, frame_ids: list[int]) -> dict[int, list[Pred]]:
    m = db.get(Model, model_id)
    if not m or m.project_id != project.id:
        raise ValueError(f"el modelo {model_id} no existe en este proyecto")
    media = Path(settings.media_root).resolve()
    frames = {f.id: str(media / f.image_path) for f in db.scalars(select(Frame).where(Frame.id.in_(frame_ids)))}
    by_path = predict_paths(m, list(frames.values()))
    return {fid: by_path[path] for fid, path in frames.items()}
