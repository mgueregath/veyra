from test_training import trainable_world, wait_run
from test_workflow import as_user, box, save

from app.models import Annotation, Proposal


def train_model(admin, pid, vid, name="m1"):
    rid = admin.post(
        f"/api/projects/{pid}/trainings",
        json={"dataset_version_id": vid, "epochs": 2, "imgsz": 320, "batch": 2, "model_name": name},
    ).json()["run_id"]
    assert wait_run(admin, rid)["status"] == "done"
    return admin.get(f"/api/projects/{pid}/models").json()[0]["id"]


def pre(admin, pid, mid, **kw):
    r = admin.post(f"/api/projects/{pid}/preannotations", json={"model_id": mid, **kw})
    return r


def model_props(db, frame_id, mid):
    db.expire_all()
    return [p for p in db.query(Proposal).filter(Proposal.frame_id == frame_id) if f"model:{mid}" in (p.sources or [])]


def test_preannotate_unreviewed_adds_model_boxes_and_skips_reviewed(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    a, b = world["frames"]  # sin revisar (las 30 del mundo de entrenamiento sí están revisadas)
    r = pre(admin, pid, mid)
    assert r.status_code == 200, r.text
    run = wait_run(admin, r.json()["run_id"])
    assert run["status"] == "done" and run["metrics"]["frames"] == 2  # solo las 2 sin revisar
    got = model_props(db, a.id, mid)
    # el predictor simulado da dos cajas por imagen (puntajes 0,9 y 0,2); ambas superan min_score=0,15
    assert len(got) == 2 and sorted(p.score for p in got) == [0.2, 0.9] and got[0].sources == ["model", f"model:{mid}"]
    reviewed = db.query(Annotation.frame_id).first()[0]
    assert model_props(db, reviewed, mid) == []  # lo ya revisado no se toca
    # la propuesta llega al etiquetador como caja sin etiquetar, con su fuente
    d = as_user("ana").get(f"/api/frames/{b.id}").json()
    assert any("model" in p["sources"] and p["score"] == 0.9 for p in d["proposals"])


def test_preannotate_replaces_previous_model_boxes_and_respects_min_score(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    a, _ = world["frames"]
    wait_run(admin, pre(admin, pid, mid).json()["run_id"])
    wait_run(admin, pre(admin, pid, mid, min_score=0.5).json()["run_id"])
    got = model_props(db, a.id, mid)
    assert [p.score for p in got] == [0.9]  # reemplazó (no duplicó) y filtró por puntaje
    # las propuestas de otras fuentes no se tocan
    assert db.query(Proposal).filter(Proposal.frame_id == a.id).count() == 2 + 1


def test_preannotate_scopes_validation_and_permissions(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    a, b = world["frames"]
    run = wait_run(admin, pre(admin, pid, mid, scope="frame_ids", frame_ids=[a.id]).json()["run_id"])
    assert run["metrics"]["frames"] == 1 and model_props(db, b.id, mid) == []
    assert pre(admin, pid, mid, scope="frame_ids", frame_ids=[99999]).status_code == 422
    assert pre(admin, pid, 999).status_code == 404
    assert pre(as_user("ana"), pid, mid).status_code == 403
    assert pre(as_user("intruso"), pid, mid).status_code == 404
    save(as_user("ana"), a.id, [box()])
    save(as_user("ana"), b.id, [box()])
    assert pre(admin, pid, mid, scope="unreviewed").status_code == 422  # ya no queda nada sin revisar
    assert wait_run(admin, pre(admin, pid, mid, scope="all").json()["run_id"])["metrics"]["frames"] == 32


def test_preannotation_failure_is_recorded(client, world, db, fake_workers, monkeypatch):
    from app.config import settings

    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    monkeypatch.setattr(settings, "predict_script", settings.train_script.parent / "no_existe.py")
    run = wait_run(admin, pre(admin, pid, mid).json()["run_id"])
    assert run["status"] == "failed" and "falló la inferencia" in run["log"]


def test_uncertain_ranking_and_reasons(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    a, b = world["frames"]
    tag = f"model:{mid}"
    db.query(Proposal).filter(Proposal.frame_id.in_([a.id, b.id])).delete()
    db.add_all([
        # a: el modelo duda (0,5) y el detector no ve nada igual
        Proposal(frame_id=a.id, x=10, y=10, w=20, h=20, sources=["model", tag], score=0.5),
        # b: el modelo está seguro y coincide con el detector: el menos urgente
        Proposal(frame_id=b.id, x=10, y=10, w=20, h=20, sources=["model", tag], score=0.97),
        Proposal(frame_id=b.id, x=10, y=10, w=20, h=20, sources=["blob"], score=8.0),
    ])  # fmt: skip
    db.commit()
    rows = admin.get(f"/api/projects/{pid}/uncertain", params={"model_id": mid}).json()
    assert [r["frame_id"] for r in rows] == [a.id, b.id]
    assert rows[0]["score"] > rows[1]["score"] and "el modelo duda" in rows[0]["reasons"] and rows[1]["reasons"] == []
    # discrepancia: el detector ve algo fuerte que el modelo ignora (y a ya no es dudosa)
    db.query(Proposal).filter(Proposal.frame_id == a.id).update({"score": 0.9})
    db.add(Proposal(frame_id=a.id, x=10, y=10, w=20, h=20, sources=["blob"], score=8.0))  # coincide con el modelo
    db.add(Proposal(frame_id=b.id, x=60, y=50, w=10, h=10, sources=["blob"], score=9.0))
    db.commit()
    rows = admin.get(f"/api/projects/{pid}/uncertain", params={"model_id": mid}).json()
    assert rows[0]["frame_id"] == b.id and "el detector ve algo que el modelo no" in rows[0]["reasons"]
    assert admin.get(f"/api/projects/{pid}/uncertain", params={"model_id": mid, "limit": 1}).json().__len__() == 1
    assert admin.get(f"/api/projects/{pid}/uncertain", params={"model_id": 999}).status_code == 404
    assert as_user("ana").get(f"/api/projects/{pid}/uncertain", params={"model_id": mid}).status_code == 403


def test_uncertain_skips_reviewed_and_frames_without_model_boxes(client, world, db, fake_workers):
    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    a, b = world["frames"]
    db.add(Proposal(frame_id=a.id, x=1, y=1, w=5, h=5, sources=["model", f"model:{mid}"], score=0.5))
    db.add(Proposal(frame_id=b.id, x=1, y=1, w=5, h=5, sources=["model", f"model:{mid}"], score=0.5))
    db.commit()
    save(as_user("ana"), a.id, [box()])  # a ya está revisada
    rows = admin.get(f"/api/projects/{pid}/uncertain", params={"model_id": mid}).json()
    assert [r["frame_id"] for r in rows] == [b.id]  # solo lo que falta por revisar y que el modelo vio
