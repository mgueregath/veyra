from test_workflow import as_user, box, save

from app.models import DatasetVersion, Frame


def ids(c, pid, flt="all"):
    return [i["id"] for i in c.get(f"/api/projects/{pid}/frames?filter={flt}").json()]


def test_delete_hides_the_capture_everywhere_and_restore_brings_it_back(client, world, db):
    pid = world["project"].id
    a, b = (x.id for x in world["frames"])
    adm, ana = as_user("adm"), as_user("ana")
    save(ana, a, [box(label="louse")])
    assert ana.get("/api/projects").json()[0]["n_frames"] == 2
    r = adm.delete(f"/api/frames/{a}")
    assert r.status_code == 200 and r.json() == {"ok": True, "id": a}
    assert ids(ana, pid) == [b]  # ya no está en la lista
    assert ana.get("/api/projects").json()[0]["n_frames"] == 1 and ana.get("/api/projects").json()[0]["n_reviewed"] == 0
    assert ana.get(f"/api/projects/{pid}/stats").json()["frames"] == 1
    assert ana.post(f"/api/projects/{pid}/queue/next", json={"scope": "any"}).json()["frame_id"] == b
    q = adm.get(f"/api/projects/{pid}/quality").json()
    assert q["frames"] == 1 and q["status"]["single"] == 0  # su revisión ya no cuenta
    assert ids(adm, pid, "trash") == [a]
    d = adm.get(f"/api/frames/{a}").json()
    assert d["deleted"]["by"] == "adm"
    assert adm.delete(f"/api/frames/{a}").status_code == 200  # idempotente
    assert adm.post(f"/api/frames/{a}/restore").status_code == 200
    assert sorted(ids(ana, pid)) == sorted([a, b]) and ids(adm, pid, "trash") == []
    assert ana.get(f"/api/frames/{a}").json()["annotation"]["boxes"][0]["label"] == "louse"  # no se perdió nada


def test_only_admin_and_reviewer_can_trash_and_see_the_bin(client, world):
    a, pid = world["frames"][0].id, world["project"].id
    for who, code in (("ana", 403), ("lector", 403), ("intruso", 404)):
        c = as_user(who)
        assert c.delete(f"/api/frames/{a}").status_code == code
        assert c.post(f"/api/frames/{a}/restore").status_code == code
    assert as_user("ana").get(f"/api/projects/{pid}/frames?filter=trash").status_code == 403
    assert as_user("rev").delete(f"/api/frames/{a}").status_code == 200  # el revisor también puede


def test_a_trashed_capture_cannot_be_edited(client, world):
    a = world["frames"][0].id
    as_user("adm").delete(f"/api/frames/{a}")
    r = as_user("ana").put(f"/api/frames/{a}/annotation", json={"reviewed": True, "boxes": [box()]})
    assert r.status_code == 409 and "papelera" in r.json()["detail"]


def test_new_datasets_skip_trashed_but_existing_versions_keep_them(client, world, db):
    pid = world["project"].id
    a, b = (x.id for x in world["frames"])
    adm = as_user("adm")
    for f in (a, b):
        save(as_user("ana"), f, [box(label="louse")])
    v1 = adm.post(
        f"/api/projects/{pid}/datasets", json={"name": "v1", "include": ["single"], "split": [1, 0, 0], "seed": "s"}
    )
    assert v1.status_code == 200, v1.text
    adm.delete(f"/api/frames/{a}")
    v2 = adm.post(
        f"/api/projects/{pid}/datasets", json={"name": "v2", "include": ["single"], "split": [1, 0, 0], "seed": "s"}
    ).json()
    db.expire_all()
    items = {v.name: sorted(i.frame_id for i in v.items) for v in db.query(DatasetVersion).all()}
    assert items["v1"] == sorted([a, b]) and items["v2"] == [b]  # inmutable: v1 conserva la eliminada
    d = adm.get(f"/api/frames/{a}").json()
    assert d["in_datasets"] == ["v1"] and d["deleted"] is not None  # el aviso para quien la elimina
    assert v2["n_items"] == 1


def test_image_numbers_in_detection_follow_the_visible_numbering(client, world, db, fake_workers):
    from test_assisted import train_model
    from test_training import trainable_world

    admin, pid, vid = trainable_world(db, world)
    mid = train_model(admin, pid, vid)
    first = db.query(Frame).filter(Frame.project_id == pid).order_by(Frame.position).first().id
    admin.delete(f"/api/frames/{first}")  # la imagen 1 desaparece: la que era 2 pasa a ser 1
    r = admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, "first": 1, "last": 2}).json()
    assert [i["name"] for i in r["items"]] == ["Imagen 1", "Imagen 2"] and first not in [
        i["frame_id"] for i in r["items"]
    ]


def test_backup_roundtrip_keeps_the_bin(client, world, db):
    from app import backup

    a = world["frames"][0].id
    as_user("adm").delete(f"/api/frames/{a}")
    dump = backup.dump_project(db, world["project"])
    assert [f["deleted"] for f in dump["frames"]].count(True) == 1


def test_saving_the_snapshot_of_a_trashed_frame_restores_it(client, vworld, db, media):
    src = vworld["frames"][0]
    ana = as_user("ana")
    pts = ana.get(f"/api/videos/{vworld['video'].id}/pts").json()["pts"]
    snap = ana.post(f"/api/frames/{src.id}/snapshot", json={"t": pts[40]}).json()
    as_user("adm").delete(f"/api/frames/{snap['id']}")
    assert snap["id"] not in ids(ana, vworld["project"].id)
    again = ana.post(f"/api/frames/{src.id}/snapshot", json={"t": pts[40]}).json()  # el mismo fotograma
    assert again["id"] == snap["id"] and again["restored"] is True and again["existing"] is False
    assert snap["id"] in ids(ana, vworld["project"].id)
