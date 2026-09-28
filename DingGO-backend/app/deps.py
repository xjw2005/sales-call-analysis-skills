from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Store, User, Visit
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


# 第一期：每个销售只能看到自己的门店和拜访
def own_store(db: Session, user: User, store_id: int) -> Store:
    store = db.get(Store, store_id)
    if not store or store.owner_id != user.id:
        raise HTTPException(status_code=404, detail="门店不存在")
    return store


def own_visit(db: Session, user: User, visit_id: int) -> Visit:
    visit = db.get(Visit, visit_id)
    if not visit or visit.user_id != user.id:
        raise HTTPException(status_code=404, detail="拜访记录不存在")
    return visit
