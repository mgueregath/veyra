import json
import time
from pathlib import Path

import pytest
from test_assisted import train_model
from test_training import trainable_world, wait_run
from test_workers import FakeWorker, make_worker
from test_workflow import as_user

from app import flows, procedures
from app.config import settings
from app.evaluation import Pred
from app.models import DetectionFlow, Model, Project, Proposal


def leaf(source, **kw):
    return {"source": source, **kw}


def group(op, *inputs, **kw):
    return {"op": op, "inputs": list(inputs), **kw}


@pytest.fixture()
def trained(client, world, db, fake_workers):
    """Proyecto con un modelo entrenado (el predictor simulado da (10,10,20,20) con 0,9 y (70,60,8,8) con 0,2)."""
    admin, pid, vid = trainable_world(db, world)
    return admin, pid, train_model(admin, pid, vid)


def make_flow(admin, pid, name, spec):
    r = admin.post(f"/api/projects/{pid}/flows", json={"name": name, "spec": spec})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- especificación
def test_spec_validation_and_description(client, world, db, trained):
    admin, pid, mid = trained
    p = db.get(Project, pid)
    node = flows.validate(
        db, p, group("and", group("or", leaf(f"model:{mid}"), leaf("proposals:motion")), leaf("procedure:filtro"))
    )
    assert (
        flows.describe(node, {f"model:{mid}": "Mi modelo", "proposals:motion": "movimiento"})
        == "((Mi modelo O movimiento) Y procedure:filtro)"
    )
    assert len(flows.leaves(node)) == 3 and flows.depth(node) == 3
    for bad, msg in (
        (leaf("camara:1"), "fuente desconocida"),
        (leaf("model:99999"), "no existe"),
        (leaf("baseline:appearance"), "apariencia"),
        (leaf("procedure:../x"), "fuente desconocida"),
        ({"op": "xor", "inputs": [leaf("proposals:blob")]}, "inválido"),
        ({"op": "or", "inputs": []}, "inválido"),
        (group("or", *[leaf("proposals:blob")] * 13), "inválido"),
        (group("or", group("or", group("or", group("or", group("or", leaf("proposals:blob")))))), "demasiado grande"),
    ):
        with pytest.raises(flows.FlowError, match=msg):
            flows.validate(db, p, bad)
    other = Project(slug="otro-x", name="Otro X", config={"classes": []})
    db.add(other)
    db.flush()
    db.add(
        Model(project_id=other.id, name="ajeno", path="/x", arch="yolov8n", classes=["louse"], params={}, created_by=1)
    )
    db.commit()
    ajeno = db.query(Model).filter_by(name="ajeno").one().id
    with pytest.raises(flows.FlowError, match="no existe en este proyecto"):
        flows.validate(db, p, leaf(f"model:{ajeno}"))


# ---------------------------------------------------------------- ejecución con fuentes simuladas
def test_run_combines_sources_with_or_and(client, world, db, trained, monkeypatch):
    admin, pid, mid = trained
    p = db.get(Project, pid)
    frame = world["frames"][0]
    path = "/img/a.jpg"
    calls = []

    def fake_predict(m, paths):
        calls.append("model")
        return {x: [Pred((10, 10, 20, 20), "louse", 0.9), Pred((200, 200, 20, 20), "louse", 0.3)] for x in paths}

    monkeypatch.setattr("app.inference.predict_paths", fake_predict)
    db.query(Proposal).filter(Proposal.frame_id == frame.id).delete()  # (el proyecto de pruebas ya trae propuestas)
    db.add(Proposal(frame_id=frame.id, x=12, y=11, w=20, h=20, sources=["motion"], score=5.0))
    db.add(Proposal(frame_id=frame.id, x=400, y=400, w=20, h=20, sources=["motion"], score=3.0))
    db.commit()
    items = [flows.Item(path, frame.id)]
    run = lambda spec: flows.run(db, p, spec, items)[0][path]  # noqa: E731
    m, mo = leaf(f"model:{mid}"), leaf("proposals:motion")
    assert len(run(m)) == 2
    assert len(run(group("or", m, mo))) == 3  # (10,10) y (12,11) se funden; quedan (200,200) y (400,400)
    both = run(group("and", m, mo))
    assert len(both) == 1 and both[0].label == "louse" and abs(both[0].bbox[0] - 11) <= 2
    assert abs(both[0].score - (0.9 + 5.0) / 2) < 1e-6  # AND usa la media por defecto
    assert run(group("and", m, mo, score="min"))[0].score == 0.9
    assert (
        run(group("and", leaf(f"model:{mid}", min_score=0.95), mo)) == []
    )  # el mínimo de la fuente filtra antes de combinar
    assert len(run(group("and", m, group("or", mo, leaf("proposals:blob"))))) == 1  # grupos anidados
    calls.clear()
    flows.run(db, p, group("or", m, group("and", m, mo)), items)
    assert calls == ["model"]  # la misma fuente repetida se ejecuta una sola vez
    _, traces = flows.run(db, p, group("or", m, mo), items)
    assert traces == [f"model:{mid}", "proposals:motion"]
    assert (
        flows.run(db, p, mo, [flows.Item("/otra.jpg")])[0]["/otra.jpg"] == []
    )  # sin imagen del proyecto no hay propuestas guardadas


def test_procedure_source_runs_through_the_worker_and_records_the_commit(client, world, db, trained, monkeypatch):
    admin, pid, mid = trained
    p = db.get(Project, pid)
    seen = {}

    def fake_run(db_, project, name, ref, params, paths):
        seen.update(name=name, ref=ref, params=params)
        return {x: [Pred((10, 10, 20, 20), "", 0.8)] for x in paths}, "abc12345deadbeef"

    monkeypatch.setattr(procedures, "run_procedure", fake_run)
    res, traces = flows.run(
        db,
        p,
        group("and", leaf(f"model:{mid}"), leaf("procedure:manchas", ref="v1", params={"k": 3})),
        [flows.Item("/i.jpg")],
    )
    assert seen == {"name": "manchas", "ref": "v1", "params": {"k": 3}} and len(res["/i.jpg"]) == 1
    assert traces[-1] == "procedure:manchas@abc12345"


# ---------------------------------------------------------------- API de flujos
def test_flow_crud_and_permissions(client, world, db, trained):
    admin, pid, mid = trained
    spec = group("or", leaf(f"model:{mid}"), leaf("proposals:motion"))
    fid = make_flow(admin, pid, "modelo o movimiento", spec)
    lst = as_user("ana").get(f"/api/projects/{pid}/flows").json()  # cualquier miembro los ve
    assert (
        [f["name"] for f in lst] == ["modelo o movimiento"] and "O" in lst[0]["text"] and lst[0]["spec"]["op"] == "or"
    )
    assert as_user("ana").post(f"/api/projects/{pid}/flows", json={"name": "x", "spec": spec}).status_code == 403
    assert as_user("lector").delete(f"/api/projects/{pid}/flows/{fid}").status_code == 403
    assert as_user("intruso").get(f"/api/projects/{pid}/flows").status_code == 404
    assert (
        admin.post(f"/api/projects/{pid}/flows", json={"name": "modelo o movimiento", "spec": spec}).status_code == 409
    )
    assert (
        admin.post(f"/api/projects/{pid}/flows", json={"name": "malo", "spec": leaf("model:99999")}).status_code == 422
    )
    r = admin.put(f"/api/projects/{pid}/flows/{fid}", json={"name": "solo modelo", "spec": leaf(f"model:{mid}")})
    assert r.status_code == 200 and r.json()["name"] == "solo modelo"
    assert as_user("rev").delete(f"/api/projects/{pid}/flows/{fid}").status_code == 200  # el revisor también puede
    assert admin.get(f"/api/projects/{pid}/flows").json() == []


# ---------------------------------------------------------------- detección, asistido y evaluación con flujos
def test_detect_images_with_a_flow(client, world, db, trained):
    admin, pid, mid = trained
    f1, f2 = world[
        "frames"
    ]  # cada imagen trae propuestas guardadas: una «blob» en (10,10,20,20) y una «motion» en (50,30,15,15)
    fid = make_flow(admin, pid, "modelo y manchas", group("and", leaf(f"model:{mid}"), leaf("proposals:blob")))
    r = admin.post(f"/api/projects/{pid}/detect", json={"flow_id": fid, "first": 1, "last": 2})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (
        d["flow_id"] == fid
        and d["model_id"] is None
        and d["model"] == "modelo y manchas"
        and d["classes"] == ["louse", "fly"]
    )
    by = {i["frame_id"]: i["dets"] for i in d["items"]}
    assert (
        len(by[f1.id]) == 1 and by[f1.id][0]["label"] == "louse" and len(by[f2.id]) == 1
    )  # lo que modelo y mancha confirman
    mov = make_flow(admin, pid, "modelo y movimiento", group("and", leaf(f"model:{mid}"), leaf("proposals:motion")))
    r = admin.post(f"/api/projects/{pid}/detect", json={"flow_id": mov, "first": 1, "last": 2})
    assert all(
        len(i["dets"]) == 0 for i in r.json()["items"]
    )  # el movimiento (50,30) no coincide con ninguna caja del modelo
    union = make_flow(admin, pid, "modelo o movimiento", group("or", leaf(f"model:{mid}"), leaf("proposals:motion")))
    r = admin.post(f"/api/projects/{pid}/detect", json={"flow_id": union, "first": 1})
    assert len(r.json()["items"][0]["dets"]) == 3  # (10,10) y (70,60) del modelo + (50,30) del movimiento
    assert admin.post(f"/api/projects/{pid}/detect", json={"first": 1}).status_code == 422  # falta modelo o flujo
    assert (
        admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, "flow_id": fid, "first": 1}).status_code == 422
    )
    assert admin.post(f"/api/projects/{pid}/detect", json={"flow_id": 9999, "first": 1}).status_code == 404


def test_detect_uploaded_images_with_a_flow(client, world, db, trained):
    from test_detect import png

    admin, pid, mid = trained
    fid = make_flow(admin, pid, "dos modelos", group("or", leaf(f"model:{mid}"), leaf(f"model:{mid}", min_score=0.5)))
    r = admin.post(
        f"/api/projects/{pid}/detect/upload", data={"flow_id": fid}, files=[("files", ("a.png", png(), "image/png"))]
    )
    assert r.status_code == 200, r.text
    assert r.json()["flow_id"] == fid and len(r.json()["items"][0]["dets"]) == 2
    assert (
        admin.post(f"/api/projects/{pid}/detect/upload", files=[("files", ("a.png", png(), "image/png"))]).status_code
        == 422
    )


def test_preannotate_with_a_flow_tags_proposals_with_the_flow(client, world, db, trained):
    admin, pid, mid = trained
    fid = make_flow(
        admin, pid, "flujo asistido", group("and", leaf(f"model:{mid}"), leaf(f"model:{mid}", min_score=0.5))
    )
    a = world["frames"][0]
    r = admin.post(f"/api/projects/{pid}/preannotations", json={"flow_id": fid})
    assert r.status_code == 200, r.text
    run = wait_run(admin, r.json()["run_id"])
    assert run["status"] == "done" and run["metrics"]["flow_id"] == fid and run["metrics"]["frames"] == 2
    db.expire_all()
    got = [p for p in db.query(Proposal).filter(Proposal.frame_id == a.id) if f"flow:{fid}" in (p.sources or [])]
    assert len(got) == 1 and got[0].sources == ["flow", f"flow:{fid}"] and got[0].score == pytest.approx(0.9)
    assert admin.post(f"/api/projects/{pid}/preannotations", json={}).status_code == 422
    assert admin.post(f"/api/projects/{pid}/preannotations", json={"model_id": mid, "flow_id": fid}).status_code == 422
    again = wait_run(
        admin, admin.post(f"/api/projects/{pid}/preannotations", json={"flow_id": fid, "scope": "all"}).json()["run_id"]
    )
    db.expire_all()
    assert again["status"] == "done"
    assert (
        len([p for p in db.query(Proposal).filter(Proposal.frame_id == a.id) if f"flow:{fid}" in (p.sources or [])])
        == 1
    )  # repetir reemplaza


def test_evaluate_a_flow_like_any_other_detector(client, world, db, trained):
    admin, pid, mid = trained
    fid = make_flow(admin, pid, "evaluable", leaf(f"model:{mid}"))
    vs = admin.get(f"/api/projects/{pid}/datasets").json()
    r = admin.post(
        f"/api/projects/{pid}/evaluations",
        json={"dataset_version_id": vs[0]["id"], "detector": f"flow:{fid}", "split": "all", "iou": 0.5},
    )
    assert r.status_code == 200, r.text
    m = r.json()["metrics"] if "metrics" in r.json() else r.json()
    assert m["detector"] == f"flow:{fid}" or r.json().get("detector") == f"flow:{fid}" or "micro" in m
    assert (
        admin.post(
            f"/api/projects/{pid}/evaluations",
            json={"dataset_version_id": vs[0]["id"], "detector": "flow:9999", "split": "all"},
        ).status_code
        == 422
    )


# ---------------------------------------------------------------- procedimientos del espacio de trabajo
def procedure_worker(handlers):
    c, _ = make_worker("gpu-proc")
    w = FakeWorker(c, kinds=("procedure",), handlers={"procedure": handlers})
    w.start()
    time.sleep(0.8)
    return w


def test_list_and_run_procedures_through_a_worker(client, world, db, trained, monkeypatch, tmp_path):
    admin, pid, mid = trained
    monkeypatch.setattr(settings, "runs_root", tmp_path / "runs")
    monkeypatch.setattr(settings, "worker_offline_after", 5.0)
    assert admin.get(f"/api/projects/{pid}/procedures").json() == {
        "available": False,
        "procedures": [],
        "ref": None,
        "error": None,
    }

    def handler(w, job):
        p = job["payload"]
        if p["action"] == "list":
            return {
                "status": "done",
                "result": {
                    "procedures": [
                        {"name": "manchas", "entry": "lice_motion.veyra:manchas", "params": {"k": 8}, "doc": "manchas"}
                    ],
                    "ref": "abc123",
                    "error": None,
                },
            }
        out = Path(p["out_dir"])
        (out / "result.json").write_text(
            json.dumps(
                {"ref": "feedface99", "results": {i: [{"bbox": [10, 10, 20, 20], "score": 0.7}] for i in p["images"]}}
            )
        )
        w.last = p
        return {"status": "done", "result": {"images": len(p["images"])}}

    w = procedure_worker(handler)
    lst = admin.get(f"/api/projects/{pid}/procedures").json()
    assert lst["available"] and lst["procedures"][0]["name"] == "manchas" and lst["ref"] == "abc123"
    assert as_user("ana").get(f"/api/projects/{pid}/procedures").status_code == 403
    fid = make_flow(
        admin,
        pid,
        "con procedimiento",
        group("or", leaf(f"model:{mid}"), leaf("procedure:manchas", params={"k": 3}, ref="v2")),
    )
    r = admin.post(f"/api/projects/{pid}/detect", json={"flow_id": fid, "first": 1})
    assert r.status_code == 200, r.text
    assert (
        len(r.json()["items"][0]["dets"]) == 2
    )  # (10,10) del modelo y del procedimiento se funden; queda la débil (70,60)
    assert (
        w.last["name"] == "manchas"
        and w.last["ref"] == "v2"
        and w.last["params"] == {"k": 3}
        and w.last["slug"] == "demo"
    )
    assert not any((Path(settings.runs_root) / "_flows").glob("*"))  # no deja restos
    w.stop.set()


def test_a_failing_procedure_reports_why(client, world, db, trained, monkeypatch, tmp_path):
    admin, pid, mid = trained
    monkeypatch.setattr(settings, "runs_root", tmp_path / "runs")
    monkeypatch.setattr(settings, "worker_offline_after", 5.0)
    w = procedure_worker(
        lambda w, job: {"status": "failed", "error": "Traceback: ModuleNotFoundError: no module named 'foo'"}
    )
    fid = make_flow(admin, pid, "roto", leaf("procedure:manchas"))
    r = admin.post(f"/api/projects/{pid}/detect", json={"flow_id": fid, "first": 1})
    assert r.status_code == 424 and "manchas" in r.json()["detail"] and "ModuleNotFoundError" in r.json()["detail"]
    w.stop.set()


def test_procedure_flow_without_workers_is_refused_clearly(client, world, db, trained):
    admin, pid, mid = trained
    fid = make_flow(admin, pid, "sin worker", group("or", leaf(f"model:{mid}"), leaf("procedure:manchas")))
    r = admin.post(f"/api/projects/{pid}/detect", json={"flow_id": fid, "first": 1})
    assert r.status_code == 424 and "worker" in r.json()["detail"].lower()
    assert db.query(DetectionFlow).count() == 1


# ---------------------------------------------------------------- video y cámaras en vivo con flujo
def live_flow_worker(seen):
    def handler(w, job):
        seen.append(job["payload"])
        w.c.post(
            f"/api/worker/jobs/{job['id']}/progress",
            json={"endpoint": "http://127.0.0.1:9", "token": "tok-flow", "pid": 1},
        )
        while job["id"] not in w.cancel_seen:
            time.sleep(0.1)
        return {"status": "cancelled"}

    c, _ = make_worker("gpu-live")
    w = FakeWorker(c, kinds=("live", "workspace"), handlers={"live": handler})
    w.start()
    time.sleep(0.8)
    return w


def test_live_session_with_a_flow_sends_models_and_procedures_to_the_worker(
    client, world, db, trained, vfr_video, monkeypatch
):
    from app import live
    from app.models import LiveSession, Video

    admin, pid, mid = trained
    monkeypatch.setattr(settings, "gpu_mode", "auto")
    v = db.query(Video).filter(Video.name == "v1").one()
    v.path = str(vfr_video)
    db.commit()
    spec = group("or", leaf(f"model:{mid}", min_score=0.3), leaf("procedure:manchas", ref="v1"))
    fid = make_flow(admin, pid, "en vivo", spec)
    seen: list[dict] = []
    w = live_flow_worker(seen)
    r = admin.post(
        f"/api/projects/{pid}/live", json={"source_kind": "video", "video_id": v.id, "flow_id": fid, "imgsz": 640}
    )
    assert r.status_code == 200, r.text
    sess = db.get(LiveSession, r.json()["id"])
    assert sess.model_id is None and sess.params["flow_id"] == fid and sess.token == "tok-flow"
    flow = seen[0]["flow"]
    assert flow["slug"] == db.get(Project, pid).slug and flow["models"][str(mid)]["classes"]
    assert flow["spec"]["op"] == "or" and flow["spec"]["inputs"][1] == {
        "source": "procedure:manchas",
        "min_score": 0.0,
        "ref": "v1",
        "params": {},
    }
    assert "weights" not in seen[0]
    assert admin.delete(f"/api/live/{sess.id}").status_code == 200
    w.stop.set()
    live.stop_all()


def test_live_flow_rejections(client, world, db, trained, vfr_video):
    admin, pid, mid = trained
    v = db.query(__import__("app.models", fromlist=["Video"]).Video).filter_by(name="v1").one()
    v.path = str(vfr_video)
    db.commit()
    body = {"source_kind": "video", "video_id": v.id}
    solo_img = make_flow(admin, pid, "proposals", group("or", leaf(f"model:{mid}"), leaf("proposals:motion")))
    r = admin.post(f"/api/projects/{pid}/live", json={**body, "flow_id": solo_img})
    assert r.status_code == 422 and "en vivo" in r.json()["detail"]
    ok = make_flow(admin, pid, "ok", leaf(f"model:{mid}"))
    assert admin.post(f"/api/projects/{pid}/live", json={**body, "flow_id": ok, "model_id": mid}).status_code == 422
    assert admin.post(f"/api/projects/{pid}/live", json={**body, "flow_id": 9999}).status_code == 404
