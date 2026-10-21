import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_assisted import train_model, trainable_world  # noqa: F401
from test_workflow import as_user

from app import live, remote
from app.config import settings
from app.main import app
from app.models import LiveSession, Model, Video


def make_worker(name="gpu1"):
    r = as_user("root").post("/api/admin/workers", json={"name": name})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c, r.json()


class FakeWorker(threading.Thread):
    """Un worker simulado que habla el protocolo real por HTTP y 'ejecuta' los trabajos a su manera."""

    def __init__(self, client, kinds=("train", "predict", "live"), handlers=None):
        super().__init__(daemon=True)
        self.c, self.kinds, self.handlers, self.stop = client, list(kinds), handlers or {}, threading.Event()
        self.cancel_seen: set[int] = set()
        self.running: list[int] = []
        self.procs: list = []

    def run(self):
        self.c.post(
            "/api/worker/register",
            json={"kinds": self.kinds, "max_batch": 1, "max_live": 2, "capabilities": {"gpus": []}},
        )
        threading.Thread(target=self._beats, daemon=True).start()
        while not self.stop.is_set():
            r = self.c.post("/api/worker/jobs/claim", json={"kinds": self.kinds, "wait": 1})
            if r.status_code == 200:
                job = r.json()
                self.running.append(job["id"])
                threading.Thread(target=self._do, args=(job,), daemon=True).start()

    def _beats(self):
        while not self.stop.is_set():
            r = self.c.post("/api/worker/heartbeat", json={"running_jobs": self.running})
            self.cancel_seen |= set(r.json()["cancel_jobs"])
            time.sleep(0.2)

    def _do(self, job):
        out = self.handlers[job["kind"]](self, job)
        self.c.post(f"/api/worker/jobs/{job['id']}/finish", json=out)
        self.running.remove(job["id"])


def fake_train(w, job):
    p = job["payload"]
    for e in range(1, 4):
        w.c.post(
            f"/api/worker/jobs/{job['id']}/progress",
            json={"progress": {"epoch": e, "epochs": 3, "metrics": {"m": e}}, "log": f"época {e}\n"},
        )
        time.sleep(0.2)
        if job["id"] in w.cancel_seen:
            return {"status": "cancelled"}
    Path(p["out"]).parent.mkdir(parents=True, exist_ok=True)
    Path(p["out"]).write_bytes(b"pesos")
    return {
        "status": "done",
        "result": {"map50": 0.5, "map50_95": 0.3, "precision": 0.6, "recall": 0.7, "weights": p["out"]},
        "log": "listo\n",
    }


def fake_predict(w, job):
    p = job["payload"]
    Path(p["out"]).write_text(json.dumps({i: [[10, 10, 20, 20, 0.9, 0]] for i in p["images"]}))
    return {"status": "done", "result": {"out": p["out"], "images": len(p["images"])}}


@pytest.fixture()
def remote_mode(world, db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gpu_mode", "remote")
    monkeypatch.setattr(settings, "runs_root", tmp_path / "runs")
    monkeypatch.setattr(settings, "models_root", tmp_path / "models")
    monkeypatch.setattr(settings, "export_root", tmp_path / "exports")
    monkeypatch.setattr(settings, "worker_offline_after", 3.0)
    monkeypatch.setattr(settings, "worker_queue_timeout", 3.0)
    started: list[FakeWorker] = []

    def start(**kw):
        c, info = make_worker()
        w = FakeWorker(c, **kw)
        w.start()
        started.append(w)
        t0 = time.time()
        while time.time() - t0 < 5 and not as_user("root").get("/api/admin/workers").json()["workers"][0]["kinds"]:
            time.sleep(0.05)
        return w

    yield start
    for w in started:
        w.stop.set()
    for w in started:
        w.join(3)
    live.stop_all()


# ---------------------------------------------------------------- gestión y protocolo
def test_only_superuser_manages_workers_and_token_is_shown_once(client, world, db):
    assert as_user("adm").post("/api/admin/workers", json={"name": "gpu1"}).status_code == 403
    root = as_user("root")
    r = root.post("/api/admin/workers", json={"name": "gpu1"})
    token = r.json()["token"]
    assert token.startswith("vw_") and len(token) > 30
    assert root.post("/api/admin/workers", json={"name": "gpu1"}).status_code == 409
    lst = root.get("/api/admin/workers").json()
    assert lst["mode"] == "local" and lst["workers"][0]["online"] is False and "token" not in lst["workers"][0]
    assert token not in json.dumps(lst)  # en la base solo hay una huella


def test_worker_auth_register_and_revocation(client, world, db):
    anon = TestClient(app)
    assert anon.post("/api/worker/register", json={}).status_code == 401
    bad = TestClient(app)
    bad.headers["Authorization"] = "Bearer vw_mala"
    assert bad.post("/api/worker/heartbeat", json={}).status_code == 401
    c, info = make_worker()
    assert (
        c.post(
            "/api/worker/register",
            json={"kinds": ["train", "live", "minar"], "capabilities": {"gpus": [{"name": "RTX"}]}},
        ).status_code
        == 200
    )
    w = as_user("root").get("/api/admin/workers").json()["workers"][0]
    assert w["online"] and w["kinds"] == ["train", "live"] and w["capabilities"]["gpus"][0]["name"] == "RTX"
    assert as_user("root").delete(f"/api/admin/workers/{info['id']}").status_code == 200
    assert c.post("/api/worker/heartbeat", json={}).status_code == 401  # revocado


def test_claim_is_atomic_and_filters_by_kind(client, world, db):
    c1, _ = make_worker("gpu-a")
    c2, _ = make_worker("gpu-b")
    for c in (c1, c2):
        c.post("/api/worker/register", json={"kinds": ["train", "predict"]})
    jid = remote.submit("predict", {"x": 1})
    assert c1.post("/api/worker/jobs/claim", json={"kinds": ["train"], "wait": 0}).status_code == 204  # otro tipo
    got = c1.post("/api/worker/jobs/claim", json={"kinds": ["predict"], "wait": 0})
    assert got.status_code == 200 and got.json()["id"] == jid and got.json()["payload"] == {"x": 1}
    assert c2.post("/api/worker/jobs/claim", json={"kinds": ["predict"], "wait": 0}).status_code == 204  # ya lo tiene a
    assert c2.post(f"/api/worker/jobs/{jid}/finish", json={"status": "done"}).status_code == 404  # ajeno
    assert c1.post(f"/api/worker/jobs/{jid}/progress", json={"n": 1}).json() == {"cancel": False}
    remote.request_cancel(jid)
    assert c1.post("/api/worker/heartbeat", json={"running_jobs": [jid]}).json()["cancel_jobs"] == [jid]
    assert c1.post(f"/api/worker/jobs/{jid}/finish", json={"status": "cancelled"}).status_code == 200
    assert remote.get(jid)["status"] == "cancelled"


def test_register_fails_jobs_left_running_and_silence_reaps_them(client, world, db, monkeypatch):
    monkeypatch.setattr(settings, "worker_offline_after", 1.0)
    c, info = make_worker()
    c.post("/api/worker/register", json={"kinds": ["predict"]})
    j1 = remote.submit("predict", {})
    c.post("/api/worker/jobs/claim", json={"kinds": ["predict"], "wait": 0})
    c.post("/api/worker/register", json={"kinds": ["predict"]})  # el worker se reinició
    assert remote.get(j1)["status"] == "failed" and "reinició" in remote.get(j1)["error"]
    j2 = remote.submit("predict", {})
    c.post("/api/worker/jobs/claim", json={"kinds": ["predict"], "wait": 0})
    time.sleep(1.3)  # deja de dar señales
    as_user("root").get("/api/admin/workers")
    assert remote.get(j2)["status"] == "failed" and "dejó de responder" in remote.get(j2)["error"]


def test_local_mode_ignores_workers_and_remote_mode_needs_one(client, world, db, monkeypatch):
    from app.db import SessionLocal

    with SessionLocal() as s:
        assert remote.pick_worker(s, "train") is None  # local
    monkeypatch.setattr(settings, "gpu_mode", "remote")
    with SessionLocal() as s, pytest.raises(remote.RemoteUnavailable):
        remote.pick_worker(s, "train")
    monkeypatch.setattr(settings, "gpu_mode", "auto")
    with SessionLocal() as s:
        assert remote.pick_worker(s, "train") is None  # sin workers, auto vuelve a lo local


# ---------------------------------------------------------------- entrenamiento remoto
def wait_run(c, rid, tmo=30):
    t0 = time.time()
    while time.time() - t0 < tmo:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] in ("done", "failed", "cancelled"):
            return r
        time.sleep(0.2)
    raise AssertionError("el entrenamiento no terminó: " + str(c.get(f"/api/runs/{rid}").json()))


def test_training_runs_in_the_worker_and_registers_the_model(client, world, db, remote_mode):
    admin, pid, vid = trainable_world(db, world)
    w = remote_mode(handlers={"train": fake_train})
    rid = admin.post(
        f"/api/projects/{pid}/trainings",
        json={"dataset_version_id": vid, "epochs": 3, "imgsz": 320, "batch": 2, "model_name": "r1"},
    ).json()["run_id"]
    run = wait_run(admin, rid)
    assert run["status"] == "done", run
    assert run["metrics"]["map50"] == 0.5 and run["metrics"]["progress"]["epoch"] == 3 and "listo" in (run["log"] or "")
    m = admin.get(f"/api/projects/{pid}/models").json()[0]
    assert m["name"] == "r1" and m["metrics"]["recall"] == 0.7
    assert Path(db.query(Model).one().path).read_bytes() == b"pesos"
    job = db.query(__import__("app.models", fromlist=["WorkerJob"]).WorkerJob).one()
    assert (
        job.payload["weights"] == "yolov8n.pt"
        and "device" not in job.payload
        and job.payload["data"].endswith("data.yaml")
    )
    w.stop.set()


def test_cancelling_a_remote_training_reaches_the_worker(client, world, db, remote_mode):
    admin, pid, vid = trainable_world(db, world)

    def slow(w, job):
        for _ in range(100):
            time.sleep(0.2)
            if job["id"] in w.cancel_seen:
                return {"status": "cancelled", "log": "detenido"}
        return {"status": "done", "result": {}}

    remote_mode(handlers={"train": slow})
    rid = admin.post(
        f"/api/projects/{pid}/trainings",
        json={"dataset_version_id": vid, "epochs": 3, "imgsz": 320, "batch": 2, "model_name": "r2"},
    ).json()["run_id"]
    t0 = time.time()
    while time.time() - t0 < 10 and admin.get(f"/api/runs/{rid}").json()["status"] != "running":
        time.sleep(0.1)
    time.sleep(1.5)
    assert admin.post(f"/api/runs/{rid}/cancel").status_code == 200
    run = wait_run(admin, rid, 20)
    assert run["status"] == "cancelled" and not admin.get(f"/api/projects/{pid}/models").json()


def test_remote_training_without_workers_fails_with_a_clear_reason(client, world, db, remote_mode):
    admin, pid, vid = trainable_world(db, world)
    rid = admin.post(
        f"/api/projects/{pid}/trainings",
        json={"dataset_version_id": vid, "epochs": 3, "imgsz": 320, "batch": 2, "model_name": "r3"},
    ).json()["run_id"]
    run = wait_run(admin, rid)
    assert (
        run["status"] == "failed"
        and "ningún worker" in run["log"].lower()
        or "no hay ningún worker" in run["log"].lower()
    )


# ---------------------------------------------------------------- predicción remota
def test_detect_images_goes_through_the_worker(client, world, db, remote_mode):
    admin, pid, vid = trainable_world(db, world)
    db.add(
        Model(
            project_id=pid,
            name="mm",
            path="/data/x.pt",
            arch="yolov8n",
            classes=["louse", "fly"],
            params={},
            created_by=1,
        )
    )
    db.commit()
    mid = db.query(Model).one().id
    remote_mode(handlers={"predict": fake_predict})
    r = admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, "first": 1, "last": 2})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2 and items[0]["dets"][0]["label"] == "louse" and items[0]["dets"][0]["score"] == 0.9


def test_predict_times_out_when_no_worker_takes_the_job(client, world, db, remote_mode):
    admin, pid, vid = trainable_world(db, world)
    db.add(
        Model(project_id=pid, name="mm", path="/data/x.pt", arch="yolov8n", classes=["louse"], params={}, created_by=1)
    )
    db.commit()
    mid = db.query(Model).one().id
    c, _ = make_worker()
    c.post("/api/worker/register", json={"kinds": ["predict"]})  # en línea, pero nunca reclama
    r = admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, "first": 1})
    assert r.status_code == 424 and "ningún worker" in r.json()["detail"]


# ---------------------------------------------------------------- en vivo remoto
def live_handler_factory(video_path):
    live_yolo = Path(__file__).resolve().parents[2] / "workers" / "live_yolo.py"

    def handler(w, job):
        import socket
        import subprocess

        with socket.socket() as k:
            k.bind(("127.0.0.1", 0))
            port = k.getsockname()[1]
        p = subprocess.Popen(
            [
                sys.executable,
                str(live_yolo),
                "--source",
                job["payload"]["source"],
                "--detector",
                "fake",
                "--port",
                str(port),
                "--token",
                "tok-remoto",
                "--no-realtime",
                "--loop",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        w.procs.append(p)
        for line in p.stdout:
            if line.startswith("LISTO"):
                break
        w.c.post(
            f"/api/worker/jobs/{job['id']}/progress",
            json={"endpoint": f"http://127.0.0.1:{port}", "token": "tok-remoto", "pid": p.pid},
        )
        while p.poll() is None and job["id"] not in w.cancel_seen:
            time.sleep(0.2)
        if p.poll() is None:
            p.terminate()
            p.wait(5)
            return {"status": "cancelled"}
        return {"status": "failed", "error": "terminó"}

    return handler


def test_live_session_runs_in_the_worker_and_is_proxied(client, world, db, vfr_video, remote_mode):
    v = db.query(Video).filter(Video.name == "v1").one()
    v.path = str(vfr_video)
    m = Model(
        project_id=world["project"].id,
        name="m1",
        path="/data/m.pt",
        arch="yolov8n",
        classes=["louse", "fly"],
        params={},
        created_by=1,
    )
    db.add(m)
    db.commit()
    w = remote_mode(handlers={"live": live_handler_factory(vfr_video)})
    admin = as_user("adm")
    r = admin.post(
        f"/api/projects/{world['project'].id}/live",
        json={"source_kind": "video", "video_id": v.id, "model_id": m.id, "threshold": 0.25},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    sess = db.get(LiveSession, sid)
    assert sess.endpoint.startswith("http://127.0.0.1:") and sess.token == "tok-remoto" and sess.job_id
    t0, st = time.time(), None
    while time.time() - t0 < 20:
        st = admin.get(f"/api/live/{sid}").json()
        if st["stats"] and st["stats"]["frames"] > 0:
            break
        time.sleep(0.2)
    assert st["status"] == "running" and st["stats"]["frames"] > 0
    assert (
        admin.post(f"/api/live/{sid}/control", json={"show_boxes": False}).json()["show_boxes"] is False
    )  # los controles llegan al worker
    assert live.active_count() == 1
    assert admin.delete(f"/api/live/{sid}").status_code == 200
    t0 = time.time()
    while time.time() - t0 < 10 and remote.get(sess.job_id)["status"] != "cancelled":
        time.sleep(0.2)
    assert remote.get(sess.job_id)["status"] == "cancelled"
    assert w.procs[0].poll() is not None  # el proceso del worker se detuvo
    assert live.active_count() == 0


def test_live_without_workers_in_remote_mode_is_refused(client, world, db, vfr_video, remote_mode):
    v = db.query(Video).filter(Video.name == "v1").one()
    v.path = str(vfr_video)
    db.commit()
    r = as_user("adm").post(
        f"/api/projects/{world['project'].id}/live", json={"source_kind": "video", "video_id": v.id, "threshold": 0.25}
    )
    assert r.status_code == 424 and "worker" in r.json()["detail"].lower()
