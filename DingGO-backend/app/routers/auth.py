import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..config import get_settings
from ..models import BindCode, User, utcnow
from ..security import create_bind_token, create_token, decode_bind_token, hash_bind_code
from ..services.wx import code_to_openid

router = APIRouter(prefix="/auth", tags=["登录"])


class LoginIn(BaseModel):
    code: str
    name: str = ""


def user_out(u: User) -> dict:
    return {"id": str(u.id), "name": u.name, "role": u.role, "region": u.region, "managerId": str(u.manager_id) if u.manager_id else ""}


@router.post("/wx-login")
def wx_login(body: LoginIn, db: Session = Depends(get_db)):
    openid = code_to_openid(body.code)
    user = db.scalars(select(User).where(User.openid == openid)).first()
    if user is None:
        st = get_settings()
        if st.require_bind_code and not (st.dev_login and openid.startswith("dev-")):
            # 不认识的微信：不自动建账号，让对方输入管理员发的绑定码
            return {"needBind": True, "bindToken": create_bind_token(openid)}
        user = User(openid=openid, name=body.name or "销售")
        db.add(user)
        db.commit()
    return {"token": create_token(user.id), "user": user_out(user)}


class BindIn(BaseModel):
    bindToken: str
    code: str


_FAILS: dict[str, deque] = defaultdict(deque)
MAX_FAILS, LOCK_SECONDS = 5, 600


@router.post("/bind")
def bind(body: BindIn, db: Session = Depends(get_db)):
    """第一次使用：输入管理员发的一次性绑定码，把自己的微信绑定到对应的人员记录上"""
    openid = decode_bind_token(body.bindToken)
    if openid is None:
        raise HTTPException(status_code=401, detail="验证已过期，请重新打开小程序")
    now = time.time()
    fails = _FAILS[openid]
    while fails and now - fails[0] > LOCK_SECONDS:
        fails.popleft()
    if len(fails) >= MAX_FAILS:
        raise HTTPException(status_code=429, detail="输入错误次数太多，请 10 分钟后再试")
    row = db.scalars(select(BindCode).where(BindCode.code_hash == hash_bind_code(body.code))).first()
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        fails.append(now)
        raise HTTPException(status_code=400, detail="绑定码不正确或已过期，请向管理员重新要一个")
    user = db.get(User, row.user_id)
    if user is None or user.openid is not None:
        raise HTTPException(status_code=409, detail="这个人员已经绑定过微信了")
    if db.scalars(select(User).where(User.openid == openid)).first() is not None:
        raise HTTPException(status_code=409, detail="这个微信已经绑定过了")
    user.openid, row.used_at = openid, utcnow()
    db.commit()
    _FAILS.pop(openid, None)
    return {"token": create_token(user.id), "user": user_out(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)
