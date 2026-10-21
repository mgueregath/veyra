"""Espacios de trabajo por proyecto (VS Code por SSH en el equipo con GPU) y las llaves SSH de las personas."""

import base64
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import remote
from ..config import settings
from ..db import get_db
from ..deps import current_user, role_in
from ..models import AuditLog, Membership, Project, SshKey, User, Workspace

router = APIRouter(prefix="/api", tags=["workspaces"])
KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
}
MEMORY = re.compile(r"^[1-9][0-9]{0,3}[mg]$")
START_TIMEOUT = 240  # s: lo que se espera a que un worker tome el trabajo y arranque el contenedor


# ---------------------------------------------------------------- llaves SSH
def parse_key(text: str) -> tuple[str, str]:
    """(llave normalizada «tipo base64», huella SHA256). Valida el tipo y que el cuerpo coincida con él."""
    parts = text.strip().split()
    if len(parts) < 2 or parts[0] not in KEY_TYPES:
        raise HTTPException(422, "No es una llave pública SSH válida (pega el contenido de tu archivo .pub)")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        n = int.from_bytes(blob[:4], "big")
        if blob[4 : 4 + n].decode() != parts[0]:
            raise ValueError
    except Exception:
        raise HTTPException(422, "La llave está dañada o incompleta: copia el archivo .pub completo") from None
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return f"{parts[0]} {parts[1]}", fp


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    public_key: str = Field(max_length=2000)


def _key_out(k: SshKey) -> dict:
    return {
        "id": k.id,
        "name": k.name,
        "type": k.public_key.split()[0],
        "fingerprint": k.fingerprint,
        "created_at": k.created_at.isoformat(),
    }


@router.get("/me/ssh-keys")
def my_keys(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_key_out(k) for k in db.scalars(select(SshKey).where(SshKey.user_id == user.id).order_by(SshKey.id))]


@router.post("/me/ssh-keys")
def add_key(body: KeyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if "PRIVATE KEY" in body.public_key:
        raise HTTPException(422, "Eso es una llave PRIVADA: no la compartas. Pega solo la pública (archivo .pub)")
    key, fp = parse_key(body.public_key)
    if db.scalar(select(SshKey.id).where(SshKey.fingerprint == fp)):
        raise HTTPException(409, "Esa llave ya está registrada")
    k = SshKey(user_id=user.id, name=body.name.strip(), public_key=key, fingerprint=fp)
    db.add(k)
    db.add(AuditLog(user_id=user.id, action="sshkey.add", target=fp))
    db.commit()
    return _key_out(k)


@router.delete("/me/ssh-keys/{key_id}")
def delete_key(key_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    k = db.get(SshKey, key_id)
    if not k or k.user_id != user.id:
        raise HTTPException(404, "Llave no encontrada")
    db.add(AuditLog(user_id=user.id, action="sshkey.delete", target=k.fingerprint))
    db.delete(k)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- espacio de trabajo
def _project(db: Session, user: User, pid: int) -> Project:
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role != "admin":
        raise HTTPException(403, "Solo un administrador del proyecto puede usar el espacio de trabajo")
    return p


def _free_port(db: Session) -> int:
    lo, _, hi = settings.workspace_ports.partition("-")
    taken = set(db.scalars(select(Workspace.port)))
    for port in range(int(lo), int(hi or lo) + 1):
        if port not in taken:
            return port
    raise HTTPException(507, "No quedan puertos libres para espacios de trabajo")


def _workspace(db: Session, project: Project, user: User) -> Workspace:
    w = db.scalar(select(Workspace).where(Workspace.project_id == project.id))
    if w is None:
        w = Workspace(project_id=project.id, port=_free_port(db), created_by=user.id)
        db.add(w)
        db.commit()
    return w


def _admin_keys(db: Session, project: Project, user: User) -> tuple[list[str], list[str], list[str]]:
    """Llaves de quienes administran el proyecto (más quien lo enciende): (llaves, con llave, sin llave)."""
    admins = set(
        db.scalars(select(Membership.user_id).where(Membership.project_id == project.id, Membership.role == "admin"))
    )
    admins.add(user.id)
    keys, with_key, without = [], [], []
    for u in db.scalars(select(User).where(User.id.in_(admins), User.is_active).order_by(User.username)):
        ks = db.scalars(select(SshKey).where(SshKey.user_id == u.id).order_by(SshKey.id)).all()
        (with_key if ks else without).append(u.username)
        keys += [k.public_key + f" {u.username}" for k in ks]
    return keys, with_key, without


def _state(db: Session, project: Project, w: Workspace) -> dict:
    job = remote.get(w.job_id) if w.job_id else None
    status, error, info = "stopped", "", {}
    if job:
        prog = job["progress"]
        if job["status"] == "queued":
            status = "starting"
        elif job["status"] == "running":
            status = "running" if "port" in prog else "starting"
            info = {k: prog.get(k) for k in ("host", "port", "user", "workdir")}
        elif job["status"] == "failed":
            status, error = "failed", job["error"]
        else:
            res = job["result"] or {}
            info = {"last_stop": res.get("reason", job["status"]), "autosave": res.get("autosave", "")}
    workers = [x for x in remote.online_workers(db, "workspace")]
    out = {
        "status": status,
        "error": error,
        "port": w.port,
        "idle_minutes": w.idle_minutes,
        "cpus": w.cpus,
        "memory": w.memory,
        "slug": project.slug,
        "worker_available": bool(workers),
        **info,
    }
    if status == "running":
        alias = f"veyra-{project.slug}"
        out["ssh_config"] = (
            f"Host {alias}\n"
            f"  HostName {info['host']}\n"
            f"  Port {info['port']}\n"
            f"  User {info['user']}\n"
            "  IdentityFile ~/.ssh/TU_LLAVE_PRIVADA\n"
        )
        out["vscode_url"] = f"vscode://vscode-remote/ssh-remote+{alias}{info['workdir']}"
        out["vscode_cmd"] = f"code --remote ssh-remote+{alias} {info['workdir']}"
    return out


@router.get("/projects/{project_id}/workspace")
def get_workspace(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id)
    w = _workspace(db, p, user)
    out = _state(db, p, w)
    _, with_key, without = _admin_keys(db, p, user)
    out["keys"] = {"with_key": with_key, "without_key": without}
    return out


class StartIn(BaseModel):
    idle_minutes: float | None = Field(None, ge=0, le=24 * 60)
    cpus: float | None = Field(None, ge=0.5, le=64)
    memory: str | None = None


@router.post("/projects/{project_id}/workspace/start")
def start_workspace(
    project_id: int, body: StartIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = _project(db, user, project_id)
    w = _workspace(db, p, user)
    if body:
        if body.memory is not None:
            if not MEMORY.match(body.memory):
                raise HTTPException(422, "Memoria inválida (ejemplos: 8g, 16g, 4096m)")
            w.memory = body.memory
        w.idle_minutes = w.idle_minutes if body.idle_minutes is None else body.idle_minutes
        w.cpus = w.cpus if body.cpus is None else body.cpus
        db.commit()
    state = _state(db, p, w)
    if state["status"] in ("starting", "running"):
        return state
    keys, _, _ = _admin_keys(db, p, user)
    if not keys:
        raise HTTPException(422, "Registra primero una llave pública SSH en tu menú de usuario («Mis llaves SSH»)")
    try:
        worker = remote.pick_worker(db, "workspace", force=True)
    except remote.RemoteUnavailable:
        raise HTTPException(503, "No hay ningún worker de GPU en línea que ofrezca espacios de trabajo") from None
    w.job_id = remote.submit(
        "workspace",
        {
            "slug": p.slug,
            "port": w.port,
            "keys": keys,
            "idle_minutes": w.idle_minutes,
            "cpus": w.cpus,
            "memory": w.memory,
            "gpu": True,
        },
        worker.id,
    )
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="workspace.start", target=f"port:{w.port}"))
    db.commit()
    return _state(db, p, w)


@router.post("/projects/{project_id}/workspace/stop")
def stop_workspace(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _project(db, user, project_id)
    w = _workspace(db, p, user)
    if w.job_id:
        remote.request_cancel(w.job_id)
        db.add(AuditLog(user_id=user.id, project_id=p.id, action="workspace.stop", target=f"port:{w.port}"))
        db.commit()
    return _state(db, p, w)


@router.patch("/projects/{project_id}/workspace")
def configure_workspace(
    project_id: int, body: StartIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Valores para el próximo arranque (no cambian un espacio que ya está encendido)."""
    p = _project(db, user, project_id)
    w = _workspace(db, p, user)
    if body.memory is not None:
        if not MEMORY.match(body.memory):
            raise HTTPException(422, "Memoria inválida (ejemplos: 8g, 16g, 4096m)")
        w.memory = body.memory
    if body.idle_minutes is not None:
        w.idle_minutes = body.idle_minutes
    if body.cpus is not None:
        w.cpus = body.cpus
    db.commit()
    return _state(db, p, w)
