from test_workflow import as_user, box, save

from app.models import Run


def labeled_world(world):
    """f0: un piojo en (10,10,20,20) · f1: sin piojos (solo una caja «ni uno»). Propuestas: blob en (10,10,20,20), motion en (50,30,15,15)."""
    a, b = world["frames"]
    ana = as_user("ana")
    save(ana, a.id, [box((10, 10, 20, 20))])
    save(ana, b.id, [box((50, 30, 15, 15), "not_louse")])
    admin = as_user("adm")
    pid = world["project"].id
    vid = admin.post(f"/api/projects/{pid}/datasets", json={"name": "v1", "split": [1, 0, 0]}).json()["id"]
    return admin, pid, vid, (a, b)


def ev(admin, pid, vid, detector, **kw):
    return admin.post(
        f"/api/projects/{pid}/evaluations",
        json={"dataset_version_id": vid, "split": "train", "detector": detector, **kw},
    )


def test_appearance_baseline_metrics(client, world):
    admin, pid, vid, (a, b) = labeled_world(world)
    r = ev(admin, pid, vid, "baseline:appearance", score_threshold=5)
    assert r.status_code == 200, r.text
    m = r.json()["metrics"]
    assert (m["micro"]["tp"], m["micro"]["fp"], m["micro"]["fn"]) == (
        1,
        1,
        0,
    )  # acierta f0 y falla en la imagen sin piojos
    assert m["map"] == 1.0 and m["threshold_choice"] == "fijo" and m["score_threshold"] == 5
    assert m["per_class"]["louse"]["gt"] == 1 and m["detector"] == "baseline:appearance" and m["dataset"] == "v1"
    fp = m["examples"]["false_positives"][0]
    assert fp["frame_id"] == b.id and fp["w"] == 100 and fp["h"] == 80 and fp["video"] == "v1"
    assert r.json()["status"] == "done" and r.json()["summary"]["precision"] == 0.5


def test_motion_baseline_misses_and_best_f1_threshold(client, world):
    admin, pid, vid, (a, b) = labeled_world(world)
    m = ev(admin, pid, vid, "baseline:motion").json()["metrics"]
    assert (m["micro"]["tp"], m["micro"]["fn"]) == (0, 1) and m["map"] == 0.0
    assert m["examples"]["false_negatives"][0]["frame_id"] == a.id
    best = ev(admin, pid, vid, "baseline:appearance").json()["metrics"]  # sin umbral fijo: lo elige y lo dice
    assert best["threshold_choice"] == "mejor F1 en este conjunto" and best["score_threshold"] == 7.0


def test_evaluation_validation_permissions_and_records(client, world, db):
    admin, pid, vid, _ = labeled_world(world)
    assert ev(admin, pid, vid, "baseline:nope").status_code == 422
    assert ev(admin, pid, vid, "inventado").status_code == 422
    assert (
        admin.post(
            f"/api/projects/{pid}/evaluations",
            json={"dataset_version_id": vid, "split": "val", "detector": "baseline:appearance"},
        ).status_code
        == 422
    )
    assert (
        admin.post(
            f"/api/projects/{pid}/evaluations", json={"dataset_version_id": 999, "detector": "baseline:appearance"}
        ).status_code
        == 404
    )
    assert (
        as_user("ana")
        .post(
            f"/api/projects/{pid}/evaluations",
            json={"dataset_version_id": vid, "split": "train", "detector": "baseline:appearance"},
        )
        .status_code
        == 403
    )
    assert (
        as_user("intruso")
        .post(f"/api/projects/{pid}/evaluations", json={"dataset_version_id": vid, "detector": "baseline:appearance"})
        .status_code
        == 404
    )
    # las ejecuciones fallidas también quedan registradas, con el motivo
    failed = [r for r in db.query(Run).all() if r.status == "failed"]
    assert len(failed) >= 3 and all(r.log for r in failed)
    ok = ev(admin, pid, vid, "baseline:appearance", score_threshold=5).json()
    runs = as_user("rev").get(f"/api/projects/{pid}/runs", params={"kind": "evaluation"}).json()
    assert runs[0]["id"] == ok["id"] and runs[0]["created_by"] == "adm" and runs[0]["summary"]["map"] == 1.0
    full = as_user("rev").get(f"/api/runs/{ok['id']}").json()
    assert full["metrics"]["micro"]["tp"] == 1 and full["params"]["detector"] == "baseline:appearance"
    assert as_user("ana").get(f"/api/projects/{pid}/runs").status_code == 403
    assert as_user("intruso").get(f"/api/runs/{ok['id']}").status_code == 404
