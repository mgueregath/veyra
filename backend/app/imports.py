"""Carga de imágenes (con o sin etiquetas) y videos a un proyecto.

Etiquetas admitidas junto a las imágenes: YOLO (un .txt por imagen con «clase cx cy ancho alto» normalizados, y
opcionalmente classes.txt o data.yaml con los nombres) y COCO (un .json con images / annotations / categories).
Las funciones de este módulo son puras (sin base de datos ni disco) para poder probarlas solas.
"""

import json
import re
from pathlib import PurePosixPath

import yaml

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MAX_BOXES_PER_IMAGE = 2000


class ImportError_(ValueError):
    pass


def safe_name(name: str) -> str:
    """Nombre de archivo sin carpetas ni caracteres raros."""
    base = PurePosixPath(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._\- ]", "_", base).strip(". ")
    return base[:120] or "archivo"


def parse_names(files: dict[str, bytes]) -> list[str] | None:
    """Nombres de clase por índice: classes.txt, data.yaml (names) u obj.names."""
    for fname, data in files.items():
        low = PurePosixPath(fname).name.lower()
        try:
            if low in ("classes.txt", "obj.names", "labels.txt"):
                return [ln.strip() for ln in data.decode("utf-8-sig").splitlines() if ln.strip()]
            if low in ("data.yaml", "data.yml", "dataset.yaml"):
                names = (yaml.safe_load(data) or {}).get("names")
                if isinstance(names, dict):
                    return [str(names[k]) for k in sorted(names, key=int)]
                if isinstance(names, list):
                    return [str(n) for n in names]
        except (ValueError, yaml.YAMLError, UnicodeDecodeError, TypeError):
            raise ImportError_(f"no se pudo leer la lista de clases ({fname})") from None
    return None


def class_resolver(project_classes: list[dict], names: list[str] | None):
    """Función índice→id de clase del proyecto (None si no se puede asignar) y la lista de avisos que va llenando."""
    warnings: set[str] = set()
    by_key = {}
    for c in project_classes:
        by_key[str(c["id"]).lower()] = c["id"]
        by_key[str(c.get("name", "")).lower()] = c["id"]

    def resolve(idx: int) -> str | None:
        if names is not None:
            if 0 <= idx < len(names):
                got = by_key.get(names[idx].lower())
                if got is None:
                    warnings.add(f"la clase «{names[idx]}» no existe en el proyecto: sus cajas quedan sin etiquetar")
                return got
            warnings.add(f"índice de clase {idx} fuera de la lista de clases")
            return None
        if 0 <= idx < len(project_classes):  # sin nombres: el orden de las clases del proyecto
            return project_classes[idx]["id"]
        warnings.add(f"índice de clase {idx} sin lista de clases: sus cajas quedan sin etiquetar")
        return None

    return resolve, warnings


def parse_yolo(text: str, width: int, height: int, resolve) -> list[dict]:
    """Cajas en píxeles [x, y, w, h] de un .txt YOLO (se ignoran líneas vacías o mal formadas, y polígonos)."""
    out = []
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 5:
            continue
        try:
            k = int(float(p[0]))
            cx, cy, w, h = (float(v) for v in p[1:5])
        except ValueError:
            continue
        x0, y0 = (cx - w / 2) * width, (cy - h / 2) * height
        x1, y1 = (cx + w / 2) * width, (cy + h / 2) * height
        box = _clip(x0, y0, x1, y1, width, height)
        if box:
            out.append({"bbox": box, "label": resolve(k)})
        if len(out) >= MAX_BOXES_PER_IMAGE:
            break
    return out


def _clip(x0: float, y0: float, x1: float, y1: float, width: int, height: int) -> list[int] | None:
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(width), x1), min(float(height), y1)
    w, h = round(x1 - x0), round(y1 - y0)
    if w < 1 or h < 1:
        return None
    return [round(x0), round(y0), w, h]


def parse_coco(data: bytes, resolve_name) -> tuple[dict[str, list[dict]], dict[str, tuple[int, int]]]:
    """Cajas por nombre de archivo (en píxeles) y tamaños declarados. `resolve_name(nombre)` -> id de clase o None."""
    try:
        d = json.loads(data.decode("utf-8-sig"))
        cats = {c["id"]: str(c["name"]) for c in d["categories"]}
        imgs = {i["id"]: i for i in d["images"]}
        anns = d["annotations"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise ImportError_("el archivo COCO no tiene el formato esperado (images, annotations, categories)") from None
    boxes: dict[str, list[dict]] = {}
    for a in anns:
        im = imgs.get(a.get("image_id"))
        if im is None or "bbox" not in a or a.get("iscrowd"):
            continue
        x, y, w, h = (float(v) for v in a["bbox"][:4])
        box = _clip(x, y, x + w, y + h, int(im.get("width") or 10**6), int(im.get("height") or 10**6))
        if box:
            boxes.setdefault(PurePosixPath(im["file_name"]).name, []).append(
                {"bbox": box, "label": resolve_name(cats.get(a.get("category_id"), ""))}
            )
    return boxes, {PurePosixPath(i["file_name"]).name: (i.get("width"), i.get("height")) for i in imgs.values()}
