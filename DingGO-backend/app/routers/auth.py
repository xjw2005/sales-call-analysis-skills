from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..security import create_token
from ..services.wx import code_to_openid

router = APIRouter(prefix="/auth", tags=["登录"])


class LoginIn(BaseModel):
    code: str
    name: str = ""


def user_out(u: User) -> dict:
    return {"id": str(u.id), "name": u.name, "role": u.role, "region": u.region}


@router.post("/wx-login")
def wx_login(body: LoginIn, db: Session = Depends(get_db)):
    openid = code_to_openid(body.code)
    user = db.scalars(select(User).where(User.openid == openid)).first()
    if user is None:
        user = User(openid=openid, name=body.name or "销售")
        db.add(user)
        db.commit()
    elif body.name and body.name != user.name:
        user.name = body.name
        db.commit()
    return {"token": create_token(user.id), "user": user_out(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)
