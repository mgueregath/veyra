import cv2
import numpy as np
from conftest import login
from sqlalchemy import select

from app.models import Frame


def test_info_and_pts_use_real_container_times(client, vworld):
    login(client, "lector")
    v = vworld["video"].id
    info = client.get(f"/api/videos/{v}/info").json()
    assert info["name"] == "v1" and abs(info["dur"] - 4.0) < 0.2
    pts = client.get(f"/api/videos/{v}/pts").json()["pts"]
    assert len(pts) == 90 and pts[1] == 0.1 and abs(pts[16] - pts[15] - 1 / 30) < 1e-3  # NO son múltiplos de 1/fps
    win = client.get(f"/api/videos/{v}/pts", params={"a": 1.3, "b": 1.5}).json()["pts"]
    assert win[0] == 1.3 and win[-1] <= 1.5 and len(win) == 3


def test_stream_range(client, vworld, vfr_video, monkeypatch):
    from app.routers import videos

    login(client, "lector")
    v = vworld["video"].id
    raw = vfr_video.read_bytes()
    r = client.get(f"/api/videos/{v}/stream", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == raw[:100] and r.headers["content-range"] == f"bytes 0-99/{len(raw)}"
    assert client.get(f"/api/videos/{v}/stream", headers={"Range": "bytes=-50"}).content == raw[-50:]
    assert client.get(f"/api/videos/{v}/stream", headers={"Range": f"bytes={len(raw) + 5}-"}).status_code == 416
    assert client.get(f"/api/videos/{v}/stream", headers={"Range": "bytes=abc"}).status_code == 416
    monkeypatch.setattr(videos, "CHUNK", 1000)  # un Range abierto se acota: no entrega el archivo entero
    r = client.get(f"/api/videos/{v}/stream", headers={"Range": "bytes=10-"})
    assert r.status_code == 206 and len(r.content) == 1000 and r.content == raw[10:1010]
    assert client.get(f"/api/videos/{v}/stream").status_code == 200


def test_video_access_control_and_missing_file(client, vworld, db):
    v = vworld["video"]
    assert client.get(f"/api/videos/{v.id}/stream").status_code == 401
    login(client, "intruso")
    for ep in ("stream", "info", "pts"):
        assert client.get(f"/api/videos/{v.id}/{ep}").status_code == 404
    client.post("/api/auth/logout")
    login(client, "lector")
    assert client.get("/api/videos/9999/info").status_code == 404
    v.path = "/no/existe.mp4"
    db.commit()
    assert client.get(f"/api/videos/{v.id}/stream").status_code == 404
    v.path = None
    db.commit()
    assert client.get(f"/api/videos/{v.id}/stream").status_code == 404


def test_snapshot_creates_exact_frame_after_source_and_is_idempotent(client, vworld, db, media):
    src = vworld["frames"][0]  # posición 0; hay otro frame en la posición 1
    login(client, "ana")
    pts = client.get(f"/api/videos/{vworld['video'].id}/pts").json()["pts"]
    t = pts[40]
    r = client.post(f"/api/frames/{src.id}/snapshot", json={"t": t})
    assert r.status_code == 200 and r.json()["existing"] is False and r.json()["position"] == 1
    new = db.get(Frame, r.json()["id"])
    assert new.frame_index == 40 and abs(new.t - round(t, 3)) < 1e-9 and new.stratum == "manual"
    assert (new.width, new.height) == (160, 96) and (media / new.image_path).is_file()
    # el frame que antes estaba en la posición 1 se corrió a la 2 (orden estable y sin huecos)
    db.expire_all()
    assert db.get(Frame, vworld["frames"][1].id).position == 2
    # es el fotograma que se ve: coincide con la decodificación de OpenCV
    cap = cv2.VideoCapture(str(vworld["video"].path))
    for _ in range(41):  # lectura secuencial: el salto por índice de OpenCV no es fiable
        ok, ref = cap.read()
    got = cv2.imread(str(media / new.image_path))
    ref = cv2.imdecode(
        cv2.imencode(".jpg", ref, [cv2.IMWRITE_JPEG_QUALITY, 92])[1], 1
    )  # misma compresión que lo guardado
    assert ok and float(np.mean((ref.astype(float) - got.astype(float)) ** 2)) < 2
    # idempotente: el mismo fotograma no se duplica
    r2 = client.post(f"/api/frames/{src.id}/snapshot", json={"t": t}).json()
    assert r2["existing"] is True and r2["id"] == new.id
    assert len(db.scalars(select(Frame)).all()) == 3
    # una segunda instantánea del mismo video queda DESPUÉS de la primera (orden: origen, 1.ª, 2.ª)
    r3 = client.post(f"/api/frames/{src.id}/snapshot", json={"t": pts[60]}).json()
    db.expire_all()
    assert r3["position"] == 2 and db.get(Frame, new.id).position == 1


def test_snapshot_errors_and_permissions(client, vworld, db):
    src = vworld["frames"][0]
    login(client, "ana")
    assert client.post(f"/api/frames/{src.id}/snapshot", json={"t": 0.0123}).status_code == 400  # no es un fotograma
    assert client.post(f"/api/frames/{src.id}/snapshot", json={}).status_code == 422
    assert client.post("/api/frames/9999/snapshot", json={"t": 0.1}).status_code == 404
    client.post("/api/auth/logout")
    login(client, "lector")
    assert client.post(f"/api/frames/{src.id}/snapshot", json={"t": 0.1}).status_code == 403
    client.post("/api/auth/logout")
    login(client, "intruso")
    assert client.post(f"/api/frames/{src.id}/snapshot", json={"t": 0.1}).status_code == 404


def test_snapshot_runs_project_proposer(client, vworld, db, monkeypatch):
    from app import proposers

    monkeypatch.setattr(
        proposers, "load", lambda cfg: lambda bgr: [{"bbox": [5, 6, 30, 20], "score": 7.5, "sources": ["blob"]}]
    )
    login(client, "ana")
    r = client.post(f"/api/frames/{vworld['frames'][0].id}/snapshot", json={"t": 0.5}).json()
    d = client.get(f"/api/frames/{r['id']}").json()
    assert d["proposals"][0]["bbox"] == [5, 6, 30, 20] and d["proposals"][0]["score"] == 7.5


def test_frame_exposes_video_and_thumb_is_cached(client, vworld, media):
    login(client, "lector")
    f = vworld["frames"][0]
    d = client.get(f"/api/frames/{f.id}").json()
    assert d["has_video"] is True and d["video_id"] == vworld["video"].id
    cv2.imwrite(str(media / "f0.jpg"), np.zeros((400, 800, 3), np.uint8))
    r = client.get(f"/api/frames/{f.id}/thumb", params={"h": 100})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert cv2.imdecode(np.frombuffer(r.content, np.uint8), 1).shape[:2] == (100, 200)
    assert (media / ".thumbs" / "100" / "f0.jpg").is_file()
