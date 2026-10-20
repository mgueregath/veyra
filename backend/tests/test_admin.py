import gzip
import json

from conftest import login
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_workflow import as_user, box, save

from app import backup
from app.main import app
from app.models import (
    Annotation,
    AnnotationBox,
    Assignment,
    DatasetItem,
    DatasetVersion,
    Frame,
    Membership,
    Proposal,
    Review,
    User,
)


def pid_of(world):
    return world["project"].id


def test_team_list_and_permissions(client, world):
    admin, pid = as_user("adm"), pid_of(world)
    t = {m["username"]: m["role"] for m in admin.get(f"/api/projects/{pid}/team").json()}
    assert t == {"ana": "labeler", "beto": "labeler", "rev": "reviewer", "lector": "viewer", "adm": "admin"}
    for who in ("ana", "rev", "lector"):
        assert as_user(who).get(f"/api/projects/{pid}/team").status_code == 403
    assert as_user("intruso").get(f"/api/projects/{pid}/team").status_code == 404


def test_add_member_creates_user_and_validates(client, world, db):
    admin, pid = as_user("adm"), pid_of(world)
    r = admin.post(
        f"/api/projects/{pid}/members",
        json={"username": "nuevo", "role": "labeler", "password": "clave-larga-1", "display_name": "Nueva Persona"},
    )
    assert r.status_code == 200, r.text
    c = TestClient(app)
    assert login(c, "nuevo", "clave-larga-1").status_code == 200
    assert c.get("/api/projects").json()[0]["role"] == "labeler"
    assert (
        admin.post(f"/api/projects/{pid}/members", json={"username": "nuevo", "role": "viewer"}).status_code == 409
    )  # ya pertenece
    assert (
        admin.post(f"/api/projects/{pid}/members", json={"username": "otro", "role": "labeler"}).status_code == 422
    )  # no existe y falta contraseña
    assert (
        admin.post(
            f"/api/projects/{pid}/members", json={"username": "otro", "role": "labeler", "password": "corta"}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            f"/api/projects/{pid}/members", json={"username": "otro", "role": "jefe", "password": "clave-larga-1"}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            f"/api/projects/{pid}/members", json={"username": "../x", "role": "labeler", "password": "clave-larga-1"}
        ).status_code
        == 422
    )
    # un usuario que ya existe en otro proyecto se añade sin contraseña
    assert admin.post(f"/api/projects/{pid}/members", json={"username": "intruso", "role": "viewer"}).status_code == 200
    assert (
        as_user("ana")
        .post(f"/api/projects/{pid}/members", json={"username": "xx", "role": "viewer", "password": "clave-larga-1"})
        .status_code
        == 403
    )
    assert db.scalar(select(User.password_hash).where(User.username == "nuevo")).startswith("scrypt$")  # nunca en claro


def test_role_change_and_last_admin_protection(client, world):
    admin, pid = as_user("adm"), pid_of(world)
    assert admin.put(f"/api/projects/{pid}/members/ana", json={"role": "reviewer"}).status_code == 200
    assert as_user("ana").get(f"/api/projects/{pid}/quality").status_code == 200  # el cambio rige de inmediato
    assert admin.put(f"/api/projects/{pid}/members/ana", json={"role": "supremo"}).status_code == 422
    assert admin.put(f"/api/projects/{pid}/members/fantasma", json={"role": "viewer"}).status_code == 404
    assert (
        admin.put(f"/api/projects/{pid}/members/adm", json={"role": "viewer"}).status_code == 409
    )  # es el único administrador
    assert admin.delete(f"/api/projects/{pid}/members/adm").status_code == 409
    assert admin.post(f"/api/projects/{pid}/members", json={"username": "beto", "role": "admin"}).status_code == 409
    assert admin.put(f"/api/projects/{pid}/members/beto", json={"role": "admin"}).status_code == 200
    assert admin.put(f"/api/projects/{pid}/members/adm", json={"role": "viewer"}).status_code == 200  # ya hay otro


def test_remove_member_keeps_their_work(client, world, db):
    admin, pid = as_user("adm"), pid_of(world)
    save(as_user("ana"), world["frames"][0].id, [box()])
    assert admin.delete(f"/api/projects/{pid}/members/ana").status_code == 200
    assert as_user("ana").get(f"/api/projects/{pid}/frames").status_code == 404  # ya no entra
    assert db.scalar(select(func.count()).select_from(Annotation)) == 1  # su trabajo se conserva
    assert admin.get(f"/api/frames/{world['frames'][0].id}/annotations").json()[0]["user"] == "ana"


def test_password_reset_and_self_change(client, world):
    admin, pid = as_user("adm"), pid_of(world)
    assert admin.post(f"/api/projects/{pid}/members/ana/password", json={"password": "otra-clave-2"}).status_code == 200
    c = TestClient(app)
    assert login(c, "ana", "pw-ana").status_code == 401 and login(c, "ana", "otra-clave-2").status_code == 200
    assert admin.post(f"/api/projects/{pid}/members/ana/password", json={"password": "corta"}).status_code == 422
    assert (
        admin.post(f"/api/projects/{pid}/members/intruso/password", json={"password": "otra-clave-2"}).status_code
        == 404
    )
    assert (
        as_user("rev").post(f"/api/projects/{pid}/members/ana/password", json={"password": "otra-clave-2"}).status_code
        == 403
    )
    assert c.post("/api/me/password", json={"old": "mala", "new": "nueva-clave-3"}).status_code == 403
    assert c.post("/api/me/password", json={"old": "otra-clave-2", "new": "nueva-clave-3"}).status_code == 200
    c2 = TestClient(app)
    assert login(c2, "ana", "otra-clave-2").status_code == 401 and login(c2, "ana", "nueva-clave-3").status_code == 200
    assert (
        as_user("root").post(f"/api/projects/{pid}/members/adm/password", json={"password": "clave-root-9"}).status_code
        == 200
    )


def test_superuser_password_cannot_be_reset_by_project_admin(client, world, db):
    from app.legacy_import import ensure_member

    ensure_member(db, world["root"], world["project"], "labeler")
    db.commit()
    assert (
        as_user("adm")
        .post(f"/api/projects/{pid_of(world)}/members/root/password", json={"password": "clave-nueva-1"})
        .status_code
        == 403
    )


def test_usage_and_quota_blocks_new_images(client, world, db, media, vfr_video):
    from app.models import Video

    admin, pid = as_user("adm"), pid_of(world)
    u = admin.get(f"/api/projects/{pid}/usage").json()
    assert u["images"]["files"] == 2 and u["images"]["bytes"] > 0 and u["quota_bytes"] is None and not u["over_quota"]
    db.query(Video).filter(Video.name == "v1").one().path = str(vfr_video)
    db.commit()
    assert admin.get(f"/api/projects/{pid}/usage").json()["videos"]["bytes"] > 0
    r = admin.put(
        f"/api/projects/{pid}/quota", json={"quota_gb": 0.000000001}
    )  # ~1 byte: las imágenes de la prueba ya la superan
    assert r.json()["over_quota"] is True
    pts = admin.get(f"/api/videos/{db.query(Video).one().id}/pts").json()["pts"]
    s = admin.post(f"/api/frames/{world['frames'][0].id}/snapshot", json={"t": pts[50]})
    assert s.status_code == 507 and "cuota" in s.json()["detail"]
    assert db.query(Frame).count() == 2  # no se guardó nada
    assert admin.put(f"/api/projects/{pid}/quota", json={"quota_gb": 5}).json()["over_quota"] is False
    assert admin.post(f"/api/frames/{world['frames'][0].id}/snapshot", json={"t": pts[50]}).status_code == 200
    assert admin.put(f"/api/projects/{pid}/quota", json={"quota_gb": None}).json()["quota_bytes"] is None
    assert admin.put(f"/api/projects/{pid}/quota", json={"quota_gb": -3}).status_code == 422
    assert as_user("rev").get(f"/api/projects/{pid}/usage").status_code == 403


def test_system_status(client, world):
    admin, pid = as_user("adm"), pid_of(world)
    save(as_user("ana"), world["frames"][0].id, [box()])
    s = admin.get(f"/api/projects/{pid}/system").json()
    assert s["jobs"] == {"queued": 0, "running": 0} and s["live_active"] == 0
    assert s["recent"][0]["action"] == "annotation.save" and s["recent"][0]["user"] == "ana"
    assert as_user("ana").get(f"/api/projects/{pid}/system").status_code == 403


def populated(world, db):
    """Un proyecto con de todo: anotaciones de dos personas, caja movida y borrada, revisión, asignaciones y un dataset."""
    a, b = world["frames"]
    ana, beto, rev, admin = as_user("ana"), as_user("beto"), as_user("rev"), as_user("adm")
    prop = admin.get(f"/api/frames/{a.id}").json()["proposals"][0]["id"]
    save(
        ana,
        a.id,
        [
            box(proposal_id=prop, orig_bbox=[9, 9, 20, 20]),
            box((70, 60, 5, 5), "fly", manual=True),
            box((1, 1, 4, 4), None, deleted=True),
        ],
    )
    save(beto, a.id, [box((70, 60, 5, 5), "fly")])
    save(ana, b.id, [box()])
    rev.post(
        f"/api/frames/{a.id}/review",
        json={"final_user_id": db.scalar(select(User.id).where(User.username == "ana")), "note": "ok"},
    )
    admin.post(f"/api/projects/{world['project'].id}/assignments", json={"users": ["ana", "beto"], "seed": 1})
    admin.post(f"/api/projects/{world['project'].id}/datasets", json={"name": "d1", "split": [1, 0, 0]})


def counts(db, pid):
    q = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w))  # noqa: E731
    fids = select(Frame.id).where(Frame.project_id == pid)
    return {
        "frames": q(Frame, Frame.project_id == pid), "proposals": q(Proposal, Proposal.frame_id.in_(fids)),
        "annotations": q(Annotation, Annotation.frame_id.in_(fids)),
        "boxes": q(AnnotationBox, AnnotationBox.annotation_id.in_(select(Annotation.id).where(Annotation.frame_id.in_(fids)))),
        "reviews": q(Review, Review.frame_id.in_(fids)), "assignments": q(Assignment, Assignment.frame_id.in_(fids)),
        "datasets": q(DatasetVersion, DatasetVersion.project_id == pid),
        "items": q(DatasetItem, DatasetItem.version_id.in_(select(DatasetVersion.id).where(DatasetVersion.project_id == pid))),
        "members": q(Membership, Membership.project_id == pid),
    }  # fmt: skip


def test_backup_roundtrip_restores_everything(client, world, db):
    populated(world, db)
    pid = pid_of(world)
    raw = as_user("adm").get(f"/api/projects/{pid}/backup")
    assert (
        raw.status_code == 200
        and raw.headers["content-type"] == "application/gzip"
        and "labelhub-demo-" in raw.headers["content-disposition"]
    )
    data = json.loads(gzip.decompress(raw.content))
    assert (
        data["format"] == 1
        and data["project"]["slug"] == "demo"
        and "password" not in raw.content.decode("latin1").lower()
    )  # sin credenciales
    new = backup.restore_project(db, backup.load_bytes(raw.content), slug="copia", name="Copia")
    before, after = counts(db, pid), counts(db, new.id)
    assert (
        before == after
        and before["frames"] == 2
        and before["boxes"] == 5
        and before["reviews"] == 1
        and before["items"] == 2
    )
    # las etiquetas restauradas dicen lo mismo y el vínculo caja ↔ propuesta se conserva
    ana_new = db.scalar(
        select(Annotation).join(Frame).where(Frame.project_id == new.id, Annotation.reviewed).order_by(Annotation.id)
    )
    linked = [b for b in ana_new.boxes if b.proposal_id is not None]
    assert len(linked) == 1 and linked[0].orig_bbox == [9, 9, 20, 20]
    assert db.get(Proposal, linked[0].proposal_id).frame_id == ana_new.frame_id
    assert sum(b.deleted for b in ana_new.boxes) == 1 and any(b.manual for b in ana_new.boxes)
    # el original no cambió
    assert counts(db, pid) == before


def test_restore_errors_and_missing_users(client, world, db):
    populated(world, db)
    data = backup.dump_project(db, world["project"])
    try:
        backup.restore_project(db, data)  # mismo slug
        raise AssertionError("debía fallar")
    except ValueError as e:
        assert "ya existe" in str(e)
    try:
        backup.restore_project(db, {**data, "format": 99}, slug="x")
        raise AssertionError("debía fallar")
    except ValueError as e:
        assert "formato" in str(e)
    # una persona que ya no existe en esta instalación (la base impide borrar usuarios con historial: se simula en el volcado)
    data = json.loads(json.dumps(data).replace('"beto"', '"fantasma"'))
    new = backup.restore_project(db, data, slug="sin-beto")
    c = counts(db, new.id)
    assert c["annotations"] == 2 and c["members"] == 4  # se omite su trabajo y su membresía, el resto se restaura


def test_backup_permissions(client, world):
    pid = pid_of(world)
    for who in ("ana", "rev", "lector"):
        assert as_user(who).get(f"/api/projects/{pid}/backup").status_code == 403
    assert as_user("intruso").get(f"/api/projects/{pid}/backup").status_code == 404
