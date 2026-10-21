from conftest import login


def box(bbox=(10, 10, 20, 20), label="louse", **kw):
    return {"bbox": list(bbox), "label": label, **kw}


def test_requires_login(client, world):
    assert client.get("/api/projects").status_code == 401
    assert client.get(f"/api/frames/{world['frames'][0].id}").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_login_ok_and_wrong_password(client, world):
    assert login(client, "ana", "mala").status_code == 401
    assert login(client, "nadie", "x").status_code == 401
    assert login(client, "ana").status_code == 200
    assert client.get("/api/me").json()["username"] == "ana"
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_user_without_password_cannot_login(client, world, db):
    from app.legacy_import import ensure_user

    ensure_user(db, "legacy")
    db.commit()
    assert login(client, "legacy", "").status_code == 401


def test_projects_scoped_to_membership(client, world):
    login(client, "ana")
    ps = client.get("/api/projects").json()
    assert [p["slug"] for p in ps] == ["demo"] and ps[0]["role"] == "labeler" and ps[0]["n_frames"] == 2
    client.post("/api/auth/logout")
    login(client, "intruso")
    assert client.get("/api/projects").json() == []
    client.post("/api/auth/logout")
    login(client, "root")
    assert {p["slug"] for p in client.get("/api/projects").json()} == {"demo", "otro"}


def test_outsider_gets_404_not_403(client, world):
    f = world["frames"][0]
    login(client, "intruso")
    assert client.get(f"/api/frames/{f.id}").status_code == 404
    assert client.get(f"/api/frames/{f.id}/image").status_code == 404
    assert client.get(f"/api/projects/{world['project'].id}/frames").status_code == 404
    assert client.put(f"/api/frames/{f.id}/annotation", json={"boxes": []}).status_code == 404


def test_viewer_reads_but_cannot_write(client, world):
    f = world["frames"][0]
    login(client, "lector")
    assert client.get(f"/api/frames/{f.id}").status_code == 200
    assert client.put(f"/api/frames/{f.id}/annotation", json={"boxes": []}).status_code == 403


def test_frame_detail_and_list(client, world):
    login(client, "ana")
    f = world["frames"][0]
    d = client.get(f"/api/frames/{f.id}").json()
    assert d["video"] == "v1" and len(d["proposals"]) == 2 and d["annotation"] is None and d["n_annotators"] == 0
    items = client.get(f"/api/projects/{world['project'].id}/frames").json()
    assert [i["position"] for i in items] == [0, 1] and not items[0]["reviewed"]


def test_save_roundtrip_and_isolation_between_users(client, world):
    f = world["frames"][0]
    login(client, "ana")
    prop = client.get(f"/api/frames/{f.id}").json()["proposals"][0]["id"]
    body = {
        "reviewed": True,
        "boxes": [
            box(proposal_id=prop, sources=["blob"], score=7.0),
            box((50, 30, 15, 15), "not_louse"),
            box((5, 5, 9, 9), "louse", manual=True),
            box((60, 60, 8, 8), "fly", deleted=True),
        ],
    }
    r = client.put(f"/api/frames/{f.id}/annotation", json=body)
    assert r.status_code == 200 and r.json()["reviewed"] and len(r.json()["boxes"]) == 4
    d = client.get(f"/api/frames/{f.id}").json()
    assert d["annotation"]["boxes"][0]["proposal_id"] == prop and d["annotation"]["boxes"][3]["deleted"] is True
    item = client.get(f"/api/projects/{world['project'].id}/frames").json()[0]
    assert item["reviewed"] and item["n_labeled"] == {"louse": 2, "not_louse": 1}  # el borrado no cuenta
    # otra persona ve el frame sin el trabajo de ana (cada quien tiene su anotación)
    client.post("/api/auth/logout")
    login(client, "rev")
    d2 = client.get(f"/api/frames/{f.id}").json()
    assert d2["annotation"] is None and d2["n_annotators"] == 1
    assert client.get(f"/api/projects/{world['project'].id}/frames").json()[0]["reviewed"] is False


def test_save_replaces_previous_set_and_is_audited(client, world, db):
    from sqlalchemy import func, select

    from app.models import AnnotationBox, AuditLog

    f = world["frames"][0]
    login(client, "ana")
    client.put(f"/api/frames/{f.id}/annotation", json={"boxes": [box(), box((1, 1, 5, 5))]})
    client.put(f"/api/frames/{f.id}/annotation", json={"reviewed": True, "boxes": [box()]})
    assert db.scalar(select(func.count()).select_from(AnnotationBox)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "annotation.save")) == 2


def test_save_validation(client, world):
    f = world["frames"][0]
    login(client, "ana")
    assert client.put(f"/api/frames/{f.id}/annotation", json={"boxes": [box(label="dragon")]}).status_code == 422
    assert client.put(f"/api/frames/{f.id}/annotation", json={"boxes": [box((1, 1, 0, 5))]}).status_code == 422
    assert client.put(f"/api/frames/{f.id}/annotation", json={"boxes": [{"bbox": [1, 2, 3]}]}).status_code == 422


def test_foreign_proposal_id_is_not_linked(client, world):
    a, b = world["frames"]
    login(client, "ana")
    foreign = client.get(f"/api/frames/{b.id}").json()["proposals"][0]["id"]
    r = client.put(f"/api/frames/{a.id}/annotation", json={"boxes": [box(proposal_id=foreign)]})
    assert r.json()["boxes"][0]["proposal_id"] is None


def test_only_pending_filter(client, world):
    login(client, "ana")
    a, _ = world["frames"]
    client.put(f"/api/frames/{a.id}/annotation", json={"reviewed": True, "boxes": []})
    items = client.get(f"/api/projects/{world['project'].id}/frames", params={"only_pending": True}).json()
    assert [i["position"] for i in items] == [1]


def test_image_served_and_path_traversal_blocked(client, world, db, media):
    f = world["frames"][0]
    login(client, "lector")
    r = client.get(f"/api/frames/{f.id}/image")
    assert r.status_code == 200 and r.content.startswith(b"\xff\xd8")
    f.image_path = "../../etc/passwd"
    db.add(f)
    db.commit()
    assert client.get(f"/api/frames/{f.id}/image").status_code == 404
    f.image_path = "no_existe.jpg"
    db.commit()
    assert client.get(f"/api/frames/{f.id}/image").status_code == 404


def test_project_stats_summary(client, world):
    login(client, "ana")
    a, _ = world["frames"]
    client.put(
        f"/api/frames/{a.id}/annotation",
        json={
            "reviewed": True,
            "boxes": [
                box(),
                box((1, 1, 5, 5), "fly", manual=True),
                box((9, 9, 6, 6), None),
                box((60, 60, 8, 8), "louse", deleted=True),
            ],
        },
    )
    s = client.get(f"/api/projects/{world['project'].id}/stats").json()
    assert s == {
        "frames": 2,
        "reviewed": 1,
        "assigned": 0,
        "assigned_done": 0,
        "by_label": {"louse": 1, "fly": 1},
        "manual": 1,
        "unlabeled": 1,
        "deleted": 1,
    }
    client.post("/api/auth/logout")
    login(client, "rev")  # las estadísticas son de cada usuario
    assert client.get(f"/api/projects/{world['project'].id}/stats").json()["reviewed"] == 0
    client.post("/api/auth/logout")
    login(client, "intruso")
    assert client.get(f"/api/projects/{world['project'].id}/stats").status_code == 404


def test_members_list_permissions(client, world):
    pid = world["project"].id
    login(client, "rev")
    m = client.get(f"/api/projects/{pid}/members").json()
    assert {x["username"]: x["role"] for x in m} == {
        "ana": "labeler",
        "beto": "labeler",
        "rev": "reviewer",
        "lector": "viewer",
        "adm": "admin",
    }
    client.post("/api/auth/logout")
    login(client, "ana")
    assert client.get(f"/api/projects/{pid}/members").status_code == 403
    client.post("/api/auth/logout")
    login(client, "intruso")
    assert client.get(f"/api/projects/{pid}/members").status_code == 404


def test_project_videos_list(client, world, db, vfr_video):
    from app.models import Video

    db.query(Video).filter(Video.name == "v1").one().path = str(vfr_video)
    db.commit()
    login(client, "lector")
    assert client.get(f"/api/projects/{world['project'].id}/videos").json() == [
        {
            "id": db.query(Video).one().id,
            "name": "v1",
            "has_file": True,
            "n_frames": 2,
            "first_frame_id": min(f.id for f in world["frames"]),
            "duration": None,
        }
    ]
    client.post("/api/auth/logout")
    login(client, "intruso")
    assert client.get(f"/api/projects/{world['project'].id}/videos").status_code == 404
