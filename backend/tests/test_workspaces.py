import time

import pytest
from test_workers import FakeWorker, make_worker
from test_workflow import as_user

from app.config import settings
from app.models import SshKey

ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAdcTYJBw5A2ja8cNd1BUztHAk5zaMGLUwWZO4IU7CRD"
ED2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGJdoshHr5vBDYDtmoNaZ5uVQWSJYpJ+NAZ8MZBB4Kw0"


def fake_workspace(w, job):
    p = job["payload"]
    w.c.post(
        f"/api/worker/jobs/{job['id']}/progress",
        json={"host": "10.0.0.4", "port": p["port"], "user": "veyra", "workdir": "/workspace/project"},
    )
    w.seen = p
    for _ in range(300):
        if job["id"] in w.cancel_seen:
            return {"status": "cancelled", "result": {"reason": "stopped", "autosave": "autosave: sin cambios"}}
        time.sleep(0.1)
    return {"status": "done", "result": {"reason": "idle"}}


@pytest.fixture()
def ws_worker(world, db, monkeypatch):
    monkeypatch.setattr(settings, "worker_offline_after", 5.0)
    started = []

    def start():
        c, _ = make_worker("gpu-ws")
        w = FakeWorker(c, kinds=("workspace",), handlers={"workspace": fake_workspace})
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


def add_key(c, key=ED, name="portátil"):
    return c.post("/api/me/ssh-keys", json={"name": name, "public_key": key + " usuario@pc"})


def test_ssh_keys_are_validated_normalized_and_private(client, world, db):
    adm = as_user("adm")
    r = add_key(adm)
    assert r.status_code == 200 and r.json()["type"] == "ssh-ed25519" and r.json()["fingerprint"].startswith("SHA256:")
    assert db.query(SshKey).one().public_key == ED  # se guarda sin el comentario
    assert (
        add_key(adm).status_code == 409 and add_key(as_user("rev")).status_code == 409
    )  # ni repetida ni de otra persona
    for bad in ("hola mundo", "ssh-rsa AAAA", "ssh-ed25519 !!!", ED.replace("ssh-ed25519", "ssh-rsa"), ""):
        assert adm.post("/api/me/ssh-keys", json={"name": "x", "public_key": bad}).status_code == 422
    priv = adm.post("/api/me/ssh-keys", json={"name": "x", "public_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc"})
    assert priv.status_code == 422 and "PRIVADA" in priv.json()["detail"]
    assert [k["name"] for k in adm.get("/api/me/ssh-keys").json()] == ["portátil"]
    assert as_user("rev").get("/api/me/ssh-keys").json() == []  # cada quien ve solo las suyas
    kid = adm.get("/api/me/ssh-keys").json()[0]["id"]
    assert as_user("rev").delete(f"/api/me/ssh-keys/{kid}").status_code == 404
    assert adm.delete(f"/api/me/ssh-keys/{kid}").status_code == 200 and adm.get("/api/me/ssh-keys").json() == []


def test_only_project_admins_use_the_workspace(client, world):
    pid = world["project"].id
    for who, code in (("ana", 403), ("rev", 403), ("lector", 403), ("intruso", 404)):
        assert as_user(who).get(f"/api/projects/{pid}/workspace").status_code == code
        assert as_user(who).post(f"/api/projects/{pid}/workspace/start").status_code == code
    assert as_user("adm").get(f"/api/projects/{pid}/workspace").json()["status"] == "stopped"


def test_start_needs_a_key_and_a_worker(client, world, ws_worker):
    pid, adm = world["project"].id, as_user("adm")
    r = adm.post(f"/api/projects/{pid}/workspace/start")
    assert r.status_code == 422 and "llave" in r.json()["detail"]
    add_key(adm)
    r = adm.post(f"/api/projects/{pid}/workspace/start")
    assert r.status_code == 503 and "worker" in r.json()["detail"]
    assert adm.get(f"/api/projects/{pid}/workspace").json()["worker_available"] is False


def test_workspace_lifecycle_with_stable_port_and_only_admin_keys(client, world, db, ws_worker):
    pid, adm = world["project"].id, as_user("adm")
    add_key(adm)
    add_key(as_user("ana"), ED2, "de ana")  # ana no administra: su llave no entra
    w = ws_worker()
    g = adm.get(f"/api/projects/{pid}/workspace").json()
    assert g["worker_available"] and g["keys"] == {"with_key": ["adm"], "without_key": []} and g["port"] == 9100
    assert (
        adm.post(
            f"/api/projects/{pid}/workspace/start", json={"idle_minutes": 10, "cpus": 2, "memory": "4g"}
        ).status_code
        == 200
    )
    t0, st = time.time(), None
    while time.time() - t0 < 15:
        st = adm.get(f"/api/projects/{pid}/workspace").json()
        if st["status"] == "running":
            break
        time.sleep(0.2)
    assert st["status"] == "running" and st["host"] == "10.0.0.4" and st["port"] == 9100 and st["user"] == "veyra"
    assert (
        "Host veyra-demo" in st["ssh_config"]
        and "Port 9100" in st["ssh_config"]
        and "TU_LLAVE_PRIVADA" in st["ssh_config"]
    )
    assert (
        st["vscode_url"] == "vscode://vscode-remote/ssh-remote+veyra-demo/workspace/project"
        and "ssh-remote+veyra-demo" in st["vscode_cmd"]
    )
    p = w.seen
    assert (
        p["slug"] == "demo" and p["idle_minutes"] == 10 and p["cpus"] == 2 and p["memory"] == "4g" and p["gpu"] is True
    )
    assert (
        len(p["keys"]) == 1
        and p["keys"][0].startswith(ED)
        and "de ana" not in str(p["keys"])
        and ED2 not in str(p["keys"])
    )
    again = adm.post(f"/api/projects/{pid}/workspace/start").json()  # encender lo ya encendido no duplica nada
    assert again["status"] == "running" and again["port"] == 9100
    assert adm.post(f"/api/projects/{pid}/workspace/stop").status_code == 200
    t0 = time.time()
    while time.time() - t0 < 15 and adm.get(f"/api/projects/{pid}/workspace").json()["status"] != "stopped":
        time.sleep(0.2)
    s = adm.get(f"/api/projects/{pid}/workspace").json()
    assert s["status"] == "stopped" and s["last_stop"] == "stopped" and s["port"] == 9100  # el puerto no cambia
    assert adm.post(f"/api/projects/{pid}/workspace/start").json()["port"] == 9100


def test_each_project_gets_its_own_port_and_settings_persist(client, world, db, ws_worker):
    adm = as_user("adm")
    other = as_user("root").post("/api/projects", json={"name": "Segundo"}).json()
    ws_worker()
    a = adm.get(f"/api/projects/{world['project'].id}/workspace").json()["port"]
    b = as_user("root").get(f"/api/projects/{other['id']}/workspace").json()["port"]
    assert (a, b) == (9100, 9101)
    r = adm.patch(f"/api/projects/{world['project'].id}/workspace", json={"idle_minutes": 0, "memory": "16g"}).json()
    assert r["idle_minutes"] == 0 and r["memory"] == "16g"
    assert adm.patch(f"/api/projects/{world['project'].id}/workspace", json={"memory": "mucha"}).status_code == 422


def test_failed_workspace_reports_the_reason(client, world, ws_worker):
    pid, adm = world["project"].id, as_user("adm")
    add_key(adm)

    def broken(w, job):
        return {"status": "failed", "error": "el espacio no arrancó: sin espacio en disco"}

    c, _ = make_worker("gpu-ws")
    w = FakeWorker(c, kinds=("workspace",), handlers={"workspace": broken})
    w.start()
    time.sleep(1)
    adm.post(f"/api/projects/{pid}/workspace/start")
    t0, st = time.time(), {}
    while time.time() - t0 < 10 and st.get("status") != "failed":
        st = adm.get(f"/api/projects/{pid}/workspace").json()
        time.sleep(0.2)
    w.stop.set()
    assert st["status"] == "failed" and "sin espacio en disco" in st["error"]
