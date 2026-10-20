import datetime as dt
import threading

from conftest import login
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import DatasetItem, Frame, FrameLock, Proposal, Video


def box(bbox=(10, 10, 20, 20), label="louse", **kw):
    return {"bbox": list(bbox), "label": label, **kw}


def as_user(name):
    c = TestClient(app)
    assert login(c, name).status_code == 200
    return c


def save(c, frame_id, boxes, reviewed=True):
    r = c.put(f"/api/frames/{frame_id}/annotation", json={"reviewed": reviewed, "boxes": boxes})
    assert r.status_code == 200, r.text
    return r.json()


def more_frames(db, world, n):
    """Agrega n imágenes más al proyecto (para probar colas y repartos)."""
    v = db.scalar(select(Video).where(Video.name == "v1"))
    base = len(world["frames"])
    out = []
    for i in range(n):
        f = Frame(
            project_id=world["project"].id,
            video_id=v.id,
            frame_index=100 + i,
            t=float(i),
            image_path="f0.jpg",
            width=100,
            height=80,
            position=base + i,
            meta={},
        )
        f.proposals = [Proposal(x=1, y=1, w=5, h=5, sources=["blob"], score=5.0)]
        db.add(f)
        out.append(f)
    db.commit()
    return out


# ---------- cola y bloqueos ----------
def test_queue_assigned_scope_only_gives_my_frames(client, world, db):
    a, b = world["frames"]
    ana = as_user("ana")
    assert (
        ana.post(f"/api/projects/{world['project'].id}/queue/next", json={"scope": "assigned"}).json()["frame_id"]
        is None
    )
    admin = as_user("adm")
    r = admin.post(f"/api/projects/{world['project'].id}/assignments", json={"users": ["ana"], "frame_ids": [b.id]})
    assert r.json()["created"] == 1
    got = ana.post(f"/api/projects/{world['project'].id}/queue/next", json={"scope": "assigned"}).json()
    assert got["frame_id"] == b.id
    save(ana, b.id, [box()])
    assert (
        ana.post(f"/api/projects/{world['project'].id}/queue/next", json={"scope": "assigned"}).json()["frame_id"]
        is None
    )
    assert a.id  # la otra imagen nunca fue de ana


def test_queue_never_gives_the_same_frame_to_two_people(client, world, db):
    more_frames(db, world, 10)
    pid = world["project"].id
    ana, beto = as_user("ana"), as_user("beto")
    ga = ana.post(f"/api/projects/{pid}/queue/next", json={"scope": "any"}).json()["frame_id"]
    gb = beto.post(f"/api/projects/{pid}/queue/next", json={"scope": "any"}).json()["frame_id"]
    assert ga and gb and ga != gb
    # ana vuelve a pedir sin haber terminado: recibe la misma (su bloqueo se renueva), no otra nueva
    assert ana.post(f"/api/projects/{pid}/queue/next", json={"scope": "any"}).json()["frame_id"] == ga


def test_queue_is_race_safe_under_concurrency(client, world, db):
    more_frames(db, world, 30)
    pid = world["project"].id
    users = ["ana", "beto"]
    clients = {u: as_user(u) for u in users}
    got: dict[str, list[int]] = {u: [] for u in users}
    barrier = threading.Barrier(2)

    def worker(u):
        barrier.wait()
        for _ in range(12):
            r = clients[u].post(f"/api/projects/{pid}/queue/next", json={"scope": "any"}).json()
            if r["frame_id"]:
                got[u].append(r["frame_id"])
                # termina la imagen para pedir la siguiente
                clients[u].put(f"/api/frames/{r['frame_id']}/annotation", json={"reviewed": True, "boxes": []})

    ts = [threading.Thread(target=worker, args=(u,)) for u in users]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(got["ana"]) == len(set(got["ana"])) and len(got["beto"]) == len(set(got["beto"]))
    assert not set(got["ana"]) & set(got["beto"])  # nadie recibió una imagen que ya tenía la otra persona
    assert len(got["ana"]) + len(got["beto"]) == 24


def test_lock_endpoint_expiry_and_roles(client, world, db):
    f = world["frames"][0]
    ana, beto, lector = as_user("ana"), as_user("beto"), as_user("lector")
    assert ana.post(f"/api/frames/{f.id}/lock").json() == {"ok": True, "holder": None}
    assert beto.post(f"/api/frames/{f.id}/lock").json() == {"ok": False, "holder": "ana"}
    assert beto.get(f"/api/frames/{f.id}").json()["locked_by"] == "ana"
    assert ana.get(f"/api/frames/{f.id}").json()["locked_by"] is None
    assert lector.post(f"/api/frames/{f.id}/lock").status_code == 403
    lock = db.get(FrameLock, f.id)
    lock.until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)  # caducado
    db.commit()
    assert beto.post(f"/api/frames/{f.id}/lock").json()["ok"] is True
    assert as_user("intruso").post(f"/api/frames/{f.id}/lock").status_code == 404


# ---------- asignaciones ----------
def test_assignments_round_robin_overlap_and_permissions(client, world, db):
    more_frames(db, world, 8)  # 10 imágenes en total
    pid = world["project"].id
    admin = as_user("adm")
    r = admin.post(
        f"/api/projects/{pid}/assignments", json={"users": ["ana", "beto"], "overlap_pct": 20, "seed": 7}
    ).json()
    assert r["frames"] == 10 and r["overlap"] == 2 and r["created"] == 12
    s = {x["user"]: x for x in admin.get(f"/api/projects/{pid}/assignments").json()}
    assert s["ana"]["assigned"] + s["beto"]["assigned"] == 12 and abs(s["ana"]["assigned"] - s["beto"]["assigned"]) <= 2
    # repetirlo no duplica ni vuelve a asignar lo ya asignado
    assert admin.post(f"/api/projects/{pid}/assignments", json={"users": ["ana", "beto"]}).json()["frames"] == 0
    assert as_user("ana").post(f"/api/projects/{pid}/assignments", json={"users": ["ana"]}).status_code == 403
    assert admin.post(f"/api/projects/{pid}/assignments", json={"users": ["intruso"]}).status_code == 422


def test_assignment_seed_is_deterministic(client, world, db):
    more_frames(db, world, 8)
    pid = world["project"].id
    admin = as_user("adm")
    admin.post(f"/api/projects/{pid}/assignments", json={"users": ["ana", "beto"], "seed": 3})
    from app.models import Assignment

    first = {(a.frame_id, a.user_id) for a in db.scalars(select(Assignment))}
    db.query(Assignment).delete()
    db.commit()
    admin.post(f"/api/projects/{pid}/assignments", json={"users": ["ana", "beto"], "seed": 3})
    assert {(a.frame_id, a.user_id) for a in db.scalars(select(Assignment))} == first


def test_assignment_summary_counts_done(client, world, db):
    pid = world["project"].id
    admin, ana = as_user("adm"), as_user("ana")
    admin.post(f"/api/projects/{pid}/assignments", json={"users": ["ana"]})
    save(ana, world["frames"][0].id, [box()])
    s = admin.get(f"/api/projects/{pid}/assignments").json()
    assert s == [{"user": "ana", "assigned": 2, "done": 1}]
    assert ana.get(f"/api/projects/{pid}/assignments").status_code == 403


# ---------- estados, revisión y filtros ----------
def status_of(c, fid):
    return c.get(f"/api/frames/{fid}").json()["status"]


def test_status_transitions_single_agreed_disagreement_final(client, world):
    f = world["frames"][0]
    ana, beto, rev = as_user("ana"), as_user("beto"), as_user("rev")
    assert status_of(ana, f.id) == "pending"
    save(ana, f.id, [box()], reviewed=False)  # sin marcar revisado no cuenta
    assert status_of(ana, f.id) == "pending"
    save(ana, f.id, [box()])
    assert status_of(ana, f.id) == "single"
    save(beto, f.id, [box((11, 11, 20, 20))])  # mismo piojo (IoU alto)
    assert status_of(ana, f.id) == "agreed"
    save(beto, f.id, [box((11, 11, 20, 20), "fly")])  # otra clase
    d = ana.get(f"/api/frames/{f.id}").json()
    assert d["status"] == "disagreement" and d["n_reviewed"] == 2
    # el revisor ve las dos versiones y decide
    anns = rev.get(f"/api/frames/{f.id}/annotations").json()
    assert {a["user"] for a in anns} == {"ana", "beto"}
    beto_id = next(a["user_id"] for a in anns if a["user"] == "beto")
    r = rev.post(f"/api/frames/{f.id}/review", json={"final_user_id": beto_id, "note": "era una mosca"})
    assert r.status_code == 200
    d = ana.get(f"/api/frames/{f.id}").json()
    assert d["status"] == "final" and d["review"] == {
        "reviewer": "rev",
        "final_user": "beto",
        "final_user_id": beto_id,
        "note": "era una mosca",
    }
    rev.delete(f"/api/frames/{f.id}/review")
    assert status_of(ana, f.id) == "disagreement"


def test_negative_label_difference_is_not_disagreement(client, world):
    f = world["frames"][0]
    ana, beto = as_user("ana"), as_user("beto")
    save(ana, f.id, [box(), box((50, 30, 15, 15), "not_louse")])
    save(beto, f.id, [box()])
    assert status_of(ana, f.id) == "agreed"


def test_review_permissions_and_validation(client, world):
    f = world["frames"][0]
    ana, rev, lector = as_user("ana"), as_user("rev"), as_user("lector")
    save(ana, f.id, [box()])
    uid = ana.get("/api/me").json()["id"]
    assert ana.get(f"/api/frames/{f.id}/annotations").status_code == 403  # un etiquetador no ve el trabajo de los demás
    assert ana.post(f"/api/frames/{f.id}/review", json={"final_user_id": uid}).status_code == 403
    assert lector.post(f"/api/frames/{f.id}/review", json={"final_user_id": uid}).status_code == 403
    assert rev.post(f"/api/frames/{f.id}/review", json={"final_user_id": 9999}).status_code == 422
    assert as_user("intruso").get(f"/api/frames/{f.id}/annotations").status_code == 404
    assert rev.post(f"/api/frames/{f.id}/review", json={"final_user_id": uid}).status_code == 200


def test_frame_list_filters(client, world, db):
    a, b = world["frames"]
    more_frames(db, world, 2)
    pid = world["project"].id
    ana, beto, rev = as_user("ana"), as_user("beto"), as_user("rev")
    save(ana, a.id, [box()])
    save(beto, a.id, [box()])  # acuerdo
    save(ana, b.id, [box()])
    save(beto, b.id, [box((70, 60, 10, 10), "fly")])  # desacuerdo

    def ids(c, flt):
        return [x["id"] for x in c.get(f"/api/projects/{pid}/frames", params={"filter": flt}).json()]

    assert ids(ana, "agreed") == [a.id] and ids(ana, "disagreement") == [b.id]
    assert set(ids(ana, "needs_review")) == {a.id, b.id}
    assert len(ids(ana, "pending")) == 2 and len(ids(ana, "mine_pending")) == 2
    assert ids(ana, "final") == []
    uid = ana.get("/api/me").json()["id"]
    rev.post(f"/api/frames/{b.id}/review", json={"final_user_id": uid})
    assert ids(ana, "final") == [b.id] and ids(ana, "disagreement") == []
    assert [x["status"] for x in ana.get(f"/api/projects/{pid}/frames").json()][:2] == ["agreed", "final"]
    admin = as_user("adm")
    admin.post(f"/api/projects/{pid}/assignments", json={"users": ["ana"], "frame_ids": [world["frames"][0].id]})
    assert ids(ana, "assigned_pending") == []  # ya la revisó
    assert ana.get(f"/api/projects/{pid}/frames", params={"filter": "inventado"}).status_code == 422


# ---------- calidad ----------
def test_quality_agreement_and_counts(client, world):
    a, b = world["frames"]
    ana, beto, rev = as_user("ana"), as_user("beto"), as_user("rev")
    save(ana, a.id, [box()])
    save(beto, a.id, [box()])  # acuerdo perfecto
    save(ana, b.id, [box(), box((60, 50, 10, 10), "fly")])
    save(beto, b.id, [box()])  # 1 TP, 1 FN -> F1 = 2/3
    q = rev.get(f"/api/projects/{world['project'].id}/quality").json()
    assert q["status"] == {"pending": 0, "single": 0, "agreed": 1, "disagreement": 1, "final": 0}
    assert q["agreement"]["frames_with_overlap"] == 2 and q["agreement"]["agree_rate"] == 0.5
    assert abs(q["agreement"]["mean_f1"] - (1 + 2 / 3) / 2) < 1e-3
    users = {u["user"]: u for u in q["users"]}
    assert users["ana"]["reviewed"] == 2 and users["ana"]["boxes"] == {"louse": 2, "fly": 1}
    assert as_user("ana").get(f"/api/projects/{world['project'].id}/quality").status_code == 403


# ---------- datasets ----------
def make_labels(world):
    """Imagen 0: acuerdo de dos personas (agreed). Imagen 1: solo ana (single). Más: nada (no entran)."""
    a, b = world["frames"]
    ana, beto = as_user("ana"), as_user("beto")
    save(ana, a.id, [box(), box((50, 30, 15, 15), "fly"), box((70, 60, 5, 5), "not_louse")])
    save(beto, a.id, [box(), box((50, 30, 15, 15), "fly")])
    save(ana, b.id, [box((5, 5, 10, 10))])
    return a, b


def test_dataset_version_contents_and_immutability(client, world, db):
    a, b = make_labels(world)
    admin = as_user("adm")
    pid = world["project"].id
    r = admin.post(
        f"/api/projects/{pid}/datasets", json={"name": "v1", "include": ["agreed", "single", "final"], "seed": "x"}
    )
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["n_items"] == 2 and sum(s["frames"] for s in st["splits"].values()) == 2
    assert (
        sum(s["boxes"]["louse"] for s in st["splits"].values()) == 2
        and sum(s["boxes"]["fly"] for s in st["splits"].values()) == 1
    )
    # solo los acordados
    r2 = admin.post(f"/api/projects/{pid}/datasets", json={"name": "solo-acordadas", "include": ["agreed"]}).json()
    assert r2["n_items"] == 1
    # inmutable: cambiar las etiquetas después NO cambia la versión
    save(as_user("ana"), b.id, [box((5, 5, 10, 10)), box((30, 30, 10, 10))])
    detail = admin.get(f"/api/datasets/{st['id']}").json()
    assert sum(s["boxes"]["louse"] for s in detail["splits"].values()) == 2
    items = db.scalars(select(DatasetItem).where(DatasetItem.version_id == st["id"])).all()
    assert {i.source for i in items} == {"agreed", "single"}
    assert all(b["label"] is not None for i in items for b in i.boxes)


def test_dataset_validation_and_permissions(client, world):
    make_labels(world)
    admin, ana, rev = as_user("adm"), as_user("ana"), as_user("rev")
    pid = world["project"].id
    ok = {"name": "v1"}
    assert ana.post(f"/api/projects/{pid}/datasets", json=ok).status_code == 403
    assert rev.post(f"/api/projects/{pid}/datasets", json=ok).status_code == 403
    assert admin.post(f"/api/projects/{pid}/datasets", json=ok).status_code == 200
    assert admin.post(f"/api/projects/{pid}/datasets", json=ok).status_code == 409  # nombre repetido
    assert admin.post(f"/api/projects/{pid}/datasets", json={"name": "x", "include": ["pending"]}).status_code == 422
    assert admin.post(f"/api/projects/{pid}/datasets", json={"name": "x", "include": []}).status_code == 422
    assert admin.post(f"/api/projects/{pid}/datasets", json={"name": "x", "split": [0.5, 0.5, 0.5]}).status_code == 422
    assert admin.post(f"/api/projects/{pid}/datasets", json={"name": "../etc"}).status_code == 422
    assert [v["name"] for v in rev.get(f"/api/projects/{pid}/datasets").json()] == ["v1"]
    assert as_user("intruso").get(f"/api/projects/{pid}/datasets").status_code == 404
    assert as_user("intruso").get("/api/datasets/1").status_code == 404


def test_split_is_by_video_and_stable_between_versions(client, world, db):
    make_labels(world)
    v2 = Video(project_id=world["project"].id, name="otro_video", path=None, meta={})
    db.add(v2)
    db.flush()
    extra = []
    for i in range(30):
        vv = Video(project_id=world["project"].id, name=f"vid{i}", path=None, meta={})
        db.add(vv)
        db.flush()
        f = Frame(
            project_id=world["project"].id,
            video_id=vv.id,
            frame_index=1,
            t=0.0,
            image_path="f0.jpg",
            width=100,
            height=80,
            position=10 + i,
            meta={},
        )
        db.add(f)
        extra.append(f)
    db.commit()
    ana = as_user("ana")
    for f in extra:
        save(ana, f.id, [box()])
    admin = as_user("adm")
    pid = world["project"].id
    admin.post(f"/api/projects/{pid}/datasets", json={"name": "a", "seed": "s1"})
    admin.post(f"/api/projects/{pid}/datasets", json={"name": "b", "seed": "s1"})
    va = {
        i.frame_id: i.split
        for i in db.scalars(select(DatasetItem).join(DatasetItem.version).where(DatasetItem.version.has(name="a")))
    }
    vb = {
        i.frame_id: i.split
        for i in db.scalars(select(DatasetItem).join(DatasetItem.version).where(DatasetItem.version.has(name="b")))
    }
    assert va == vb and set(va.values()) == {"train", "val", "test"}
    names = {f.id: db.get(Video, f.video_id).name for f in db.scalars(select(Frame))}
    per_video: dict[str, set[str]] = {}
    for fid, sp in va.items():
        per_video.setdefault(names[fid], set()).add(sp)
    assert all(len(s) == 1 for s in per_video.values())  # un video nunca se reparte en dos particiones


def test_export_yolo_and_coco(client, world, db, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "export_root", tmp_path / "exports")
    a, b = make_labels(world)
    admin, rev, ana = as_user("adm"), as_user("rev"), as_user("ana")
    pid = world["project"].id
    vid = admin.post(f"/api/projects/{pid}/datasets", json={"name": "v1", "split": [1, 0, 0]}).json()["id"]
    assert ana.post(f"/api/datasets/{vid}/export", json={"format": "yolo"}).status_code == 403
    r = rev.post(f"/api/datasets/{vid}/export", json={"format": "both"})
    assert r.status_code == 200, r.text
    out = tmp_path / "exports" / "demo" / "v1"
    assert (out / "manifest.json").is_file() and r.json()["path"] == str(out.resolve())
    names = sorted(p.name for p in (out / "images" / "train").iterdir())
    assert names == ["v1__f000000.jpg", "v1__f000001.jpg"] and (out / "images" / "train" / names[0]).is_symlink()
    yolo = {n: (out / "labels" / "train" / n.replace(".jpg", ".txt")).read_text().split("\n")[:-1] for n in names}
    # clases en el orden del proyecto: louse=0, fly=1; la clase negativa (not_louse) no se exporta
    assert sorted(ln.split()[0] for ln in yolo["v1__f000000.jpg"]) == ["0", "1"]
    assert len(yolo["v1__f000001.jpg"]) == 1
    line = next(ln for ln in yolo["v1__f000000.jpg"] if ln.startswith("0 "))
    cx, cy, w, h = map(float, line.split()[1:])
    assert (round(cx * 100), round(cy * 80), round(w * 100), round(h * 80)) == (
        20,
        20,
        20,
        20,
    )  # bbox (10,10,20,20) en 100x80
    yaml = (out / "data.yaml").read_text()
    assert "0: louse" in yaml and "1: fly" in yaml and "not_louse" not in yaml and "train: images/train" in yaml
    import json

    coco = json.loads((out / "annotations_train.json").read_text())
    assert {c["name"] for c in coco["categories"]} == {"louse", "fly"} and {
        x["category_id"] for x in coco["annotations"]
    } == {1, 2}
    assert len(coco["images"]) == 2 and coco["annotations"][0]["bbox"] == [10, 10, 20, 20]
    # volver a exportar no falla (los enlaces ya existen)
    assert rev.post(f"/api/datasets/{vid}/export", json={"format": "yolo"}).status_code == 200
    assert rev.post(f"/api/datasets/{vid}/export", json={"format": "pdf"}).status_code == 422


def test_dataset_warnings_for_empty_partitions(client, world):
    make_labels(world)
    admin = as_user("adm")
    st = admin.post(f"/api/projects/{world['project'].id}/datasets", json={"name": "v1", "split": [1, 0, 0]}).json()
    assert any("val está vacía" in w for w in st["warnings"]) and any("test está vacía" in w for w in st["warnings"])
