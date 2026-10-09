import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..config import get_settings
from ..models import BindCode, User, Visit, utcnow
from ..security import hash_bind_code
from ..services.accounts import MergeRefused, bind_wechat
from ..services.constants import USER_ROLES
from ..services.ingest import apply_analysis
from ..services.summary import serialize_visit
from .auth import user_out

router = APIRouter(prefix="/admin", tags=["管理"], dependencies=[Depends(require_admin)])


class AnalysisIn(BaseModel):
    analysis: dict
    transcript: list | None = None


class UserIn(BaseModel):
    name: str
    role: str = "sales"
    managerId: str | None = None
    region: str = ""


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    managerId: str | None = None
    region: str | None = None


class MergeIn(BaseModel):
    fromId: str  # 刚用微信登录产生的新账号
    intoId: str  # 旧数据导入的人员账号（还没有微信身份）


def _check_role(role: str | None) -> None:
    if role is not None and role not in USER_ROLES:
        raise HTTPException(status_code=400, detail=f"角色只能是：{'、'.join(USER_ROLES)}")


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id)).all()
    return [{**user_out(u), "bound": u.openid is not None} for u in users]


@router.post("/users")
def create_user(body: UserIn, db: Session = Depends(get_db)):
    _check_role(body.role)
    u = User(name=body.name, role=body.role, region=body.region, manager_id=int(body.managerId) if body.managerId else None)
    db.add(u)
    db.commit()
    return user_out(u)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    _check_role(body.role)
    if body.managerId is not None:
        if body.managerId and int(body.managerId) == u.id:
            raise HTTPException(status_code=400, detail="不能把自己设为自己的经理")
        u.manager_id = int(body.managerId) if body.managerId else None
    for field in ("name", "role", "region"):
        if getattr(body, field) is not None:
            setattr(u, field, getattr(body, field))
    db.commit()
    return user_out(u)


@router.post("/users/{user_id}/bind-code")
def create_bind_code(user_id: int, db: Session = Depends(get_db)):
    """给还没绑定微信的人员生成一次性绑定码（明文只返回这一次，库里只存摘要）：发给本人，让他第一次打开小程序时输入"""
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.openid is not None:
        raise HTTPException(status_code=409, detail="这个人员已经绑定了微信")
    for old in db.scalars(select(BindCode).where(BindCode.user_id == u.id, BindCode.used_at.is_(None))):
        old.used_at = utcnow()  # 重新生成后，之前没用的码作废
    code = f"{secrets.randbelow(10**8):08d}"
    minutes = get_settings().bind_code_minutes
    db.add(BindCode(user_id=u.id, code_hash=hash_bind_code(code), expires_at=utcnow() + timedelta(minutes=minutes)))
    db.commit()
    return {"user": user_out(u), "code": code, "expiresInMinutes": minutes}


@router.post("/users/merge")
def merge_users(body: MergeIn, db: Session = Depends(get_db)):
    """把新登录的微信账号并入旧数据里的人员：旧人员获得微信身份，新账号删除（它不能已有业务数据）"""
    new, old = db.get(User, int(body.fromId)), db.get(User, int(body.intoId))
    if not new or not old or new.id == old.id:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        moved = bind_wechat(db, new, old)
    except MergeRefused as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    db.commit()
    out = user_out(old)
    out["moved"] = moved  # 迁移了哪些表多少条（对话、记忆、计划等），方便核对
    return out



@router.post("/visits/{visit_id}/analysis")
def import_analysis(visit_id: int, body: AnalysisIn, db: Session = Depends(get_db)):
    """写入一条拜访的分析结果（结构同小程序 model/analysis.js），需请求头 X-Admin-Token"""
    v = db.get(Visit, visit_id)
    if not v:
        raise HTTPException(status_code=404, detail="拜访记录不存在")
    apply_analysis(db, v, body.analysis, body.transcript)
    return serialize_visit(db, v, full=True)


@router.post("/visits/{visit_id}/retry")
def force_retry(visit_id: int, force: bool = False, db: Session = Depends(get_db)):
    """管理员重试失败的处理任务。force=true 用于「无法确认是否已提交转写」的拜访：
    必须先在火山 LAS 控制台核对该拜访是否已经产生任务，确认没有再强制重试"""
    from ..ai import worker
    v = db.get(Visit, visit_id)
    if v is None:
        raise HTTPException(status_code=404, detail="拜访不存在")
    try:
        worker.retry(db, v, force=force)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.commit()
    return {"ok": True, "status": v.status}
