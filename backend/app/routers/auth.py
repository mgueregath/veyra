from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..schemas import LoginIn, MeOut
from ..security import verify_password

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login", response_model=MeOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    request.session["uid"] = user.id
    return user


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)):
    return user
