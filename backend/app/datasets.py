"""Versiones de dataset (inmutables) y su exportación a YOLO / COCO."""

import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import consensus
from .config import settings
from .models import DatasetItem, DatasetVersion, Frame, Project, Video
from .workflow import FrameState, class_sets, compute_states, discarded, final_annotation, labeled

SPLITS = ("train", "val", "test")


def _frame_rows(db: Session, frame_ids: list[int]) -> dict[int, tuple[Frame, str]]:
    """Imagen y nombre de su video, por id."""
    if not frame_ids:
        return {}
    q = select(Frame, Video.name).join(Video, Video.id == Frame.video_id).where(Frame.id.in_(frame_ids))
    return {f.id: (f, vn) for f, vn in db.execute(q).all()}


def _yolo_line(cls: int, box: list[int], width: int, height: int) -> str | None:
    x, y, w, h = box
    x0, y0, x1, y1 = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    cx, cy, bw, bh = (x0 + x1) / 2 / width, (y0 + y1) / 2 / height, (x1 - x0) / width, (y1 - y0) / height
    return f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def create_version(
    db: Session, project: Project, name: str, include: list[str], fractions, seed: str, user_id: int
) -> DatasetVersion:
    """Congela las etiquetas finales de las imágenes cuyo estado esté en `include`. Después ya no cambian."""
    states = compute_states(db, project)
    ids = [i for i, s in states.items() if s.status in include and s.final_user_id is not None]
    rows = [(fid, vn) for fid, (_, vn) in sorted(_frame_rows(db, ids).items(), key=lambda kv: kv[1][0].position)]
    v = DatasetVersion(
        project_id=project.id,
        name=name,
        created_by=user_id,
        rules={"include": include, "split": list(fractions), "seed": seed},
    )
    db.add(v)
    db.flush()
    for fid, vname in rows:
        st: FrameState = states[fid]
        ann = final_annotation(db, fid, st)
        if ann is None:
            continue
        v.items.append(
            DatasetItem(
                frame_id=fid,
                split=consensus.split_for_video(vname, seed, tuple(fractions)),
                source=_source(st),
                boxes=[{"bbox": list(bb), "label": lb} for bb, lb in labeled(ann)],
                discarded=discarded(ann),
            )
        )
    db.commit()
    return v


def _source(st: FrameState) -> str:
    return {"final": "review", "agreed": "agreed", "single": "single"}[st.status]


def version_stats(db: Session, version: DatasetVersion, project: Project) -> dict:
    positive, _ = class_sets(project)
    frames = _frame_rows(db, [i.frame_id for i in version.items])
    out = {
        s: {"frames": 0, "frames_with_positive": 0, "videos": set(), "boxes": {c: 0 for c in positive}} for s in SPLITS
    }
    for it in version.items:
        o = out[it.split]
        o["frames"] += 1
        o["videos"].add(frames[it.frame_id][1])
        pos = [b for b in it.boxes if b["label"] in positive]
        o["frames_with_positive"] += bool(pos)
        for b in pos:
            o["boxes"][b["label"]] += 1
    for o in out.values():
        o["videos"] = len(o["videos"])
    warnings = [
        f"La partición {s} no tiene ninguna caja positiva"
        for s, o in out.items()
        if o["frames"] and not any(o["boxes"].values())
    ]
    warnings += [f"La partición {s} está vacía" for s, o in out.items() if not o["frames"]]
    return {
        "id": version.id,
        "name": version.name,
        "rules": version.rules,
        "n_items": len(version.items),
        "splits": out,
        "warnings": warnings,
    }


def _fname(video: str, frame_index: int) -> str:
    return f"{video}__f{frame_index:06d}.jpg"


DISCARDED_CLASS = "descartada"


def export_version(
    db: Session, version: DatasetVersion, project: Project, out_dir: Path, fmt: str, include_discarded: bool = False
) -> dict:
    """Escribe la versión en disco. Las imágenes son ENLACES a los originales (no se copian: el disco es limitado).

    Con `include_discarded` también salen las cajas descartadas, como clases adicionales después de las positivas: las
    de clases negativas (p. ej. «ni piojo ni mosca») con su propia clase y las borradas por la persona como «descartada»
    (los rechazos de un detector sirven de ejemplos negativos). Las versiones anteriores a esta opción no guardaron las
    borradas.
    """
    positive, negative = class_sets(project)
    neg_ids = [c["id"] for c in (project.config or {}).get("classes", []) if c["id"] in negative]
    classes = positive + (neg_ids + [DISCARDED_CLASS] if include_discarded else [])
    cls_idx = {c: i for i, c in enumerate(classes)}
    out_dir = Path(out_dir)
    media = Path(settings.media_root).resolve()
    rows = _frame_rows(db, [i.frame_id for i in version.items])
    counts = {s: 0 for s in SPLITS}
    boxes_n = {s: {c: 0 for c in classes} for s in SPLITS}
    coco = {
        s: {"images": [], "annotations": [], "categories": [{"id": i + 1, "name": c} for i, c in enumerate(classes)]}
        for s in SPLITS
    }
    ann_id = 1
    for it in version.items:
        f, vname = rows[it.frame_id]
        name = _fname(vname, f.frame_index)
        pos = [b for b in it.boxes if b["label"] in cls_idx]
        if include_discarded:
            pos += [{"bbox": b["bbox"], "label": DISCARDED_CLASS} for b in (it.discarded or [])]
        src = media / f.image_path
        for sub in ("images", "labels"):
            (out_dir / sub / it.split).mkdir(parents=True, exist_ok=True)
        link = out_dir / "images" / it.split / name
        if not link.is_symlink() and not link.exists():
            os.symlink(src, link)
        counts[it.split] += 1
        if fmt in ("yolo", "both"):
            lines = [ln for b in pos if (ln := _yolo_line(cls_idx[b["label"]], b["bbox"], f.width, f.height))]
            (out_dir / "labels" / it.split / name.replace(".jpg", ".txt")).write_text(
                "\n".join(lines) + ("\n" if lines else "")
            )
        coco[it.split]["images"].append(
            {"id": f.id, "file_name": f"images/{it.split}/{name}", "width": f.width, "height": f.height}
        )
        for b in pos:
            x, y, w, h = b["bbox"]
            coco[it.split]["annotations"].append(
                {
                    "id": ann_id,
                    "image_id": f.id,
                    "category_id": cls_idx[b["label"]] + 1,
                    "bbox": [x, y, w, h],
                    "area": w * h,
                    "iscrowd": 0,
                }
            )
            ann_id += 1
            boxes_n[it.split][b["label"]] += 1
    if fmt in ("yolo", "both"):
        names = "\n".join(f"  {i}: {c}" for i, c in enumerate(classes))
        (out_dir / "data.yaml").write_text(
            f"path: {out_dir.resolve()}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n{names}\n"
        )
    if fmt in ("coco", "both"):
        for s in SPLITS:
            (out_dir / f"annotations_{s}.json").write_text(json.dumps(coco[s]))
    manifest = {
        "project": project.slug,
        "version": version.name,
        "rules": version.rules,
        "format": fmt,
        "classes": classes,
        "include_discarded": include_discarded,
        "frames": counts,
        "boxes": boxes_n,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
