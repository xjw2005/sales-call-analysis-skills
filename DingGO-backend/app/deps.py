from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User
from .security import decode_token


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    user_id = decode_token(token) if token else None
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(x_admin_token: str = Header(default="")) -> None:
    token = get_settings().admin_token
    if not token or x_admin_token != token:
        raise HTTPException(status_code=403, detail="无权限")
