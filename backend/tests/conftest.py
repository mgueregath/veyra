"""Pruebas contra una base PostgreSQL REAL y aparte (labelhub_test): nunca tocan los datos de trabajo."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BASE = os.environ.get("LABELHUB_TEST_ADMIN_URL", "postgresql+psycopg2://labelhub:labelhub_dev@127.0.0.1:55433/postgres")
TEST_URL = BASE.rsplit("/", 1)[0] + "/labelhub_test"
os.environ["LABELHUB_DATABASE_URL"] = TEST_URL  # antes de importar app.*
os.environ["LABELHUB_SECRET_KEY"] = "test"

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _database():
    admin = create_engine(BASE, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS labelhub_test WITH (FORCE)"))
        c.execute(text("CREATE DATABASE labelhub_test"))
    # el esquema se crea con las migraciones reales, así se prueban también
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        check=True,
        env={**os.environ, "LABELHUB_DATABASE_URL": TEST_URL},
    )
    yield
    from app.db import engine

    engine.dispose()
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS labelhub_test WITH (FORCE)"))


@pytest.fixture(autouse=True)
def _clean(_database):
    yield
    from app.db import engine

    with engine.begin() as c:
        c.execute(
            text(
                "TRUNCATE audit_log, dataset_items, dataset_versions, reviews, frame_locks, assignments, annotation_boxes, "
                "annotations, proposals, frames, videos, memberships, projects, users RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture()
def db():
    from app.db import SessionLocal

    with SessionLocal() as s:
        yield s


@pytest.fixture()
def media(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "media_root", tmp_path)
    return tmp_path


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def world(db, media):
    """Un proyecto con 2 frames y usuarios de cada rol."""
    from app.legacy_import import DEFAULT_CONFIG, ensure_member, ensure_project, ensure_user
    from app.models import Frame, Proposal, Video
    from app.security import hash_password

    p = ensure_project(db, "demo", "Demo", DEFAULT_CONFIG)
    other = ensure_project(db, "otro", "Otro", DEFAULT_CONFIG)
    users = {}
    for name, role in [
        ("ana", "labeler"),
        ("beto", "labeler"),
        ("rev", "reviewer"),
        ("lector", "viewer"),
        ("adm", "admin"),
    ]:
        u = ensure_user(db, name, hash_password("pw-" + name))
        ensure_member(db, u, p, role)
        users[name] = u
    ensure_user(db, "intruso", hash_password("pw-intruso"))  # sin acceso a ningún proyecto
    root = ensure_user(db, "root", hash_password("pw-root"), is_superuser=True)
    v = Video(project_id=p.id, name="v1", path=None, meta={})
    db.add(v)
    db.flush()
    frames = []
    for i in range(2):
        (media / f"f{i}.jpg").write_bytes(b"\xff\xd8fake\xff\xd9")
        f = Frame(
            project_id=p.id,
            video_id=v.id,
            frame_index=i,
            t=i * 0.5,
            image_path=f"f{i}.jpg",
            width=100,
            height=80,
            stratum="enriched",
            position=i,
        )
        f.proposals = [
            Proposal(x=10, y=10, w=20, h=20, sources=["blob"], score=7.0),
            Proposal(x=50, y=30, w=15, h=15, sources=["motion"], score=4.0),
        ]
        db.add(f)
        frames.append(f)
    db.commit()
    return {"project": p, "other": other, "users": users, "root": root, "frames": frames}


def login(client, user, password=None):
    r = client.post("/api/auth/login", json={"username": user, "password": password or "pw-" + user})
    return r


@pytest.fixture(scope="session")
def vfr_video(tmp_path_factory):
    """Video sintético de tasa variable: 15 fotogramas a 0,1 s y 75 a 1/30 s (90 en total)."""
    p = tmp_path_factory.mktemp("vid") / "vfr.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x96:rate=30:duration=3", "-vf",
                    "setpts='if(lt(N,15),N/10,1.5+(N-15)/30)/TB'", "-vsync", "vfr", "-pix_fmt", "yuv420p", str(p)],
                   check=True)  # fmt: skip
    return p


@pytest.fixture()
def vworld(world, db, vfr_video):
    """`world` con el video real asociado al video del proyecto."""
    from sqlalchemy import select

    from app.models import Video

    v = db.scalar(select(Video).where(Video.name == "v1"))
    v.path = str(vfr_video)
    db.commit()
    world["video"] = v
    return world


@pytest.fixture()
def fake_workers(tmp_path, monkeypatch):
    """Entrenamiento e inferencia con procesos simulados (sin GPU ni PyTorch) y carpetas temporales."""
    from app.config import settings

    here = Path(__file__).parent
    monkeypatch.setattr(settings, "train_python", sys.executable)
    monkeypatch.setattr(settings, "train_script", here / "fake_train.py")
    monkeypatch.setattr(settings, "predict_script", here / "fake_predict.py")
    monkeypatch.setattr(settings, "models_root", tmp_path / "models")
    monkeypatch.setattr(settings, "runs_root", tmp_path / "runs")
    monkeypatch.setattr(settings, "export_root", tmp_path / "exports")
    monkeypatch.setattr(settings, "pretrained_dirs", str(tmp_path))
    yield
    from app import training

    for rid in list(training._procs):  # no dejar procesos vivos entre pruebas
        training.cancel(rid)
