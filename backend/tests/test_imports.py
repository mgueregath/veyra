import io
import json
import zipfile

import cv2
import numpy as np
import pytest
from test_workflow import as_user

from app.imports import class_resolver, parse_coco, parse_names, parse_yolo, safe_name
from app.models import Annotation, AnnotationBox, Frame, Video


def jpg(w=200, h=100, v=90):
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), v, np.uint8))
    return buf.tobytes()


CLASSES = [{"id": "louse", "name": "Piojo"}, {"id": "fly", "name": "Mosca"}]


def test_pure_parsers():
    assert safe_name("../../etc/pa ss?.jpg") == "pa ss_.jpg"
    assert parse_names({"classes.txt": b"Piojo\nMosca\n"}) == ["Piojo", "Mosca"]
    assert parse_names({"data.yaml": b"names:\n  0: fly\n  1: louse\n"}) == ["fly", "louse"]
    res, warn = class_resolver(CLASSES, ["fly", "louse", "raro"])
    assert [res(0), res(1), res(2)] == ["fly", "louse", None] and len(warn) == 1
    res, _ = class_resolver(CLASSES, None)
    assert res(1) == "fly" and res(5) is None
    boxes = parse_yolo("0 0.5 0.5 0.2 0.4\nbasura\n1 0.99 0.5 0.5 0.5\n", 200, 100, res)
    assert boxes[0] == {"bbox": [80, 30, 40, 40], "label": "louse"} and boxes[1]["bbox"][0] == 148  # recortada al borde
    coco = json.dumps(
        {
            "images": [{"id": 1, "file_name": "a/x.jpg", "width": 200, "height": 100}],
            "categories": [{"id": 7, "name": "Piojo"}],
            "annotations": [{"image_id": 1, "category_id": 7, "bbox": [10, 10, 20, 20]}],
        }
    ).encode()
    b, _ = parse_coco(coco, lambda n: "louse" if n == "Piojo" else None)
    assert b == {"x.jpg": [{"bbox": [10, 10, 20, 20], "label": "louse"}]}


def post(c, pid, files, **data):
    return c.post(f"/api/projects/{pid}/import/images", files=files, data=data)


def test_import_images_with_and_without_labels(client, world, db):
    adm, pid = as_user("adm"), world["project"].id
    n0 = db.query(Frame).filter(Frame.project_id == pid).count()
    files = [
        ("files", ("a.jpg", jpg(), "image/jpeg")),
        ("files", ("b.png", cv2.imencode(".png", np.zeros((50, 80, 3), np.uint8))[1].tobytes(), "image/png")),
        ("files", ("a.txt", b"0 0.5 0.5 0.2 0.4\n1 0.1 0.1 0.1 0.1\n", "text/plain")),
        ("files", ("classes.txt", b"Piojo\nMosca\n", "text/plain")),
        ("files", ("mala.jpg", b"no soy imagen", "image/jpeg")),
    ]
    r = post(adm, pid, files, reviewed="true")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["images"] == 2 and d["with_labels"] == 1 and d["boxes"] == 2 and len(d["skipped"]) == 1
    assert db.query(Frame).filter(Frame.project_id == pid).count() == n0 + 2
    a = db.query(Annotation).join(Frame).filter(Frame.meta["original_name"].astext == "a.jpg").one()
    assert (
        a.reviewed
        and sorted(b.label for b in a.boxes) == ["fly", "louse"]
        and all(b.sources == ["import"] for b in a.boxes)
    )
    b_frame = db.query(Frame).filter(Frame.meta["original_name"].astext == "b.png").one()
    assert db.query(Annotation).filter_by(frame_id=b_frame.id).count() == 0  # sin etiquetas: queda por etiquetar
    assert adm.get(f"/api/frames/{b_frame.id}/image").status_code == 200
    assert db.query(Video).filter(Video.project_id == pid, Video.name.like("subidas-%")).count() == 1


def test_import_images_from_zip_with_coco(client, world, db):
    adm, pid = as_user("adm"), world["project"].id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fotos/x.jpg", jpg())
        z.writestr("anotaciones.json", json.dumps({
            "images": [{"id": 1, "file_name": "x.jpg", "width": 200, "height": 100}],
            "categories": [{"id": 1, "name": "Mosca"}, {"id": 2, "name": "Otra"}],
            "annotations": [{"image_id": 1, "category_id": 1, "bbox": [5, 5, 30, 30]},
                            {"image_id": 1, "category_id": 2, "bbox": [50, 5, 30, 30]}],
        }))  # fmt: skip
    r = post(adm, pid, [("files", ("lote.zip", buf.getvalue(), "application/zip"))])
    assert r.status_code == 200, r.text
    assert r.json()["boxes"] == 2 and any("Otra" in w for w in r.json()["warnings"])
    labels = sorted(str(b.label) for b in db.query(AnnotationBox))
    assert labels.count("fly") == 1 and labels.count("None") == 1


def test_import_permissions_and_errors(client, world):
    pid = world["project"].id
    assert post(as_user("ana"), pid, [("files", ("a.jpg", jpg(), "image/jpeg"))]).status_code == 403
    assert post(as_user("intruso"), pid, [("files", ("a.jpg", jpg(), "image/jpeg"))]).status_code == 404
    assert post(as_user("adm"), pid, [("files", ("a.txt", b"x", "text/plain"))]).status_code == 422
    assert post(as_user("adm"), pid, [("files", ("z.zip", b"no", "application/zip"))]).status_code == 422
    r = as_user("adm").post(f"/api/projects/{pid}/import/video", files={"file": ("v.exe", b"x")})
    assert r.status_code == 422


def test_import_video_extracts_frames_and_is_playable(client, world, db, vfr_video):
    adm, pid = as_user("adm"), world["project"].id
    data = vfr_video.read_bytes()
    r = adm.post(f"/api/projects/{pid}/import/video", files={"file": ("mi video.mp4", data)}, data={"samples": "4"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "mi video" and 2 <= d["frames"] <= 5
    v = db.get(Video, d["video_id"])
    assert v.path and v.meta["fps"] and v.meta["pts"]
    fr = db.query(Frame).filter(Frame.video_id == v.id).order_by(Frame.frame_index).all()
    assert fr[0].frame_index == 0 and all(f.stratum == "carga" for f in fr)
    assert adm.get(f"/api/frames/{fr[-1].id}/image").status_code == 200
    assert adm.get(f"/api/videos/{v.id}/info").status_code == 200  # el reproductor lo puede abrir
    r2 = adm.post(f"/api/projects/{pid}/import/video", files={"file": ("mi video.mp4", data)}, data={"samples": "0"})
    assert r2.json()["name"] == "mi video-2" and r2.json()["frames"] == 1
    bad = adm.post(f"/api/projects/{pid}/import/video", files={"file": ("roto.mp4", b"no es video")})
    assert bad.status_code == 422 and not (db.query(Video).filter(Video.name == "roto").count())


@pytest.mark.parametrize("ext", ["mp4"])
def test_imported_video_can_be_used_for_live_sessions(client, world, db, vfr_video, ext):
    adm, pid = as_user("adm"), world["project"].id
    d = adm.post(f"/api/projects/{pid}/import/video", files={"file": ("v2.mp4", vfr_video.read_bytes())}).json()
    import os

    assert os.path.isabs(db.get(Video, d["video_id"]).path)  # el inicio en vivo exige ruta absoluta existente
