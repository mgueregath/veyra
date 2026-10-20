"""El proceso de detección en vivo completo (workers/live_yolo.py) con las tres fuentes: archivo, MJPEG y RTSP."""

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pytest

WORKER = Path(__file__).resolve().parents[2] / "workers" / "live_yolo.py"
TOKEN = "tok123"
procs: list[subprocess.Popen] = []


def free_port():
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


def start_worker(source, *extra, detector="fake"):
    port = free_port()
    p = subprocess.Popen([sys.executable, str(WORKER), "--source", source, "--detector", detector, "--port", str(port), "--token", TOKEN, *extra],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # fmt: skip
    procs.append(p)
    assert p.stdout.readline().startswith("LISTO"), "el proceso no arrancó"
    return port, p


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    procs.clear()


def get(port, path, token=TOKEN, timeout=10):
    return urllib.request.urlopen(
        f"http://127.0.0.1:{port}{path}{'&' if '?' in path else '?'}token={token}", timeout=timeout
    )


def stats(port):
    return json.load(get(port, "/stats"))


def wait_stats(port, cond, tmo=15):
    t0 = time.time()
    while time.time() - t0 < tmo:
        try:
            s = stats(port)
            if cond(s):
                return s
        except Exception:
            pass
        time.sleep(0.1)
    raise AssertionError("condición no cumplida: " + str(stats(port)))


def post(port, body):
    rq = urllib.request.Request(
        f"http://127.0.0.1:{port}/control?token={TOKEN}", data=json.dumps(body).encode(), method="POST"
    )
    return json.load(urllib.request.urlopen(rq, timeout=10))


def test_file_source_stats_frame_and_token(vfr_video):
    port, _ = start_worker(str(vfr_video), "--loop", "--no-realtime")
    s = wait_stats(port, lambda s: s["frames"] > 5)
    assert s["fps"] > 0 and s["latency_ms"] >= 0 and s["size"] == [160, 96] and s["classes"] == ["objeto"]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=5)
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        get(port, "/stats", token="mal")
    assert e.value.code == 403
    r = get(port, "/frame.jpg")
    img = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
    dets = json.loads(r.headers["X-Detections"])
    assert img.shape[:2] == (96, 160) and len(dets) == 1 and dets[0][4] == 0.9  # umbral 0,25: el de 0,15 no entra


def test_threshold_pause_and_distinct_count(vfr_video):
    port, _ = start_worker(str(vfr_video), "--loop", "--no-realtime")
    wait_stats(port, lambda s: s["frames"] > 5)
    assert post(port, {"threshold": 0.95})["threshold"] == 0.95
    time.sleep(0.5)
    assert json.loads(get(port, "/frame.jpg").headers["X-Detections"]) == []  # nada supera 0,95
    post(port, {"threshold": 0.05})
    time.sleep(0.5)
    assert len(json.loads(get(port, "/frame.jpg").headers["X-Detections"])) == 2
    s = wait_stats(port, lambda s: s["totals"].get("objeto", 0) >= 1)
    assert s["threshold"] == 0.05
    post(port, {"paused": True})
    time.sleep(0.4)
    n = stats(port)["frames"]
    time.sleep(0.6)
    assert stats(port)["frames"] == n and stats(port)["paused"] is True  # en pausa no procesa
    post(port, {"paused": False})
    wait_stats(port, lambda s: s["frames"] > n)


def test_mjpeg_stream_serves_valid_jpegs(vfr_video):
    port, _ = start_worker(str(vfr_video), "--loop")
    wait_stats(port, lambda s: s["frames"] > 2)
    r = get(port, "/stream.mjpg", timeout=15)
    assert "multipart/x-mixed-replace" in r.headers["Content-Type"]
    buf, got = b"", 0
    t0 = time.time()
    while got < 3 and time.time() - t0 < 10:
        buf += r.read(4096)
        while True:
            a, b = buf.find(b"\xff\xd8"), buf.find(b"\xff\xd9")
            if a < 0 or b < 0 or b < a:
                break
            img = cv2.imdecode(np.frombuffer(buf[a : b + 2], np.uint8), cv2.IMREAD_COLOR)
            assert img is not None and img.shape[:2] == (96, 160)
            buf, got = buf[b + 2 :], got + 1
    r.close()
    assert got >= 3


def test_record_clip(vfr_video, tmp_path):
    port, _ = start_worker(str(vfr_video), "--loop", "--no-realtime", "--record-dir", str(tmp_path))
    wait_stats(port, lambda s: s["frames"] > 3)
    post(port, {"record": True})
    s = wait_stats(port, lambda s: s["recording"] and s["record_path"])
    time.sleep(1.0)
    post(port, {"record": False})
    time.sleep(0.5)
    clip = Path(s["record_path"])
    cap = cv2.VideoCapture(str(clip))
    n = 0
    while cap.read()[0]:
        n += 1
    assert clip.is_file() and n >= 5


def test_file_ends_and_process_exits_cleanly(vfr_video):
    port, p = start_worker(str(vfr_video), "--no-realtime")
    assert p.wait(timeout=20) == 0  # sin --loop, al terminar el archivo el proceso termina solo


def test_mjpeg_source_is_ingested_by_another_worker(vfr_video):
    """Cadena real: un proceso sirve MJPEG y otro lo lee como si fuera una cámara de red."""
    a, _ = start_worker(str(vfr_video), "--loop")
    wait_stats(a, lambda s: s["frames"] > 3)
    b, _ = start_worker(f"http://127.0.0.1:{a}/stream.mjpg?token={TOKEN}", detector="none")
    s = wait_stats(b, lambda s: s["frames"] > 5, tmo=20)
    assert s["size"] == [160, 96] and s["fps"] > 0 and s["dropped"] >= 0


def _docker_ok():
    return (
        shutil.which("docker")
        and subprocess.run(["docker", "image", "inspect", "bluenviron/mediamtx:latest"], capture_output=True).returncode
        == 0
    )


@pytest.mark.skipif(not _docker_ok(), reason="sin Docker o sin la imagen bluenviron/mediamtx")
def test_rtsp_source_and_reconnection(vfr_video):
    """RTSP real: servidor mediamtx propio en Docker + ffmpeg publicando; se corta la cámara y el proceso reconecta."""
    port = free_port()
    name = f"labelhub-rtsp-test-{port}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:8554", "bluenviron/mediamtx:latest"],
        check=True,
        capture_output=True,
    )
    pub = None

    def publish():
        return subprocess.Popen(["ffmpeg", "-v", "error", "-re", "-stream_loop", "-1", "-i", str(vfr_video), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                                 "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:{port}/cam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip

    try:
        time.sleep(2.0)
        pub = publish()
        time.sleep(2.5)
        w, _ = start_worker(f"rtsp://127.0.0.1:{port}/cam")
        s = wait_stats(w, lambda s: s["frames"] > 5, tmo=30)
        assert s["size"] == [160, 96] and s["fps"] > 0
        before = s["frames"]
        pub.terminate()  # la cámara se apaga
        pub.wait(timeout=5)
        time.sleep(3.0)
        pub = publish()  # y vuelve
        s2 = wait_stats(w, lambda s: s["frames"] > before + 10 and s["reconnects"] >= 1, tmo=60)
        assert s2["reconnects"] >= 1  # reconectó solo y siguió procesando
    finally:
        if pub and pub.poll() is None:
            pub.terminate()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_stream_connected_before_the_first_frame_never_sends_an_empty_jpeg(vfr_video):
    """Regresión: el navegador se conecta nada más iniciar la sesión, antes de que haya un fotograma procesado."""
    port, _ = start_worker(str(vfr_video), "--loop")  # sin --no-realtime: el primer fotograma tarda algo
    r = get(port, "/stream.mjpg", timeout=15)
    buf = b""
    t0 = time.time()
    while b"\xff\xd9" not in buf and time.time() - t0 < 10:
        buf += r.read1(4096)
    r.close()
    head = buf.split(b"\r\n\r\n", 1)[0]
    assert b"Content-Length: 0" not in head and b"\xff\xd8" in buf  # la primera parte ya es una imagen real
