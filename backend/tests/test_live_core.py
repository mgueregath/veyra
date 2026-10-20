import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workers"))
from live_core import FakeDetector, IouTracker, LatestFrameReader, Stats, YoloDetector, draw_overlay, iou, is_network  # noqa: E402


def D(x, y, w=30, h=30, s=0.9, c=0):
    return (x, y, w, h, s, c)


# ---------- seguimiento ----------
def test_iou_and_source_kinds():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0 and iou((0, 0, 10, 10), (20, 0, 5, 5)) == 0.0
    assert is_network("rtsp://cam/stream") and is_network("http://x/a.mjpg") and not is_network("/data/video.mp4")


def test_tracker_keeps_identity_of_a_moving_object_and_counts_it_once():
    t = IouTracker()
    ids = []
    for k in range(12):
        out = t.update([D(10 + 4 * k, 50)])
        ids.append(out[0].id if out else None)
    assert ids[:2] == [None, None] and len(set(ids[2:])) == 1  # se confirma al 3.er fotograma y no cambia de id
    assert t.totals == {0: 1}


def test_tracker_counts_distinct_objects_and_ignores_flicker():
    t = IouTracker()
    for _ in range(5):
        t.update([D(10, 10), D(200, 200)])
    assert t.totals == {0: 2}
    t2 = IouTracker()
    t2.update([D(10, 10)])
    t2.update([])  # un solo fotograma aislado (ruido): no se confirma ni cuenta
    t2.update([D(300, 300)])
    t2.update([])
    assert t2.totals == {}


def test_tracker_drops_lost_tracks_and_does_not_mix_classes():
    t = IouTracker(max_age=3)
    for _ in range(4):
        t.update([D(10, 10)])
    for _ in range(5):
        t.update([])
    assert t.tracks == []  # perdido más de max_age: se descarta
    t3 = IouTracker()
    for _ in range(4):
        t3.update([D(10, 10, c=0), D(10, 10, c=1)])  # misma caja, dos clases: son dos objetos
    assert t3.totals == {0: 1, 1: 1}


def test_tracker_reappearing_object_after_gap_is_new_only_after_max_age():
    t = IouTracker(max_age=5)
    for _ in range(4):
        t.update([D(10, 10)])
    t.update([])
    t.update([])  # desaparece 2 fotogramas (< max_age) y vuelve: es el mismo
    for _ in range(3):
        t.update([D(10, 10)])
    assert t.totals == {0: 1}


# ---------- estadísticas y dibujo ----------
def test_stats_fps_and_latency():
    s = Stats()
    now = time.time()
    for k in range(10):
        s.times.append(now + k * 0.1)
        s.lat.append(0.05)
    assert abs(s.fps - 10) < 0.5 and abs(s.latency_ms - 50) < 1
    assert Stats().fps == 0.0 and Stats().latency_ms == 0.0


def test_draw_overlay_draws_on_a_copy():
    from live_core import Track

    img = np.zeros((120, 200, 3), np.uint8)
    out = draw_overlay(img, [Track(1, (20, 30, 50, 40), 0, 0.8, confirmed=True)], ["piojo"], "banner")
    assert out.shape == img.shape and out.sum() > 0 and img.sum() == 0  # no toca el original


def test_fake_detector_moves():
    d = FakeDetector()
    f = np.zeros((90, 160, 3), np.uint8)
    a, b = d(f), d(f)
    assert b[0][0] - a[0][0] == 5 and a[0][4] == 0.9 and a[1][4] == 0.15


# ---------- lector de fuentes ----------
def test_reader_delivers_frames_in_order_and_finishes(vfr_video):
    r = LatestFrameReader(str(vfr_video), realtime=False)
    seen, t0 = [], time.time()
    while time.time() - t0 < 10:
        g = r.read_new(timeout=0.5)
        if g is None and r.ended:
            break
        if g:
            seen.append(g[1])
    r.close()
    assert r.ended and seen == sorted(seen) and len(set(seen)) == len(seen) and r.captured == 90
    assert r.size == (160, 96) and r.delivered == len(seen)


def test_slow_consumer_drops_old_frames_instead_of_falling_behind(vfr_video):
    r = LatestFrameReader(str(vfr_video), realtime=False)
    time.sleep(1.0)  # el consumidor llega tarde: el lector ya capturó todo
    g = r.read_new(timeout=1)
    r.close()
    assert g is not None and g[1] == 90  # recibe el MÁS RECIENTE, no el primero
    assert r.dropped >= 80


def test_realtime_file_is_paced_by_video_timestamps(vfr_video):
    r = LatestFrameReader(str(vfr_video), realtime=True)
    t0 = time.time()
    n = 0
    while n < 20 and time.time() - t0 < 10:
        if r.read_new(timeout=1):
            n += 1
    elapsed = time.time() - t0
    r.close()
    assert n >= 15 and elapsed > 1.0  # 15 fotogramas a 0,1 s no pueden llegar de golpe (ritmo real del video)


def test_loop_restarts_the_file(vfr_video):
    r = LatestFrameReader(str(vfr_video), realtime=False, loop=True)
    t0 = time.time()
    while r.captured < 200 and time.time() - t0 < 10:
        time.sleep(0.05)
    r.close()
    assert r.captured >= 200 and not r.ended


def test_missing_source_retries_without_crashing():
    r = LatestFrameReader("rtsp://127.0.0.1:1/no-existe", realtime=True, max_backoff=0.2)
    time.sleep(2.0)
    n = r.open_failures
    r.close()
    assert n >= 2 and r.last_error and r.captured == 0 and r.read_new(timeout=0.1) is None


def test_missing_file_ends_or_retries_cleanly(tmp_path):
    r = LatestFrameReader(str(tmp_path / "no.mp4"), realtime=False, max_backoff=0.2)
    time.sleep(1.0)
    r.close()
    assert r.captured == 0 and r.open_failures >= 1


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="sin Python del sistema")
def test_yolo_detector_importable_only_when_needed():
    assert (
        YoloDetector.__name__ == "YoloDetector"
    )  # el módulo carga sin PyTorch; solo la clase lo necesita al instanciarse
