import json
import os
import time

import cv2
import numpy as np
import pytest
from test_workflow import as_user

from app import live
from app.models import Frame, LiveSession, Model, Video


@pytest.fixture()
def live_world(world, db, vfr_video, tmp_path, monkeypatch):
    """`world` con el video real asociado, un modelo registrado y el detector simulado del proceso en vivo."""
    from app.config import settings

    monkeypatch.setattr(settings, "live_detector", "fake")
    monkeypatch.setattr(settings, "live_python", os.sys.executable)
    monkeypatch.setattr(settings, "runs_root", tmp_path / "runs")
    v = db.query(Video).filter(Video.name == "v1").one()
    v.path = str(vfr_video)
    m = Model(
        project_id=world["project"].id,
        name="m1",
        path="/no/importa.pt",
        arch="yolov8n",
        classes=["louse", "fly"],
        params={},
        created_by=1,
    )
    db.add(m)
    db.commit()
    world["video"], world["model"] = v, m
    yield world
    live.stop_all()


def start(c, w, **kw):
    body = {
        "source_kind": "video",
        "video_id": w["video"].id,
        "model_id": w["model"].id,
        "threshold": 0.25,
        "no_realtime": True,
        **kw,
    }
    return c.post(f"/api/projects/{w['project'].id}/live", json=body)


def wait_stats(c, sid, cond, tmo=20):
    t0 = time.time()
    while time.time() - t0 < tmo:
        d = c.get(f"/api/live/{sid}").json()
        if d["stats"] and cond(d["stats"]):
            return d
        time.sleep(0.2)
    raise AssertionError("sin estadísticas útiles: " + str(c.get(f"/api/live/{sid}").json()))


def test_start_stats_stream_control_and_stop(client, live_world, db):
    adm = as_user("adm")
    r = start(adm, live_world)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    d = wait_stats(adm, sid, lambda s: s["frames"] > 5)
    assert (
        d["status"] == "running"
        and d["stats"]["classes"] == ["louse", "fly"]
        and d["stats"]["fps"] > 0
        and d["source"] == "vfr.mp4"
    )
    assert "token" not in json.dumps(d) and "port" not in d  # el token y el puerto internos no salen de la API
    # flujo MJPEG reenviado: el contenido viene del proceso (el TestClient no sabe cerrar una respuesta infinita,
    # por eso se lee el generador de reenvío; el endpoint completo lo ejercita la prueba en Chrome real)
    c2 = as_user("lector")  # un lector puede mirar
    gen = live.stream_iter(db.get(LiveSession, sid))
    buf, got = b"", 0
    for chunk in gen:
        buf += chunk
        a, b = buf.find(b"\xff\xd8"), buf.find(b"\xff\xd9")
        if a >= 0 and b > a:
            assert cv2.imdecode(np.frombuffer(buf[a : b + 2], np.uint8), 1).shape[:2] == (96, 160)
            buf, got = buf[b + 2 :], got + 1
        if got >= 2:
            break
    gen.close()
    assert got == 2
    assert client.get(f"/api/live/{sid}/stream").status_code == 401  # sin sesión no se ve
    assert as_user("intruso").get(f"/api/live/{sid}/stream").status_code == 404
    # control: umbral, pausa
    assert adm.post(f"/api/live/{sid}/control", json={"threshold": 0.95}).json()["threshold"] == 0.95
    assert adm.get(f"/api/live/{sid}").json()["params"]["threshold"] == 0.95
    assert adm.post(f"/api/live/{sid}/control", json={"threshold": 2}).status_code == 422
    assert c2.post(f"/api/live/{sid}/control", json={"paused": True}).status_code == 403
    # detener
    out = adm.delete(f"/api/live/{sid}").json()
    assert out["status"] == "stopped" and out["summary"]["frames"] > 5 and out["ended_at"]
    assert (
        adm.get(f"/api/live/{sid}/stream").status_code == 409
        and adm.post(f"/api/live/{sid}/control", json={"paused": True}).status_code == 409
    )


def test_save_frame_goes_back_to_labeling_with_detections(client, live_world, db):
    adm = as_user("adm")
    sid = start(adm, live_world).json()["id"]
    wait_stats(adm, sid, lambda s: s["frames"] > 5)
    adm.post(f"/api/live/{sid}/control", json={"threshold": 0.05})
    time.sleep(0.5)
    r = adm.post(f"/api/live/{sid}/save-frame")
    assert r.status_code == 200, r.text
    fid, n = r.json()["frame_id"], r.json()["boxes"]
    assert n == 2  # el detector simulado da 2 cajas (0,9 y 0,15) y el umbral es 0,05
    d = adm.get(f"/api/frames/{fid}").json()
    assert d["stratum"] == "live" and d["width"] == 160 and d["height"] == 96 and len(d["proposals"]) == 2
    assert all(p["sources"] == ["model", f"model:{live_world['model'].id}"] for p in d["proposals"])
    assert sorted(p["score"] for p in d["proposals"]) == [0.15, 0.9]
    assert adm.get(f"/api/frames/{fid}/image").content[:2] == b"\xff\xd8"  # la imagen quedó en el almacenamiento
    # aparece al final de la lista de etiquetado, lista para etiquetar
    items = as_user("ana").get(f"/api/projects/{live_world['project'].id}/frames?limit=500").json()
    assert items[-1]["id"] == fid and items[-1]["video"] == f"live-{sid}"
    r2 = adm.post(f"/api/live/{sid}/save-frame").json()
    assert (
        r2["frame_id"] != fid and db.query(Frame).filter(Frame.stratum == "live").count() == 2
    )  # cada guardado es una imagen nueva
    assert adm.post("/api/live/9999/save-frame").status_code == 404
    assert as_user("lector").post(f"/api/live/{sid}/save-frame").status_code == 403


def test_record_clips_list_and_download(client, live_world):
    adm = as_user("adm")
    sid = start(adm, live_world).json()["id"]
    wait_stats(adm, sid, lambda s: s["frames"] > 3)
    adm.post(f"/api/live/{sid}/control", json={"record": True})
    wait_stats(adm, sid, lambda s: s["recording"] and s["record_path"])
    time.sleep(1.0)
    adm.post(f"/api/live/{sid}/control", json={"record": False})
    time.sleep(0.6)
    clips = adm.get(f"/api/live/{sid}/clips").json()
    assert len(clips) == 1 and clips[0]["bytes"] > 1000
    r = adm.get(f"/api/live/{sid}/clips/{clips[0]['name']}")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.content[4:8] == b"ftyp"
    for bad in ("../../etc/passwd", "clip_1.mp4.txt", "x.mp4", "..%2Fclip_1.mp4"):
        assert adm.get(f"/api/live/{sid}/clips/{bad}").status_code in (404, 422)
    assert as_user("intruso").get(f"/api/live/{sid}/clips").status_code == 404


def test_url_validation_blocks_dangerous_protocols_and_masks_credentials(client, live_world, monkeypatch):
    adm = as_user("adm")
    pid = live_world["project"].id
    for bad in (
        "file:///etc/passwd",
        "concat:/etc/passwd|/etc/hosts",
        "ftp://x/y",
        "data:text/plain,hola",
        "rtsp://",
        "rtsp://cam/a b",
        "/etc/passwd",
        "rtsp://cam/x\nfile:///etc/passwd",
    ):
        r = adm.post(f"/api/projects/{pid}/live", json={"source_kind": "url", "url": bad})
        assert r.status_code == 422, (bad, r.status_code)
    assert live.mask_source("rtsp://admin:secreto@10.0.0.5:554/cam1", "url") == "rtsp://admin:****@10.0.0.5:554/cam1"
    assert (
        live.mask_source("rtsp://10.0.0.5/cam1", "url") == "rtsp://10.0.0.5/cam1"
        and live.mask_source("/x/y/z.mp4", "video") == "z.mp4"
    )
    from app.config import settings

    monkeypatch.setattr(settings, "live_allowed_hosts", "10.0.0.5, camara.local")
    r = adm.post(f"/api/projects/{pid}/live", json={"source_kind": "url", "url": "rtsp://otro.host/x"})
    assert r.status_code == 422 and "lista permitida" in r.json()["detail"]
    r = adm.post(f"/api/projects/{pid}/live", json={"source_kind": "url", "url": "rtsp://127.0.0.1:1/x"})
    assert r.status_code == 422  # tampoco 127.0.0.1: la lista manda


def test_permissions_limits_and_validation(client, live_world, db):
    adm, pid = as_user("adm"), live_world["project"].id
    assert start(as_user("ana"), live_world).status_code == 403
    assert start(as_user("lector"), live_world).status_code == 403
    assert start(as_user("intruso"), live_world).status_code == 404
    assert adm.post(f"/api/projects/{pid}/live", json={"source_kind": "video", "video_id": 9999}).status_code == 422
    assert adm.post(f"/api/projects/{pid}/live", json={"source_kind": "otro"}).status_code == 422
    assert start(adm, live_world, model_id=9999).status_code == 404
    assert start(adm, live_world, imgsz=100).status_code == 422
    from app.config import settings

    s1, s2 = start(adm, live_world).json()["id"], start(adm, live_world).json()["id"]
    r = start(adm, live_world)
    assert r.status_code == 409 and str(settings.live_max) in r.json()["detail"]  # tope de sesiones simultáneas
    adm.delete(f"/api/live/{s1}")
    assert start(adm, live_world).status_code == 200  # al liberar una, se puede otra
    listed = as_user("lector").get(f"/api/projects/{pid}/live").json()
    assert len(listed) == 3 and as_user("lector").get(f"/api/live/{s2}").status_code == 200
    assert as_user("intruso").get(f"/api/live/{s2}").status_code == 404


def test_dead_process_closes_the_session(client, live_world, db):
    adm = as_user("adm")
    sid = start(adm, live_world, loop=False).json()["id"]  # sin repetir: el archivo termina y el proceso se va solo
    t0 = time.time()
    while time.time() - t0 < 20:
        d = adm.get(f"/api/live/{sid}").json()
        if d["status"] == "stopped":
            break
        time.sleep(0.3)
    assert d["status"] == "stopped" and "terminó" in d["error"]


def test_failed_start_is_recorded(client, live_world, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "live_script", settings.live_script.parent / "no_existe.py")
    adm = as_user("adm")
    r = start(adm, live_world)
    assert r.status_code == 502 and "no arrancó" in r.json()["detail"]
    s = db.query(LiveSession).one()
    assert s.status == "failed" and s.error


def test_recover_orphans_kills_old_workers_and_closes_sessions(client, live_world, db):
    adm = as_user("adm")
    sid = start(adm, live_world).json()["id"]
    wait_stats(adm, sid, lambda s: s["frames"] > 2)
    pid = db.get(LiveSession, sid).pid
    live._procs.clear()  # como si la API se hubiera reiniciado: ya no conoce el proceso, pero sigue vivo
    assert live.recover_orphans() == 1
    t0 = time.time()
    while (
        time.time() - t0 < 5
    ):  # en la prueba el proceso es hijo nuestro: hay que recogerlo para que no quede como zombi
        try:
            os.waitpid(pid, os.WNOHANG)
            os.kill(pid, 0)
        except (ChildProcessError, ProcessLookupError):
            break
        time.sleep(0.1)
    with pytest.raises(OSError):
        os.kill(pid, 0)  # el proceso huérfano murió
    db.expire_all()
    assert db.get(LiveSession, sid).status == "stopped" and "reinició" in db.get(LiveSession, sid).error


def test_start_tolerates_warnings_before_ready_and_reports_slow_start(client, live_world, monkeypatch, tmp_path):
    """Regresión: PyTorch escribe avisos ANTES de que el proceso esté listo; no deben confundirse con un fallo."""
    from app.config import settings

    noisy = tmp_path / "noisy.py"
    noisy.write_text(
        "import sys,time\nprint('UserWarning: aviso de torch', flush=True)\nprint('otro aviso', flush=True)\nprint('LISTO 1', flush=True)\ntime.sleep(30)\n"
    )
    monkeypatch.setattr(settings, "live_script", noisy)
    adm = as_user("adm")
    r = start(adm, live_world)
    assert r.status_code == 200 and r.json()["status"] == "running"
    adm.delete(f"/api/live/{r.json()['id']}")
    # y si nunca dice «LISTO» dentro del plazo, se informa con lo que escribió
    silent = tmp_path / "silent.py"
    silent.write_text("import time\nprint('cargando modelo…', flush=True)\ntime.sleep(30)\n")
    monkeypatch.setattr(settings, "live_script", silent)
    monkeypatch.setattr(settings, "live_start_timeout", 1.0)
    r = start(adm, live_world)
    assert r.status_code == 502 and "cargando modelo" in r.json()["detail"]


def test_session_token_never_looks_like_a_command_line_option():
    """Regresión: con token_urlsafe, ~1 de cada 64 sesiones fallaba al arrancar porque el token empezaba por «-»."""
    tokens = {live.new_token() for _ in range(3000)}
    assert len(tokens) == 3000 and all(len(t) == 48 and not t.startswith("-") and t.isalnum() for t in tokens)
