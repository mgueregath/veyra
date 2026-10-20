from test_workflow import as_user, box, save

from app.models import Annotation
from app.workflow import project_mode


def collab(world):
    adm = as_user("adm")
    r = adm.patch(f"/api/projects/{world['project'].id}", json={"mode": "collab"})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["mode"] == "collab"
    return adm


def test_default_mode_is_review_and_only_admin_changes_it(client, world, db):
    assert project_mode(world["project"]) == "review"
    for who, code in (("ana", 403), ("rev", 403), ("intruso", 404)):
        assert as_user(who).patch(f"/api/projects/{world['project'].id}", json={"mode": "collab"}).status_code == code
    assert as_user("adm").patch(f"/api/projects/{world['project'].id}", json={"mode": "otro"}).status_code == 422
    collab(world)
    db.refresh(world["project"])
    assert project_mode(world["project"]) == "collab"


def test_create_project_in_collab_mode(client, world):
    r = as_user("intruso").post("/api/projects", json={"name": "Equipo", "mode": "collab"})
    assert r.status_code == 200 and r.json()["config"]["mode"] == "collab"
    assert as_user("intruso").post("/api/projects", json={"name": "Otro equipo"}).json()["config"]["mode"] == "review"


def test_last_modification_is_the_final_version(client, world):
    collab(world)
    f = world["frames"][0].id
    ana, beto = as_user("ana"), as_user("beto")
    save(ana, f, [box(label="louse")])
    d = beto.get(f"/api/frames/{f}").json()
    assert d["status"] == "final" and d["last_edit"]["user"] == "ana"  # la de ana ya es la vigente
    assert [b["label"] for b in d["annotation"]["boxes"]] == ["louse"]  # beto ve y parte de la versión vigente
    save(beto, f, [box(label="fly"), box((50, 30, 15, 15), label="louse")])
    d = ana.get(f"/api/frames/{f}").json()
    assert d["last_edit"]["user"] == "beto" and sorted(b["label"] for b in d["annotation"]["boxes"]) == ["fly", "louse"]
    save(ana, f, [box(label="fly")])  # y ahora la última es la de ana otra vez
    d = beto.get(f"/api/frames/{f}").json()
    assert d["last_edit"]["user"] == "ana" and [b["label"] for b in d["annotation"]["boxes"]] == ["fly"]
    assert d["status"] == "final" and d["n_reviewed"] == 1


def test_unmarking_reviewed_makes_the_image_pending_again(client, world):
    collab(world)
    f = world["frames"][0].id
    ana, beto = as_user("ana"), as_user("beto")
    save(ana, f, [box()], reviewed=True)
    assert beto.get(f"/api/frames/{f}").json()["status"] == "final"
    save(beto, f, [box()], reviewed=False)  # la última modificación dice «sin revisar»
    assert beto.get(f"/api/frames/{f}").json()["status"] == "pending"


def test_list_shows_current_version_and_queue_skips_done_images(client, world):
    collab(world)
    a, b = (x.id for x in world["frames"])
    ana, beto = as_user("ana"), as_user("beto")
    save(ana, a, [box(label="louse")])
    items = {i["id"]: i for i in beto.get(f"/api/projects/{world['project'].id}/frames").json()}
    assert items[a]["status"] == "final" and items[a]["reviewed"] and items[a]["n_labeled"] == {"louse": 1}  # lo de ana
    assert items[b]["status"] == "pending"
    pid = world["project"].id
    nxt = beto.post(f"/api/projects/{pid}/queue/next", json={"scope": "any"}).json()
    assert nxt["frame_id"] == b  # la a ya está hecha por otra persona: no se vuelve a ofrecer
    pend = beto.get(f"/api/projects/{pid}/frames?filter=mine_pending").json()
    assert [i["id"] for i in pend] == [b]


def test_review_decisions_are_not_allowed_and_quality_has_no_agreement(client, world):
    collab(world)
    f = world["frames"][0].id
    save(as_user("ana"), f, [box()])
    rev = as_user("rev")
    r = rev.post(f"/api/frames/{f}/review", json={"final_user_id": world["users"]["ana"].id})
    assert r.status_code == 409 and "colaborativo" in r.json()["detail"]
    assert rev.delete(f"/api/frames/{f}/review").status_code == 409
    q = rev.get(f"/api/projects/{world['project'].id}/quality").json()
    assert q["mode"] == "collab" and q["status"]["final"] == 1 and q["status"]["disagreement"] == 0
    assert q["agreement"]["frames_with_overlap"] == 0 and q["agreement"]["mean_f1"] is None


def test_double_labeling_is_ignored_and_dataset_uses_latest(client, world, db):
    collab(world)
    pid, f = world["project"].id, world["frames"][0].id
    adm = as_user("adm")
    r = adm.post(f"/api/projects/{pid}/assignments", json={"users": ["ana", "beto"], "overlap_pct": 100, "seed": 1})
    assert r.status_code == 200 and r.json()["overlap"] == 0
    save(as_user("ana"), f, [box(label="louse")])
    save(as_user("beto"), f, [box(label="fly")])
    from app.workflow import compute_states, final_boxes

    db.expire_all()
    st = compute_states(db, world["project"])[f]
    assert [b["label"] for b in final_boxes(db, f, st)] == ["fly"]  # el dataset toma la última versión
    assert db.query(Annotation).filter_by(frame_id=f).count() == 2  # el historial por persona se conserva


def test_back_to_review_mode_restores_individual_reviews(client, world):
    adm = collab(world)
    f, pid = world["frames"][0].id, world["project"].id
    save(as_user("ana"), f, [box(label="louse")])
    save(as_user("beto"), f, [box(label="fly")])
    assert adm.get(f"/api/frames/{f}").json()["status"] == "final"
    assert adm.patch(f"/api/projects/{pid}", json={"mode": "review"}).status_code == 200
    assert adm.get(f"/api/frames/{f}").json()["status"] == "disagreement"  # vuelven a contar como dos revisiones


def test_progress_counts_the_whole_project_in_collab(client, world):
    pid = world["project"].id
    save(as_user("ana"), world["frames"][0].id, [box()])
    beto = as_user("beto")
    assert beto.get("/api/projects").json()[0]["n_reviewed"] == 0  # revisión: cada persona lo suyo
    collab(world)
    assert beto.get("/api/projects").json()[0]["n_reviewed"] == 1  # colaborativa: lo del proyecto
    assert beto.get(f"/api/projects/{pid}/stats").json()["reviewed"] == 1
