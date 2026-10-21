"""Esquema de datos. Los archivos pesados quedan en disco: aquí solo van rutas relativas y metadatos."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

ROLES = ("admin", "labeler", "reviewer", "viewer")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))  # None = no puede iniciar sesión (p. ej. «legacy»)
    display_name: Mapped[str | None] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    # clases, atajos, colores, fuentes de caja…: lo que antes estaba escrito en el HTML
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str | None] = mapped_column(Text)  # ruta del original (relativa a media_root o absoluta)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # fps, duración, rotación…


class Frame(Base):
    __tablename__ = "frames"
    __table_args__ = (
        UniqueConstraint("video_id", "frame_index"),
        Index("ix_frames_project_stratum", "project_id", "stratum"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    frame_index: Mapped[int] = mapped_column(Integer)
    t: Mapped[float | None] = mapped_column(Float)  # instante exacto (PTS del contenedor)
    image_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    stratum: Mapped[str | None] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)  # orden de presentación dentro del proyecto
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    # papelera: una captura eliminada deja de contar en colas, estadísticas y versiones NUEVAS de dataset
    # (se puede restaurar; las versiones de dataset ya creadas la conservan)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    video: Mapped[Video] = relationship()
    proposals: Mapped[list[Proposal]] = relationship(
        back_populates="frame", order_by="Proposal.id", cascade="all, delete-orphan"
    )


class Proposal(Base):
    """Caja propuesta por un detector (inmutable). Las ediciones humanas viven en annotation_boxes."""

    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    w: Mapped[int] = mapped_column(Integer)
    h: Mapped[int] = mapped_column(Integer)
    sources: Mapped[list] = mapped_column(JSONB, default=list)  # motion, blob, model…
    score: Mapped[float | None] = mapped_column(Float)
    frame: Mapped[Frame] = relationship(back_populates="proposals")


class Annotation(Base):
    """Trabajo de UNA persona sobre UN frame."""

    __tablename__ = "annotations"
    __table_args__ = (UniqueConstraint("frame_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    boxes: Mapped[list[AnnotationBox]] = relationship(
        back_populates="annotation", order_by="AnnotationBox.id", cascade="all, delete-orphan"
    )


class AnnotationBox(Base):
    __tablename__ = "annotation_boxes"
    id: Mapped[int] = mapped_column(primary_key=True)
    annotation_id: Mapped[int] = mapped_column(ForeignKey("annotations.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("proposals.id", ondelete="SET NULL"))
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    w: Mapped[int] = mapped_column(Integer)
    h: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(32))  # None = sin etiquetar
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    orig_bbox: Mapped[list | None] = mapped_column(JSONB)  # posición original si se movió
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    score: Mapped[float | None] = mapped_column(Float)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # borrado suave: restaurable
    annotation: Mapped[Annotation] = relationship(back_populates="boxes")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    detail: Mapped[dict | None] = mapped_column(JSONB)


class Assignment(Base):
    """Plan de trabajo: esta imagen le toca a este usuario."""

    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("frame_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FrameLock(Base):
    """Bloqueo con caducidad: evita que la cola entregue la misma imagen a dos personas a la vez."""

    __tablename__ = "frame_locks"
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class Review(Base):
    """Decisión del revisor: qué anotación es la final de una imagen."""

    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), unique=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    final_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetVersion(Base):
    """Versión INMUTABLE de un dataset: las etiquetas finales se copian en `dataset_items` al crearla."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list[DatasetItem]] = relationship(back_populates="version", cascade="all, delete-orphan")


class DatasetItem(Base):
    __tablename__ = "dataset_items"
    __table_args__ = (UniqueConstraint("version_id", "frame_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id"), index=True)
    split: Mapped[str] = mapped_column(String(8))  # train | val | test (fijo por video, igual en todas las versiones)
    source: Mapped[str] = mapped_column(String(12))  # review | agreed | single
    boxes: Mapped[list] = mapped_column(JSONB, default=list)  # [{"bbox": [x,y,w,h], "label": "louse"}]
    # cajas que la persona descartó (borrado suave) al congelar la versión; solo se exportan si se pide
    discarded: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    version: Mapped[DatasetVersion] = relationship(back_populates="items")


class Run(Base):
    """Trabajo registrado (evaluación, entrenamiento…): quién lo lanzó, con qué datos y qué resultado dio."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # evaluation | training | preannotation
    name: Mapped[str] = mapped_column(String(128))
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | running | done | failed
    log: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Model(Base):
    """Modelo entrenado: pesos en disco (ruta) + de qué versión de dataset salió y cómo rindió."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
    path: Mapped[str] = mapped_column(Text)
    arch: Mapped[str] = mapped_column(String(32))
    classes: Mapped[list] = mapped_column(JSONB, default=list)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LiveSession(Base):
    """Detección en vivo: un proceso aparte por sesión, con su fuente, modelo y recuentos finales."""

    __tablename__ = "live_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("models.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(128))
    source_kind: Mapped[str] = mapped_column(String(8))  # video | url
    source: Mapped[str] = mapped_column(
        Text
    )  # ruta del archivo o URL (puede llevar contraseña: nunca se devuelve tal cual)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    port: Mapped[int | None] = mapped_column(Integer)
    token: Mapped[str | None] = mapped_column(String(64))
    pid: Mapped[int | None] = mapped_column(Integer)
    endpoint: Mapped[str | None] = mapped_column(String(200))  # sesión remota: URL del proceso en el worker
    job_id: Mapped[int | None] = mapped_column(ForeignKey("worker_jobs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="starting")  # starting | running | stopped | failed
    summary: Mapped[dict | None] = mapped_column(JSONB)  # estadísticas al terminar
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class GpuWorker(Base):
    """Equipo con GPU que ejecuta trabajos por encargo de la API (ver el repositorio veyra-worker)."""

    __tablename__ = "gpu_workers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True
    )  # SHA-256 del token; el token solo se muestra al crearlo
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    kinds: Mapped[list] = mapped_column(JSONB, default=list)
    max_batch: Mapped[int] = mapped_column(Integer, default=1)
    max_live: Mapped[int] = mapped_column(Integer, default=1)
    capabilities: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_seen: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkerJob(Base):
    """Un encargo para un worker (train | predict | live): la API lo deja en cola y el worker lo reclama."""

    __tablename__ = "worker_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued | running | done | failed | cancelled
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("gpu_workers.id", ondelete="SET NULL"))
    progress: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    log: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    cancel: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SshKey(Base):
    """Llave pública SSH de una persona: da acceso a los espacios de trabajo de los proyectos que administra."""

    __tablename__ = "ssh_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    public_key: Mapped[str] = mapped_column(Text)  # «tipo base64», sin comentario
    fingerprint: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Workspace(Base):
    """Espacio de trabajo de un proyecto (contenedor con SSH en el equipo con GPU): uno por proyecto, puerto fijo."""

    __tablename__ = "workspaces"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    port: Mapped[int] = mapped_column(Integer, unique=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("worker_jobs.id", ondelete="SET NULL"))
    idle_minutes: Mapped[float] = mapped_column(Float, default=45)  # 0 = no se apaga solo
    cpus: Mapped[float] = mapped_column(Float, default=6)
    memory: Mapped[str] = mapped_column(String(8), default="12g")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DetectionFlow(Base):
    """Flujo de detección guardado: modelos, filtros y procedimientos del proyecto combinados con OR / AND."""

    __tablename__ = "detection_flows"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
