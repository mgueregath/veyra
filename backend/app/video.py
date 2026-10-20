"""Utilidades de video basadas en ffmpeg/ffprobe. Se trabaja siempre sobre el ORIGINAL (sin copias ni recortes)."""

import bisect
import json
import subprocess

import cv2
import numpy as np


def video_info(path: str) -> dict:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    d = json.loads(r.stdout)
    s = d["streams"][0]

    def fr(x: str) -> float:
        n, m = (float(v) for v in x.split("/"))
        return n / m if m else 0.0

    # avg_frame_rate es el promedio real; r_frame_rate puede ser la base de tiempo (p. ej. 90000) en tasa variable
    fps = fr(s["avg_frame_rate"]) or fr(s["r_frame_rate"]) or 30.0
    return {"fps": round(fps, 4), "dur": float(d["format"]["duration"])}


def video_pts(path: str) -> list[float]:
    """Marca de tiempo real de cada fotograma (la del contenedor), ordenada.

    En videos de tasa variable los fotogramas NO están a intervalos de 1/fps.
    """
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(round(float(x), 6) for x in r.stdout.split() if x.strip() not in ("", "N/A"))


def nearest_frame(pts: list[float], t: float, tol: float = 0.002) -> int | None:
    """Índice del fotograma cuyo PTS es `t` (con tolerancia), o None si `t` no es un fotograma."""
    i = bisect.bisect_left(pts, t)
    cands = [j for j in (i - 1, i) if 0 <= j < len(pts)]
    idx = min(cands, key=lambda j: abs(pts[j] - t), default=None)
    return idx if idx is not None and abs(pts[idx] - t) <= tol else None


def extract_exact_frame(path: str, pts: list[float], idx: int) -> np.ndarray | None:
    """Fotograma `idx` (orden de presentación) del original, como BGR.

    Se busca la mitad entre el PTS anterior y el del fotograma para que ffmpeg entregue exactamente ese. La matriz
    BT.601 forzada reproduce la conversión de color de OpenCV (la de las demás fotos): error 0.7 frente a 3-20.
    """
    ss = (pts[idx - 1] + pts[idx]) / 2 if idx > 0 else 0.0
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{ss:.4f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=in_color_matrix=bt601",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        capture_output=True,
    ).stdout
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None
