"""Utilidades de línea de comandos: `python -m app.cli --help`."""

import argparse
import getpass
from pathlib import Path

from .db import SessionLocal
from .legacy_import import ensure_member, ensure_project, ensure_user, import_legacy
from .models import ROLES
from .security import hash_password


def main(argv=None):
    ap = argparse.ArgumentParser(prog="labelhub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("createuser", help="crea (o actualiza la contraseña de) un usuario")
    u.add_argument("username")
    u.add_argument("--password", help="si se omite se pide por teclado")
    u.add_argument("--superuser", action="store_true")
    m = sub.add_parser("addmember", help="da acceso a un proyecto con un rol")
    m.add_argument("username")
    m.add_argument("project")
    m.add_argument("role", choices=ROLES)
    i = sub.add_parser("import-legacy", help="importa frames.json + labels.json de la herramienta anterior")
    i.add_argument("legacy_dir", type=Path)
    i.add_argument("--project", default="piojos")
    i.add_argument("--name", default="Piojos en lana")
    i.add_argument("--videos-dir", type=Path)
    cp = sub.add_parser("copy-annotations", help="copia las anotaciones de un usuario a otro (no pisa las existentes)")
    cp.add_argument("source")
    cp.add_argument("target")
    cp.add_argument("--project", default="piojos")
    ex = sub.add_parser("export-dataset", help="exporta una versión de dataset a YOLO/COCO")
    ex.add_argument("project")
    ex.add_argument("version")
    ex.add_argument("--out", type=Path, required=True)
    ex.add_argument("--format", choices=["yolo", "coco", "both"], default="yolo")
    ex.add_argument(
        "--include-discarded", action="store_true", help="incluye las cajas descartadas como clases adicionales"
    )
    rs = sub.add_parser("restore-project", help="crea un proyecto nuevo desde una copia (.json.gz) descargada")
    rs.add_argument("file", type=Path)
    rs.add_argument("--slug")
    rs.add_argument("--name")
    c = sub.add_parser("set-config", help="reemplaza la configuración (JSON) de un proyecto")
    c.add_argument("project")
    c.add_argument("json_file", type=Path)
    a = ap.parse_args(argv)

    with SessionLocal() as db:
        if a.cmd == "createuser":
            pw = a.password or getpass.getpass("Contraseña: ")
            user = ensure_user(db, a.username)
            user.password_hash = hash_password(pw)
            user.is_superuser = a.superuser or user.is_superuser
            db.commit()
            print(f"usuario {a.username} listo")
        elif a.cmd == "addmember":
            from sqlalchemy import select

            from .models import Project, User

            user = db.scalar(select(User).where(User.username == a.username))
            project = db.scalar(select(Project).where(Project.slug == a.project))
            if not user or not project:
                raise SystemExit("usuario o proyecto inexistente")
            ensure_member(db, user, project, a.role)
            db.commit()
            print(f"{a.username} -> {a.project} ({a.role})")
        elif a.cmd == "copy-annotations":
            from .legacy_import import copy_annotations

            print(copy_annotations(db, a.source, a.target, a.project))
        elif a.cmd == "export-dataset":
            from sqlalchemy import select

            from .datasets import export_version
            from .models import DatasetVersion, Project

            project = db.scalar(select(Project).where(Project.slug == a.project))
            version = (
                db.scalar(
                    select(DatasetVersion).where(
                        DatasetVersion.project_id == project.id, DatasetVersion.name == a.version
                    )
                )
                if project
                else None
            )
            if not version:
                raise SystemExit("proyecto o versión inexistente")
            print(export_version(db, version, project, a.out, a.format, a.include_discarded))
        elif a.cmd == "restore-project":
            from .backup import load_bytes, restore_project

            p = restore_project(db, load_bytes(a.file.read_bytes()), a.slug, a.name)
            print(f"proyecto {p.slug} restaurado (id {p.id})")
        elif a.cmd == "set-config":
            import json

            from sqlalchemy import select

            from .models import Project

            project = db.scalar(select(Project).where(Project.slug == a.project))
            if not project:
                raise SystemExit("proyecto inexistente")
            project.config = json.loads(a.json_file.read_text())
            db.commit()
            print(f"configuración de {a.project} actualizada")
        elif a.cmd == "import-legacy":
            ensure_project(db, a.project, a.name)
            print(import_legacy(db, a.legacy_dir, a.project, a.name, a.videos_dir))


if __name__ == "__main__":
    main()
