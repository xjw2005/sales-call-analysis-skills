"""权限：本人 + 下属可见。

- 每个人（销售或经理）能看到：自己名下的门店、自己拜访过的门店，以及下属的这些；
- 拜访：自己拜访的，或者自己名下门店的（别人去过我的店，我能看到）；下属的同理；
- 只有拜访人本人或其上级能上传录音、确认费用、修改分析结果。
"""

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Store, User, Visit


def team_ids(db: Session, user: User, depth: int = 3) -> list[int]:
    """自己 + 所有下属（最多向下 3 层）的用户 id"""
    ids = {user.id}
    frontier = {user.id}
    for _ in range(depth):
        rows = set(db.scalars(select(User.id).where(User.manager_id.in_(frontier))).all()) - ids
        if not rows:
            break
        ids |= rows
        frontier = rows
    return sorted(ids)


def store_filter(ids: list[int]):
    visited = select(Visit.store_id).where(Visit.visitor_id.in_(ids))
    return or_(Store.primary_sales_id.in_(ids), Store.id.in_(visited))


def visit_filter(ids: list[int]):
    owned = select(Store.id).where(Store.primary_sales_id.in_(ids))
    return or_(Visit.visitor_id.in_(ids), Visit.store_id.in_(owned))


def visible_store(db: Session, user: User, store_id: int) -> Store:
    store = db.scalars(select(Store).where(Store.id == store_id, store_filter(team_ids(db, user)))).first()
    if not store:
        raise HTTPException(status_code=404, detail="门店不存在")
    return store


def visible_visit(db: Session, user: User, visit_id: int) -> Visit:
    visit = db.scalars(select(Visit).where(Visit.id == visit_id, visit_filter(team_ids(db, user)))).first()
    if not visit:
        raise HTTPException(status_code=404, detail="拜访记录不存在")
    return visit


def actionable_visit(db: Session, user: User, visit_id: int) -> Visit:
    """能对拜访做写操作（上传、确认费用、改分析）：拜访人本人或其上级"""
    visit = db.get(Visit, visit_id)
    if not visit or visit.visitor_id not in team_ids(db, user):
        raise HTTPException(status_code=404, detail="拜访记录不存在")
    return visit


def is_manager(user: User) -> bool:
    return user.role == "manager"
