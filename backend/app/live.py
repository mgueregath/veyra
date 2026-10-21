"""Sesiones de detección en vivo: validación de la fuente, procesos de trabajo y reenvío autenticado."""

import json
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from sqlalchemy import func, select

from . import remote
from .config import settings
from .db import SessionLocal
from .models import LiveSession

_procs: dict[int, subprocess.Popen] = {}
ALLOWED_SCHEMES = {"rtsp", "rtsps", "http", "https"}


def validate_url(url: str) -> str:
    """Solo flujos de red. FFmpeg también lee file:, concat:, data:… que NO deben poder pedirse desde la web."""
    url = url.strip()
    if any(c in url for c in " \t\r\n\x00") or len(url) > 1000:
        raise ValueError("La URL no puede tener espacios ni saltos de línea")
    u = urlparse(url)
    if u.scheme.lower() not in ALLOWED_SCHEMES or not u.hostname:
        raise ValueError("Solo se admiten URL rtsp://, rtsps://, http:// o https:// (flujos de cámara)")
    allowed = [h.strip().lower() for h in settings.live_allowed_hosts.split(",") if h.strip()]
    if allowed and u.hostname.lower() not in allowed:
        raise ValueError(f"El host {u.hostname} no está en la lista permitida del servidor")
    return url


def mask_source(src: str, kind: str) -> str:
    """Sin contraseña: rtsp://usuario:****@host/ruta. Los archivos solo muestran su nombre."""
    if kind == "video":
        return Path(src).name
    u = urlparse(src)
    if u.password:
        netloc = f"{u.username}:****@{u.hostname}" + (f":{u.port}" if u.port else "")
        return urlunparse(u._replace(netloc=netloc))
    return src


def new_token() -> str:
    """Token de la sesión, en hexadecimal: un base64 puede empezar por «-» y argparse lo tomaría por una opción."""
    return secrets.token_hex(24)


def _free_port() -> int:
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


def active_count() -> int:
    """Sesiones activas: procesos locales más las que corren en workers."""
    with SessionLocal() as db:
        remote_n = db.scalar(
            select(func.count())
            .select_from(LiveSession)
            .where(LiveSession.endpoint.is_not(None), LiveSession.status == "running")
        )
    return sum(1 for p in _procs.values() if p.poll() is None) + (remote_n or 0)


def _base(sess: LiveSession) -> str:
    """Dónde escucha el proceso de la sesión: en esta máquina o en el worker."""
    return sess.endpoint or f"http://127.0.0.1:{sess.port}"


def start_worker(sess: LiveSession, weights: str | None, classes: list[str], record_dir: Path) -> None:
    """Lanza el proceso y espera a que esté listo (cargar el modelo tarda). Lanza RuntimeError si no arranca."""
    prm = sess.params
    with SessionLocal() as db:
        worker = remote.pick_worker(db, "live")
        worker_id = worker.id if worker else None
    if worker_id is not None:
        _start_remote(sess, weights, classes, record_dir, worker_id)
        return
    port, token = _free_port(), new_token()
    detector = settings.live_detector or ("yolo" if weights else "none")
    cmd = [
        settings.live_python or settings.train_python,
        str(settings.live_script),
        "--source",
        sess.source,
        "--detector",
        detector,
        "--port",
        str(port),
        "--token",
        token,
        "--threshold",
        str(prm.get("threshold", 0.25)),
        "--imgsz",
        str(prm.get("imgsz", 1280)),
        "--device",
        settings.device,
        "--record-dir",
        str(record_dir),
        "--classes",
        json.dumps(classes),
    ]
    if weights and detector == "yolo":
        cmd += ["--weights", weights]
    if sess.source_kind == "video":
        cmd += ["--loop"] if prm.get("loop", True) else []
    if prm.get("no_realtime"):
        cmd += ["--no-realtime"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    # El proceso puede escribir avisos (p. ej. de PyTorch) antes de estar listo: se lee hasta ver «LISTO».
    # Después se sigue vaciando su salida en el mismo hilo para que el buffer no se llene.
    lines: list[str] = []
    ready = threading.Event()

    def pump() -> None:
        for line in p.stdout:  # type: ignore[union-attr]
            if not ready.is_set():
                lines.append(line)
                if line.startswith("LISTO"):
                    ready.set()

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.time() + settings.live_start_timeout
    while not ready.is_set() and p.poll() is None and time.time() < deadline:
        ready.wait(0.2)
    if not ready.is_set():
        p.kill()
        raise RuntimeError("el proceso de detección no arrancó: " + "".join(lines)[-400:])
    _procs[sess.id] = p
    sess.port, sess.token, sess.pid = port, token, p.pid


def _start_remote(sess: LiveSession, weights: str | None, classes: list[str], record_dir: Path, worker_id: int) -> None:
    """La sesión corre en un worker: se le encarga y se espera a que diga dónde escucha."""
    prm = sess.params
    payload = {
        "source": sess.source,
        "detector": settings.live_detector or ("yolo" if weights else "none"),
        "classes": classes,
        "threshold": prm.get("threshold", 0.25),
        "imgsz": prm.get("imgsz", 1280),
        "loop": sess.source_kind == "video" and prm.get("loop", True),
        "no_realtime": bool(prm.get("no_realtime")),
        "record_dir": str(record_dir),
    }
    if weights:
        payload["weights"] = weights
    jid = remote.submit("live", payload, worker_id)
    snap = remote.wait(
        jid,
        queue_timeout=settings.worker_queue_timeout,
        stop_when=lambda s: "endpoint" in s["progress"],
        poll=0.3,
    )
    if "endpoint" not in snap["progress"]:
        remote.request_cancel(jid)
        raise RuntimeError(
            "el worker no pudo iniciar la sesión: " + (snap["error"] or snap["log"][-400:] or snap["status"])
        )
    sess.endpoint, sess.token, sess.pid = (
        snap["progress"]["endpoint"],
        snap["progress"]["token"],
        snap["progress"].get("pid"),
    )
    sess.port, sess.job_id = None, jid


def _get(sess: LiveSession, path: str, timeout: float = 5.0):
    return urllib.request.urlopen(
        f"{_base(sess)}{path}{'&' if '?' in path else '?'}token={sess.token}", timeout=timeout
    )


def worker_stats(sess: LiveSession) -> dict | None:
    try:
        return json.load(_get(sess, "/stats"))
    except Exception:
        return None


def worker_control(sess: LiveSession, body: dict) -> dict:
    rq = urllib.request.Request(
        f"{_base(sess)}/control?token={sess.token}", data=json.dumps(body).encode(), method="POST"
    )
    return json.load(urllib.request.urlopen(rq, timeout=5))


def worker_frame(sess: LiveSession) -> tuple[bytes, list]:
    r = _get(sess, "/frame.jpg", timeout=10)
    return r.read(), json.loads(r.headers.get("X-Detections", "[]"))


def stream_iter(sess: LiveSession):
    """Reenvía el MJPEG del proceso; al cerrarse el cliente se cierra también la conexión con el proceso."""
    up = _get(sess, "/stream.mjpg", timeout=15)
    try:
        while True:
            chunk = up.read1(65536)  # lo que haya disponible: read(n) esperaría a juntar n bytes y añadiría retardo
            if not chunk:
                break
            yield chunk
    finally:
        up.close()


def alive(sess: LiveSession) -> bool:
    if sess.endpoint:
        snap = remote.get(sess.job_id) if sess.job_id else None
        return bool(snap and snap["status"] == "running")
    p = _procs.get(sess.id)
    if p is not None:
        return p.poll() is None
    return False


def stop_worker(sess_id: int) -> None:
    with SessionLocal() as db:
        s = db.get(LiveSession, sess_id)
        job_id = s.job_id if s and s.endpoint else None
    if job_id:  # sesión remota: el worker detiene el proceso al recibir la cancelación en su próximo latido
        remote.request_cancel(job_id)
        return
    p = _procs.pop(sess_id, None)
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            p.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass


def stop_all() -> None:
    for sid in list(_procs):
        stop_worker(sid)


def recover_orphans() -> int:
    """Al arrancar la API las sesiones «activas» ya no tienen su proceso: se matan los huérfanos y se cierran."""
    n = 0
    with SessionLocal() as db:
        for s in db.scalars(select(LiveSession).where(LiveSession.status.in_(["starting", "running"]))):
            if s.pid and not s.endpoint:  # (el pid de una sesión remota no existe en esta máquina)
                try:
                    cmdline = Path(f"/proc/{s.pid}/cmdline").read_bytes().decode(errors="ignore")
                    if (
                        "live_yolo.py" in cmdline
                    ):  # solo si de verdad es uno de nuestros procesos (el pid pudo reutilizarse)
                        os.killpg(os.getpgid(s.pid), signal.SIGTERM)
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    pass
            s.status, s.error = "stopped", (s.error or "") + "interrumpida: la API se reinició"
            n += 1
        db.commit()
    return n
