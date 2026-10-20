import cv2
import numpy as np
from test_assisted import train_model
from test_training import trainable_world
from test_workflow import as_user


def png(w=64, h=48):
    ok, buf = cv2.imencode(".png", np.full((h, w, 3), 120, np.uint8))
    assert ok
    return buf.tobytes()


def setup(db, world):
    admin, pid, vid = trainable_world(db, world)
    return admin, pid, train_model(admin, pid, vid)


def test_detect_one_and_range_of_project_images(client, world, db, fake_workers):
    admin, pid, mid = setup(db, world)
    one = admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, "first": 1})
    assert one.status_code == 200, one.text
    r = one.json()
    assert len(r["items"]) == 1 and r["items"][0]["name"] == "Imagen 1" and r["classes"] == ["louse", "fly"]
    assert [d["score"] for d in r["items"][0]["dets"]] == [0.9, 0.2]  # el simulado da dos cajas, ordenadas por puntaje
    assert r["items"][0]["image_url"].startswith("/api/frames/")
    rng = admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, "first": 1, "last": 2}).json()
    assert [i["name"] for i in rng["items"]] == ["Imagen 1", "Imagen 2"]
    assert admin.get(f"/api/detect/{rng['id']}").json()["items"][0]["name"] == "Imagen 1"  # se puede volver a abrir


def test_detect_validation_and_permissions(client, world, db, fake_workers):
    admin, pid, mid = setup(db, world)
    bad = lambda **kw: admin.post(f"/api/projects/{pid}/detect", json={"model_id": mid, **kw})  # noqa: E731
    assert bad(first=5, last=2).status_code == 422
    assert bad(first=1, last=500).status_code == 422  # demasiadas
    assert bad(first=9999).status_code == 404  # no existe esa imagen
    assert admin.post(f"/api/projects/{pid}/detect", json={"model_id": 999, "first": 1}).status_code == 404
    for who, code in (("ana", 403), ("lector", 403), ("intruso", 404)):
        assert as_user(who).post(f"/api/projects/{pid}/detect", json={"model_id": mid, "first": 1}).status_code == code


def test_detect_uploaded_images_are_served_back_only_to_members(client, world, db, fake_workers):
    admin, pid, mid = setup(db, world)
    r = admin.post(
        f"/api/projects/{pid}/detect/upload",
        data={"model_id": mid},
        files=[("files", ("uno.png", png(), "image/png")), ("files", ("dos.png", png(80, 60), "image/png"))],
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert [i["name"] for i in got["items"]] == ["uno.png", "dos.png"] and got["items"][1]["width"] == 80
    img = admin.get(got["items"][0]["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert as_user("ana").get(got["items"][0]["image_url"]).status_code == 200  # miembro del proyecto
    assert as_user("intruso").get(got["items"][0]["image_url"]).status_code == 404
    assert as_user("intruso").get(f"/api/detect/{got['id']}").status_code == 404
    bad = admin.post(
        f"/api/projects/{pid}/detect/upload",
        data={"model_id": mid},
        files=[("files", ("x.txt", b"hola", "text/plain"))],
    )
    assert bad.status_code == 422 and "no es una imagen" in bad.json()["detail"]
    assert admin.get("/api/detect/abc123").status_code == 404


def test_live_control_accepts_view_options(client, world):
    from app.routers.live import ControlIn

    assert ControlIn(show_boxes=False, show_classes=[0]).model_dump(exclude_unset=True) == {
        "show_boxes": False,
        "show_classes": [0],
    }
    assert ControlIn(show_classes=None).model_dump(exclude_unset=True) == {"show_classes": None}  # null = todas
