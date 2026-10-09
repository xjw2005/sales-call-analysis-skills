"""账号合并：同事先用微信登录产生一个新账号，再并入旧数据里的人员（旧人员获得微信身份，新账号删除）。

- 新账号名下有业务数据（门店、拜访、待办、下属）→ 拒绝合并（由管理员先处理，避免张冠李戴）；
- 助手相关的数据（对话、记忆、今日计划、提问记录等）和其它指向用户的记录，一律迁到旧人员名下，不能丢；
  指向用户的外键是从模型元数据里自动找出来的，以后新增带用户外键的表会自动被迁移，不会因为外键报错或漏迁。
- 唯一约束冲突时（两边都有同一条记忆 / 同一天同一家店的计划）保留旧人员的，丢掉新账号的重复项。
"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..db import Base
from ..models import User, UserMemory, VisitPlan

# 这些列上有数据就说明新账号已经开始做业务了：不合并
BUSINESS_COLUMNS = {("stores", "primary_sales_id"), ("visits", "visitor_id"), ("todos", "assignee_id"), ("todos", "created_by"), ("users", "manager_id")}


class MergeRefused(Exception):
    pass


def user_fk_columns():
    """所有指向 users.id 的外键列：[(表对象, 列名)]"""
    out = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if any(fk.column.table.name == "users" and fk.column.name == "id" for fk in col.foreign_keys):
                out.append((table, col.name))
    return out


def merge_accounts(db: Session, new: User, old: User) -> dict:
    cols = user_fk_columns()
    busy = []
    for table, name in cols:
        if (table.name, name) in BUSINESS_COLUMNS and db.scalar(select(func.count()).select_from(table).where(table.c[name] == new.id)):
            busy.append(f"{table.name}.{name}")
    if busy:
        raise MergeRefused("来源账号已经有门店、拜访或待办，不能合并")

    # 唯一约束冲突：旧人员已经有的，丢掉新账号重复的
    old_mem = {(m.kind, m.key, m.scope) for m in db.scalars(select(UserMemory).where(UserMemory.user_id == old.id))}
    for m in list(db.scalars(select(UserMemory).where(UserMemory.user_id == new.id))):
        if (m.kind, m.key, m.scope) in old_mem:
            db.delete(m)
    old_plans = {(p.plan_date, p.store_id) for p in db.scalars(select(VisitPlan).where(VisitPlan.user_id == old.id))}
    for p in list(db.scalars(select(VisitPlan).where(VisitPlan.user_id == new.id))):
        if (p.plan_date, p.store_id) in old_plans:
            db.delete(p)
    db.flush()

    moved = {}
    for table, name in cols:
        res = db.execute(update(table).where(table.c[name] == new.id).values({name: old.id}))
        if res.rowcount:
            moved[f"{table.name}.{name}"] = res.rowcount
    return moved


def bind_wechat(db: Session, new: User, old: User) -> dict:
    """完整的合并：迁移数据、把微信身份给旧人员、删除新账号"""
    if old.openid is not None:
        raise MergeRefused("目标人员已经绑定了微信账号")
    if new.openid is None:
        raise MergeRefused("来源账号没有微信身份")
    moved = merge_accounts(db, new, old)
    openid = new.openid
    db.execute(delete(User).where(User.id == new.id))
    db.flush()
    old.openid = openid
    return moved
