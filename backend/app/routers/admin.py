import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import backup, usage
from ..db import get_db
from ..deps import current_user, role_in
from ..models import ROLES, AuditLog, Membership, Project, Run, User
from ..security import hash_password, verify_password

router = APIRouter(prefix="/api", tags=["admin"])


def _admin(db: Session, user: User, pid: int) -> Project:
    p = db.get(Project, pid)
    role = role_in(db, user, pid) if p else None
    if not p or not role:
        raise HTTPException(404, "Proyecto no encontrado")
    if role != "admin":
        raise HTTPException(403, "Solo un administrador del proyecto puede hacer esto")
    return p


class MemberIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.\-]{2,64}$")
    role: str
    password: str | None = Field(None, min_length=8, max_length=200)  # si el usuario no existe, es obligatoria
    display_name: str | None = Field(None, max_length=128)


class RoleIn(BaseModel):
    role: str


class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class OwnPasswordIn(BaseModel):
    old: str
    new: str = Field(min_length=8, max_length=200)


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise HTTPException(422, f"Rol inválido (opciones: {', '.join(ROLES)})")


def _last_admin(db: Session, pid: int, username: str) -> bool:
    admins = db.scalars(
        select(User.username)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.project_id == pid, Membership.role == "admin")
    ).all()
    return admins == [username]


@router.get("/projects/{project_id}/team")
def team(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(db, user, project_id)
    rows = db.execute(
        select(User.username, User.display_name, User.is_active, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.project_id == project_id)
        .order_by(User.username)
    ).all()
    return [{"username": u, "display_name": d, "active": a, "role": r, "has_password": True} for u, d, a, r in rows]


@router.post("/projects/{project_id}/members")
def add_member(project_id: int, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Da acceso a un usuario; si no existe lo crea (con contraseña inicial)."""
    p = _admin(db, user, project_id)
    _check_role(body.role)
    target = db.scalar(select(User).where(User.username == body.username))
    if target is None:
        if not body.password:
            raise HTTPException(422, "El usuario no existe: indique una contraseña inicial para crearlo")
        target = User(
            username=body.username, display_name=body.display_name, password_hash=hash_password(body.password)
        )
        db.add(target)
        db.flush()
    if db.scalar(select(Membership.id).where(Membership.user_id == target.id, Membership.project_id == p.id)):
        raise HTTPException(409, "Ya pertenece al proyecto: cambie su rol")
    db.add(Membership(user_id=target.id, project_id=p.id, role=body.role))
    db.add(
        AuditLog(
            user_id=user.id, project_id=p.id, action="member.add", target=body.username, detail={"role": body.role}
        )
    )
    db.commit()
    return {"username": target.username, "role": body.role}


@router.put("/projects/{project_id}/members/{username}")
def set_role(
    project_id: int, username: str, body: RoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = _admin(db, user, project_id)
    _check_role(body.role)
    m = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(User.username == username, Membership.project_id == p.id)
    )
    if not m:
        raise HTTPException(404, "Ese usuario no pertenece al proyecto")
    if m.role == "admin" and body.role != "admin" and _last_admin(db, p.id, username):
        raise HTTPException(409, "No se puede quitar el último administrador del proyecto")
    m.role = body.role
    db.add(
        AuditLog(user_id=user.id, project_id=p.id, action="member.role", target=username, detail={"role": body.role})
    )
    db.commit()
    return {"username": username, "role": body.role}


@router.delete("/projects/{project_id}/members/{username}")
def remove_member(project_id: int, username: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _admin(db, user, project_id)
    m = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(User.username == username, Membership.project_id == p.id)
    )
    if not m:
        raise HTTPException(404, "Ese usuario no pertenece al proyecto")
    if m.role == "admin" and _last_admin(db, p.id, username):
        raise HTTPException(409, "No se puede quitar el último administrador del proyecto")
    db.delete(m)  # su trabajo (anotaciones, historial) se conserva
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="member.remove", target=username))
    db.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/members/{username}/password")
def reset_password(
    project_id: int, username: str, body: PasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    p = _admin(db, user, project_id)
    t = db.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(User.username == username, Membership.project_id == p.id)
    )
    if not t:
        raise HTTPException(404, "Ese usuario no pertenece al proyecto")
    if t.is_superuser and not user.is_superuser:
        raise HTTPException(403, "No se puede cambiar la contraseña de un superusuario")
    t.password_hash = hash_password(body.password)
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="member.password_reset", target=username))
    db.commit()
    return {"ok": True}


@router.post("/me/password")
def change_own_password(body: OwnPasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old, user.password_hash):
        raise HTTPException(403, "La contraseña actual no es correcta")
    user.password_hash = hash_password(body.new)
    db.add(AuditLog(user_id=user.id, action="password.change", target=user.username))
    db.commit()
    return {"ok": True}


@router.get("/projects/{project_id}/usage")
def project_usage(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _admin(db, user, project_id)
    return usage.project_usage(db, p)


class QuotaIn(BaseModel):
    quota_gb: float | None = Field(None, gt=0, le=100000)


@router.put("/projects/{project_id}/quota")
def set_quota(project_id: int, body: QuotaIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _admin(db, user, project_id)
    cfg = dict(p.config or {})
    if body.quota_gb is None:
        cfg.pop("quota_gb", None)
    else:
        cfg["quota_gb"] = body.quota_gb
    p.config = cfg
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="quota.set", detail={"quota_gb": body.quota_gb}))
    db.commit()
    return usage.project_usage(db, p)


@router.get("/projects/{project_id}/system")
def system(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Estado de los trabajos y la actividad reciente, para vigilar el servicio."""
    p = _admin(db, user, project_id)
    counts = dict(
        db.execute(
            select(Run.status, func.count())
            .where(Run.project_id == p.id, Run.status.in_(["queued", "running"]))
            .group_by(Run.status)
        ).all()
    )
    last = db.scalars(select(AuditLog).where(AuditLog.project_id == p.id).order_by(AuditLog.id.desc()).limit(15)).all()
    names = (
        {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({a.user_id for a in last if a.user_id})))}
        if last
        else {}
    )
    from .. import live

    return {
        "jobs": {"queued": counts.get("queued", 0), "running": counts.get("running", 0)},
        "live_active": live.active_count(),
        "recent": [
            {"at": a.at.isoformat(), "user": names.get(a.user_id), "action": a.action, "target": a.target} for a in last
        ],
    }


@router.get("/projects/{project_id}/backup")
def download_backup(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Copia completa del proyecto (etiquetas, revisiones, datasets, equipo…) para restaurarla en otra instalación."""
    p = _admin(db, user, project_id)
    raw = backup.dump_bytes(db, p)
    db.add(AuditLog(user_id=user.id, project_id=p.id, action="project.backup", detail={"bytes": len(raw)}))
    db.commit()
    fname = f"labelhub-{re.sub(r'[^A-Za-z0-9_.-]', '_', p.slug)}-{date.today().isoformat()}.json.gz"
    return Response(
        raw, media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )
