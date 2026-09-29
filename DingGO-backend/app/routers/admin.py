from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Store, Todo, User, Visit
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


@router.post("/users/merge")
def merge_users(body: MergeIn, db: Session = Depends(get_db)):
    """把新登录的微信账号并入旧数据里的人员：旧人员获得微信身份，新账号删除（它不能已有业务数据）"""
    new, old = db.get(User, int(body.fromId)), db.get(User, int(body.intoId))
    if not new or not old or new.id == old.id:
        raise HTTPException(status_code=404, detail="用户不存在")
    if old.openid is not None:
        raise HTTPException(status_code=409, detail="目标人员已经绑定了微信账号")
    if new.openid is None:
        raise HTTPException(status_code=400, detail="来源账号没有微信身份")
    used = sum(
        db.scalar(select(func.count()).select_from(model).where(cond)) or 0
        for model, cond in (
            (Store, Store.primary_sales_id == new.id), (Visit, Visit.visitor_id == new.id),
            (Todo, (Todo.assignee_id == new.id) | (Todo.created_by == new.id)), (User, User.manager_id == new.id),
        )
    )
    if used:
        raise HTTPException(status_code=409, detail="来源账号已经有门店、拜访或待办，不能合并")
    openid = new.openid
    db.delete(new)
    db.flush()
    old.openid = openid
    db.commit()
    return user_out(old)


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
