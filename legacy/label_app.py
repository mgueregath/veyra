#!/usr/bin/env python3
"""App local para etiquetar detecciones: confirmar piojo / no piojo y marcar los que faltan.

    python scripts/label_app.py            # abre http://localhost:8899
Lee labeling/frames.json (build_label_set.py) y guarda cada cambio en labeling/labels.json.

También sirve el VIDEO ORIGINAL (sin copias ni recortes) para el reproductor de la página: /video/<nombre> con soporte de
Range (el navegador salta dentro del archivo sin descargarlo entero) y /api/video_info?name=<nombre> (fps y duración).
Solo se sirven los videos que aparecen en frames.json y existen en --videos (por defecto videos/).

POST /api/snapshot guarda como imagen nueva el fotograma que se ve en el reproductor (ver snapshot_from_video).
"""
import argparse
import bisect
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
HTML_PATH = Path(__file__).with_name("label_app.html")


CHUNK = int(float(os.environ.get("LICE_CHUNK_MB", 4)) * 1024 * 1024)    # tope por respuesta Range: el navegador pide el resto a medida que lo necesita (sin esto precarga cientos de MB)
VINFO = {}                       # nombre -> {fps, dur}
VPTS = {}                        # nombre -> PTS (s) de todos los fotogramas, ordenados


def video_info(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]

    def fr(x):
        n, m = (float(v) for v in x.split("/"))
        return n / m if m else 0.0
    # avg_frame_rate es el promedio real; r_frame_rate puede ser un valor de base de tiempo (p. ej. 90000) en videos de tasa variable
    return dict(fps=round(fr(s["avg_frame_rate"]) or fr(s["r_frame_rate"]) or 30.0, 4), dur=float(d["format"]["duration"]))


def video_pts(path):
    """Marca de tiempo real de cada fotograma (el contenedor). En videos de tasa variable los fotogramas NO están a intervalos de 1/fps."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return sorted(round(float(x), 6) for x in r.stdout.split() if x.strip() not in ("", "N/A"))


def extract_exact_frame(path, ps, idx):
    """Fotograma `idx` (por orden de presentación) del video original, como BGR.

    Se busca la mitad entre el PTS anterior y el del fotograma para que ffmpeg entregue exactamente ese. La matriz BT.601 forzada
    reproduce la conversión de color de OpenCV (la de todas las demás fotos del conjunto): error 0.7 frente a 3-20 con la de ffmpeg."""
    import cv2
    import numpy as np
    ss = (ps[idx - 1] + ps[idx]) / 2 if idx > 0 else 0.0
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss:.4f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=in_color_matrix=bt601",
                          "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True).stdout
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR) if out else None


def snapshot_from_video(base, videos_dir, path, name, t, after_key):
    """Guarda como foto nueva el fotograma del video cuyo PTS es `t`. Devuelve (código, respuesta).

    Idempotente: si ese fotograma ya es una foto del conjunto, no se duplica. La nueva foto se inserta justo después de `after_key`
    (la foto desde la que se está mirando el video), tras las instantáneas manuales que ya tuviera a continuación."""
    import cv2
    sys.path.insert(0, str(ROOT / "src"))
    from lice_motion import appearance
    if name not in VPTS:
        VPTS[name] = video_pts(path)
    ps = VPTS[name]
    i = bisect.bisect_left(ps, t)
    idx = min((j for j in (i - 1, i) if 0 <= j < len(ps)), key=lambda j: abs(ps[j] - t), default=None)
    if idx is None or abs(ps[idx] - t) > 0.002:
        return 400, dict(error="t no coincide con ningún fotograma del video")
    fj = base / "frames.json"
    frames = json.loads(fj.read_text())
    for k, f in enumerate(frames):
        if f["video"] == name and f["frame"] == idx:
            return 200, dict(existing=True, index=k, frame=f)
    bgr = extract_exact_frame(path, ps, idx)
    if bgr is None:
        return 500, dict(error="no se pudo extraer el fotograma")
    h, w = bgr.shape[:2]
    rel = f"{name}/f{idx:06d}.jpg"
    (base / name).mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(base / rel), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    sm = cv2.resize(bgr, (960, round(h * 960 / w)), interpolation=cv2.INTER_AREA)
    sc = w / 960
    boxes = [dict(sources=["blob"], score=round(v, 2), bbox=[round(c * sc) for c in bb])
             for _, _, v, bb in appearance.blob_proposals(sm, k=8, min_score=4.0)]          # preanotado, como en el resto del lote
    g = cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY)
    g2 = cv2.resize(g, (480, round(g.shape[0] * 480 / g.shape[1])))
    rec = dict(video=name, frame=idx, t=round(ps[idx], 3), t_src="indice", image=rel, w=w, h=h, boxes=boxes, stratum="manual",
               sharp=round(float(cv2.Laplacian(g2, cv2.CV_64F).var()), 1), bright=round(float(g.mean()), 1), origin="reproductor")
    pos = len(frames)
    src = next((k for k, f in enumerate(frames) if f"{f['video']}:{f['frame']}" == after_key), None)
    if src is not None:
        pos = src + 1
        while pos < len(frames) and frames[pos].get("stratum") == "manual" and frames[pos]["video"] == name:
            pos += 1
    frames.insert(pos, rec)
    tmp = fj.with_name(fj.name + ".tmp")
    tmp.write_text(json.dumps(frames))
    os.replace(tmp, fj)
    return 200, dict(existing=False, index=pos, frame=rec)


def thumb(base, p, h=168):
    """Miniatura en caché (labeling/thumbs/...) para el carrusel."""
    import cv2
    tp = base / "thumbs" / p.relative_to(base.resolve())
    if not tp.exists():
        im = cv2.imread(str(p))
        im = cv2.resize(im, (max(1, round(im.shape[1] * h / im.shape[0])), h), interpolation=cv2.INTER_AREA)
        tp.parent.mkdir(parents=True, exist_ok=True)
        tmp = tp.with_name(f"{tp.stem}.{threading.get_ident()}.tmp.jpg")
        cv2.imwrite(str(tmp), im, [cv2.IMWRITE_JPEG_QUALITY, 80])
        os.replace(tmp, tp)
    return tp.read_bytes()


class H(BaseHTTPRequestHandler):
    base = ROOT / "labeling"
    videos = ROOT / "videos"
    vstats = {"bytes": 0, "reqs": 0}
    _wl = [None, set()]          # (mtime de frames.json, nombres de video permitidos)

    def allowed_videos(self):
        fj = self.base / "frames.json"
        mt = fj.stat().st_mtime if fj.exists() else None
        if self._wl[0] != mt:
            self._wl[1] = {f["video"] for f in json.loads(fj.read_text())} if fj.exists() else set()
            self._wl[0] = mt
        return self._wl[1]

    def video_path(self, name):
        """Ruta real del video, o None si el nombre no es válido, no está en frames.json o no existe."""
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or "") or name not in self.allowed_videos():
            return None
        p = os.path.realpath(self.videos / f"{name}.mp4")
        return p if os.path.isfile(p) else None

    def _video(self, name):
        p = self.video_path(name)
        if not p:
            return self._send(404, "no encontrado", "text/plain")
        size = os.path.getsize(p)
        a, b, code = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                return self._send_416(size)
            if m.group(1) == "":                       # sufijo: los últimos N bytes
                a, b = max(size - int(m.group(2)), 0), size - 1
            else:
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) else size - 1
            if a >= size or b < a:
                return self._send_416(size)
            b = min(b, size - 1, a + CHUNK - 1)
            code = 206
        n = b - a + 1
        self.send_response(code)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(n))
        self.send_header("Cache-Control", "private, max-age=3600")
        if code == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(a)
                left = n
                while left > 0:
                    blk = f.read(min(256 * 1024, left))
                    if not blk:
                        break
                    self.wfile.write(blk)
                    left -= len(blk)
                    self.vstats["bytes"] += len(blk)
            self.vstats["reqs"] += 1
        except (BrokenPipeError, ConnectionResetError):
            pass            # el navegador cortó la lectura (saltó a otro punto o cerró el panel)

    def _send_416(self, size):
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass            # el navegador cerró la conexión (recarga, cambio de imagen)

    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path.startswith("/video/"):
            return self._video(urllib.parse.unquote(url.path[7:]))
        if url.path == "/api/video_info":
            name = (q.get("name") or [""])[0]
            p = self.video_path(name)
            if not p:
                return self._send(404, "no encontrado", "text/plain")
            if name not in VINFO:
                VINFO[name] = video_info(p)
            return self._send(200, json.dumps(VINFO[name]))
        if url.path == "/api/video_pts":      # ?name=&a=&b=  -> PTS de los fotogramas entre a y b (segundos)
            name = (q.get("name") or [""])[0]
            p = self.video_path(name)
            if not p:
                return self._send(404, "no encontrado", "text/plain")
            if name not in VPTS:
                VPTS[name] = video_pts(p)
            try:
                a, b = float((q.get("a") or ["0"])[0]), float((q.get("b") or ["1e9"])[0])
            except ValueError:
                return self._send(400, "parámetros inválidos", "text/plain")
            ps = VPTS[name]
            return self._send(200, json.dumps(dict(pts=ps[bisect.bisect_left(ps, a):bisect.bisect_right(ps, b)])))
        if url.path == "/api/video_stats":
            if q.get("reset"):
                self.vstats.update(bytes=0, reqs=0)
            return self._send(200, json.dumps(self.vstats))
        if self.path in ("/", "/index.html"):
            return self._send(200, HTML_PATH.read_text(), "text/html; charset=utf-8")
        if self.path == "/api/data":
            fj, lj = self.base / "frames.json", self.base / "labels.json"
            frames = json.loads(fj.read_text()) if fj.exists() else []
            labels = json.loads(lj.read_text()) if lj.exists() else {}
            return self._send(200, json.dumps(dict(frames=frames, labels=labels)))
        if self.path.startswith("/thumb/"):
            p = (self.base / self.path[7:]).resolve()
            if self.base.resolve() in p.parents and p.suffix == ".jpg" and p.exists():
                return self._send(200, thumb(self.base, p), "image/jpeg")
        if self.path.startswith("/img/"):
            p = (self.base / self.path[5:]).resolve()
            if self.base.resolve() in p.parents and p.suffix == ".jpg" and p.exists():
                return self._send(200, p.read_bytes(), "image/jpeg")
        self._send(404, "no encontrado", "text/plain")

    def do_POST(self):
        if self.path == "/api/snapshot":
            n = int(self.headers.get("Content-Length", 0))
            try:
                d = json.loads(self.rfile.read(n))
                name, t = d["video"], float(d["t"])
            except (ValueError, KeyError, TypeError):
                return self._send(400, json.dumps(dict(error="solicitud inválida")))
            p = self.video_path(name)
            if not p:
                return self._send(404, json.dumps(dict(error="video no encontrado")))
            with LOCK:
                code, resp = snapshot_from_video(self.base, self.videos, p, name, t, d.get("after_key"))
            return self._send(code, json.dumps(resp))
        if self.path != "/api/save":
            return self._send(404, "no encontrado", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n))        # {key, state}
        lj = self.base / "labels.json"
        with LOCK:
            labels = json.loads(lj.read_text()) if lj.exists() else {}
            labels[d["key"]] = d["state"]
            tmp = lj.with_suffix(".tmp")
            tmp.write_text(json.dumps(labels))
            os.replace(tmp, lj)
        self._send(200, '{"ok":true}')


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="IP(s) donde escuchar, separadas por coma (ej. 10.0.0.4,100.64.0.4)")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--dir", default=None, help="carpeta con frames.json (por defecto labeling/)")
    ap.add_argument("--videos", default=None, help="carpeta con los videos originales (por defecto videos/)")
    a = ap.parse_args()
    if a.dir:
        H.base = Path(a.dir).resolve()
    if a.videos:
        H.videos = Path(a.videos).resolve()
    hosts = [h.strip() for h in a.host.split(",") if h.strip()]
    servers = [ThreadingHTTPServer((h, a.port), H) for h in hosts]      # un solo proceso: un solo candado de escritura
    for h in hosts:
        print(f"Etiquetado en http://{h}:{a.port}  (datos: {H.base})", flush=True)
    for srv in servers[:-1]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    servers[-1].serve_forever()
