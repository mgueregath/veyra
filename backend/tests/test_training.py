import time

from test_workflow import as_user, box, save

from app import training
from app.models import Frame, Model, Run, Video


def wait_run(c, rid, states=("done", "failed", "cancelled"), tmo=30):
    t0 = time.time()
    while time.time() - t0 < tmo:
        r = c.get(f"/api/runs/{rid}").json()
        if r["status"] in states:
            return r
        time.sleep(0.1)
    raise AssertionError("la ejecución no terminó: " + str(c.get(f"/api/runs/{rid}").json()["status"]))


def trainable_world(db, world, n_videos=30):
    """Muchos videos con una imagen etiquetada cada uno, para que train y val tengan imágenes."""
    ana = as_user("ana")
    for i in range(n_videos):
        v = Video(project_id=world["project"].id, name=f"vid{i}", path=None, meta={})
        db.add(v)
        db.flush()
        f = Frame(
            project_id=world["project"].id,
            video_id=v.id,
            frame_index=1,
            t=0.0,
            image_path="f0.jpg",
            width=100,
            height=80,
            position=10 + i,
            meta={},
        )
        db.add(f)
        db.commit()
        save(ana, f.id, [box((10, 10, 20, 20))])
    admin = as_user("adm")
    pid = world["project"].id
    return (
        admin,
        pid,
        admin.post(f"/api/projects/{pid}/datasets", json={"name": "d1", "split": [0.6, 0.4, 0], "seed": "t"}).json()[
            "id"
        ],
    )


def start(admin, pid, vid, **kw):
    return admin.post(
        f"/api/projects/{pid}/trainings", json={"dataset_version_id": vid, "epochs": 3, "imgsz": 320, "batch": 2, **kw}
    )


def test_training_success_registers_model_and_metrics(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    r = start(admin, pid, vid, model_name="m1")
    assert r.status_code == 200, r.text
    run = wait_run(admin, r.json()["run_id"])
    assert run["status"] == "done", run
    assert run["metrics"]["map50"] == 0.5 and run["metrics"]["progress"]["epoch"] == 3
    models = as_user("rev").get(f"/api/projects/{pid}/models").json()
    assert (
        [m["name"] for m in models] == ["m1"]
        and models[0]["classes"] == ["louse", "fly"]
        and models[0]["metrics"]["recall"] == 0.7
    )
    assert models[0]["arch"] == "yolov8n" and models[0]["dataset_version_id"] == vid
    from pathlib import Path

    assert Path(db.query(Model).one().path).read_bytes() == b"pesos-falsos"
    assert "cargando datos" in run["log"]
    # el dataset se exportó para entrenar (YOLO con data.yaml y particiones)
    assert (
        training.Path(__import__("app.config", fromlist=["settings"]).settings.runs_root)
        / str(run["id"])
        / "dataset"
        / "data.yaml"
    ).is_file()


def test_training_failure_is_recorded_without_a_model(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    rid = start(admin, pid, vid, epochs=7).json()["run_id"]
    run = wait_run(admin, rid)
    assert run["status"] == "failed" and "código 3" in run["log"] and "Traceback" in run["log"]
    assert db.query(Model).count() == 0


def test_training_cancel_kills_the_process(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    rid = start(admin, pid, vid, epochs=9).json()["run_id"]
    wait_run(admin, rid, states=("running",))
    t0 = time.time()
    assert admin.post(f"/api/runs/{rid}/cancel").status_code == 200
    run = wait_run(admin, rid, states=("cancelled",), tmo=15)
    assert run["status"] == "cancelled" and time.time() - t0 < 12  # no esperó los 9 minutos del «entrenamiento»
    assert db.query(Model).count() == 0
    assert admin.post(f"/api/runs/{rid}/cancel").status_code == 409  # ya terminó


def test_second_training_waits_in_the_queue(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    r1 = start(admin, pid, vid, epochs=9).json()["run_id"]
    wait_run(admin, r1, states=("running",))
    r2 = start(admin, pid, vid, epochs=2).json()["run_id"]
    time.sleep(0.5)
    assert admin.get(f"/api/runs/{r2}").json()["status"] == "queued"  # una sola GPU: no corren a la vez
    admin.post(f"/api/runs/{r1}/cancel")
    assert wait_run(admin, r2)["status"] == "done"  # al liberarse, la siguiente arranca sola


def test_cancel_while_queued(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    r1 = start(admin, pid, vid, epochs=9).json()["run_id"]
    wait_run(admin, r1, states=("running",))
    r2 = start(admin, pid, vid, epochs=2).json()["run_id"]
    assert admin.post(f"/api/runs/{r2}/cancel").status_code == 200
    admin.post(f"/api/runs/{r1}/cancel")
    wait_run(admin, r1, states=("cancelled",))
    time.sleep(0.8)
    assert admin.get(f"/api/runs/{r2}").json()["status"] == "cancelled" and db.query(Model).count() == 0


def test_training_validation_and_permissions(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    assert start(admin, pid, vid, arch="gpt").status_code == 422
    assert start(admin, pid, vid, epochs=0).status_code == 422
    assert start(admin, pid, 999).status_code == 404
    assert start(as_user("ana"), pid, vid).status_code == 403
    assert start(as_user("intruso"), pid, vid).status_code == 404
    ok = start(admin, pid, vid, model_name="dup", epochs=2)
    wait_run(admin, ok.json()["run_id"])
    assert start(admin, pid, vid, model_name="dup").status_code == 409
    # versión sin imágenes de validación o sin cajas
    only_train = admin.post(f"/api/projects/{pid}/datasets", json={"name": "solo-train", "split": [1, 0, 0]}).json()[
        "id"
    ]
    assert "val" in start(admin, pid, only_train).json()["detail"]
    assert db.query(Run).filter(Run.kind == "training").count() == 1  # los rechazados no dejan ejecuciones


def test_recover_interrupted_marks_stuck_runs(client, world, db):
    admin, pid, vid = trainable_world(db, world)
    r = Run(
        project_id=pid, kind="training", name="x", dataset_version_id=vid, params={}, status="running", created_by=1
    )
    q = Run(project_id=pid, kind="training", name="y", dataset_version_id=vid, params={}, status="queued", created_by=1)
    d = Run(project_id=pid, kind="training", name="z", dataset_version_id=vid, params={}, status="done", created_by=1)
    db.add_all([r, q, d])
    db.commit()
    assert training.recover_interrupted() == 2
    db.expire_all()
    assert [db.get(Run, i.id).status for i in (r, q, d)] == ["failed", "failed", "done"] and "interrumpido" in db.get(
        Run, r.id
    ).log


def test_evaluate_a_trained_model(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    rid = start(admin, pid, vid, model_name="m1", epochs=2).json()["run_id"]
    wait_run(admin, rid)
    mid = admin.get(f"/api/projects/{pid}/models").json()[0]["id"]
    r = admin.post(
        f"/api/projects/{pid}/evaluations",
        json={"dataset_version_id": vid, "split": "all", "detector": f"model:{mid}", "score_threshold": 0.5},
    )
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    # el predictor simulado acierta la caja (10,10,20,20) de cada imagen etiquetada y su 2.º resultado queda bajo el umbral
    assert m["micro"]["fp"] == 0 and m["micro"]["fn"] == 0 and m["micro"]["tp"] == 30 and m["map"] == 1.0
    assert (
        admin.post(
            f"/api/projects/{pid}/evaluations",
            json={"dataset_version_id": vid, "split": "all", "detector": "model:999"},
        ).status_code
        == 422
    )
    assert admin.get(f"/api/models/{mid}").json()["name"] == "m1"
    assert (
        as_user("ana").get(f"/api/models/{mid}").status_code == 200
    )  # cualquier miembro puede ver los indicadores del modelo
    assert as_user("ana").get(f"/api/projects/{pid}/models").json()[0]["name"] == "m1"
    assert as_user("intruso").get(f"/api/models/{mid}").status_code == 404
    assert as_user("intruso").get(f"/api/projects/{pid}/models").status_code == 404
