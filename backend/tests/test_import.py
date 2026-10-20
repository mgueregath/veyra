import json

from sqlalchemy import func, select

from app.legacy_import import import_legacy
from app.models import Annotation, AnnotationBox, Frame, Proposal


def write_legacy(tmp_path):
    frames = [
        {
            "video": "A",
            "frame": 1,
            "t": 0.5,
            "image": "A/f1.jpg",
            "w": 100,
            "h": 80,
            "stratum": "enriched",
            "t_src": "pts",
            "boxes": [
                {"sources": ["blob"], "score": 9.0, "bbox": [10, 10, 20, 20]},
                {"sources": ["blob"], "score": 3.0, "bbox": [10, 10, 20, 20]},
            ],
        },
        {"video": "A", "frame": 2, "t": 1.0, "image": "A/f2.jpg", "w": 100, "h": 80, "boxes": []},
    ]
    labels = {
        "A:1": {
            "reviewed": True,
            "boxes": [
                {
                    "bbox": [12, 14, 20, 20],
                    "bbox0": [10, 10, 20, 20],
                    "sources": ["blob"],
                    "score": 9.0,
                    "label": "louse",
                    "manual": False,
                },
                {"bbox": [10, 10, 20, 20], "sources": ["blob"], "score": 3.0, "label": "not_louse", "manual": False},
                {"bbox": [1, 1, 5, 5], "sources": [], "score": None, "label": "fly", "manual": True},
            ],
            "deleted": [{"bbox": [60, 60, 8, 8], "sources": ["motion"], "score": 2.0, "label": None, "manual": False}],
        }
    }
    (tmp_path / "frames.json").write_text(json.dumps(frames))
    (tmp_path / "labels.json").write_text(json.dumps(labels))


def test_import_maps_everything_and_is_idempotent(db, tmp_path):
    write_legacy(tmp_path)
    s1 = import_legacy(db, tmp_path)
    assert s1 == {"frames": 2, "proposals": 2, "annotations": 1, "boxes": 3, "deleted": 1, "reviewed": 1}
    s2 = import_legacy(db, tmp_path)
    assert s2["proposals"] == 0
    assert db.scalar(select(func.count()).select_from(Frame)) == 2
    assert db.scalar(select(func.count()).select_from(Proposal)) == 2
    assert db.scalar(select(func.count()).select_from(Annotation)) == 1
    assert db.scalar(select(func.count()).select_from(AnnotationBox)) == 4
    boxes = db.scalars(select(AnnotationBox).order_by(AnnotationBox.id)).all()
    p = db.scalars(select(Proposal).order_by(Proposal.id)).all()
    # la caja movida y la idéntica se emparejan con SU propuesta (aunque ambas tengan el mismo bbox original)
    assert boxes[0].proposal_id == p[0].id and boxes[0].orig_bbox == [10, 10, 20, 20]
    assert boxes[1].proposal_id == p[1].id
    assert boxes[2].manual and boxes[2].proposal_id is None
    assert boxes[3].deleted and boxes[3].label is None
    f = db.scalar(select(Frame).where(Frame.frame_index == 1))
    assert f.stratum == "enriched" and f.meta == {"t_src": "pts"} and f.t == 0.5
