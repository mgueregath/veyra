from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Membership, User

WRITE_ROLES = {"admin", "labeler", "reviewer"}


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user or not user.is_active:
        raise HTTPException(401, "No autenticado")
    return user


def role_in(db: Session, user: User, project_id: int) -> str | None:
    if user.is_superuser:
        return "admin"
    return db.scalar(select(Membership.role).where(Membership.user_id == user.id, Membership.project_id == project_id))
