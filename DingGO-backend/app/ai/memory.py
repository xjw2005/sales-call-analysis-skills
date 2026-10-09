"""助手对「这个人」的记忆（学自 eigent 的轻量记忆：分层、带来源、模型只能提议、用户确认才生效）。

只记「这个人怎么说话、怎么用」：地区别名（城东 = 官渡区 + 呈贡区）、常跑区域、纠正过的做法。
不记会变的业务事实（销量、计划、拜访都实时查库）。每个人只能读写自己的记忆；
经理可以把自己的别名设为团队通用（scope=team），其下属也能用。
"""

import re
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import User, UserMemory, utcnow

KINDS = ("alias", "preference", "lesson")
MAX_PER_USER = 30  # 每人最多保留的记忆条数（已生效 + 待确认）
PENDING_TTL_DAYS = 14  # 待确认的提议放这么久没人理就清掉
PROMPT_CHARS = 400  # 每次提问带给模型的记忆总字数上限


class MemoryError_(ValueError):
    pass


def norm_key(key: str) -> str:
    return re.sub(r"\s+", "", str(key or "")).strip()[:64]


def _norm_value(kind: str, value):
    if kind == "alias":
        items = value if isinstance(value, list) else re.split(r"[、,，;；|/]+", str(value or ""))
        items = [str(x).strip()[:30] for x in items if str(x).strip()]
        if not items:
            raise MemoryError_("别名要指向至少一个地区或关键词")
        return items[:8]
    text = str(value or "").strip()[:200]
    if not text:
        raise MemoryError_("内容不能为空")
    return text


def manager_chain(db: Session, user: User, depth: int = 3) -> list[int]:
    ids, cur = [], user
    for _ in range(depth):
        if not cur.manager_id:
            break
        ids.append(cur.manager_id)
        cur = db.get(User, cur.manager_id)
        if cur is None:
            break
    return ids


def visible(db: Session, user: User, status: str | None = "active") -> list[UserMemory]:
    """我能用的记忆：自己的 + 上级设为团队通用的"""
    owners = [user.id] + manager_chain(db, user)
    q = select(UserMemory).where(or_(UserMemory.user_id == user.id, (UserMemory.user_id.in_(owners)) & (UserMemory.scope == "team")))
    if status:
        q = q.where(UserMemory.status == status)
    return list(db.scalars(q.order_by(UserMemory.hits.desc(), UserMemory.id)))


def render(m: UserMemory) -> str:
    if m.kind == "alias":
        return f"「{m.key}」指 {'、'.join(m.value or [])}"
    return f"{m.key}：{m.value}" if m.key else str(m.value)


def relevant_text(db: Session, user: User, question: str) -> str:
    """只取和这句话相关的记忆：别名在话里出现的；偏好/纠正类总是带（量很少）。命中的计数 +1"""
    out, used, hit = [], 0, []
    for m in visible(db, user):
        if m.kind == "alias" and m.key not in question:
            continue
        line = "- " + render(m) + ("（团队通用）" if m.scope == "team" and m.user_id != user.id else "")
        if used + len(line) > PROMPT_CHARS:
            continue
        out.append(line)
        used += len(line)
        hit.append(m)
    for m in hit:
        m.hits += 1
        m.last_used_at = utcnow()
    return "\n".join(out)


def _prune(db: Session, user: User) -> None:
    cutoff = utcnow() - timedelta(days=PENDING_TTL_DAYS)
    for m in db.scalars(select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.status == "pending", UserMemory.created_at < cutoff)):
        db.delete(m)
    db.flush()


def propose(db: Session, user: User, kind: str, key: str, value, reason: str = "", user_said: bool = False,
            question: str = "", session_id: int | None = None) -> tuple[UserMemory | None, str]:
    """模型提议记忆。返回 (记忆, 状态说明)。
    user_said=True 且「说法」确实出现在销售这句话里 → 视为销售明确说的，直接生效；否则只是待确认，必须销售点确认。"""
    if kind not in KINDS:
        raise MemoryError_("记忆类型只能是 alias、preference、lesson")
    key = norm_key(key)
    if kind == "alias" and not key:
        raise MemoryError_("要有说法的名字")
    value = _norm_value(kind, value)
    _prune(db, user)
    existing = db.scalars(select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.kind == kind, UserMemory.key == key, UserMemory.scope == "user")).first()
    explicit = bool(user_said and question and (key in question if key else True))
    if existing is not None:
        if existing.status == "active" and existing.value == value:
            return existing, "已经记住了"
        existing.value, existing.reason = value, reason[:255]
        if explicit:
            existing.status, existing.trust = "active", "user_asserted"
        else:
            existing.status = "pending"  # 内容变了，需要重新确认
        return existing, "已更新" if explicit else "待确认"
    total = db.query(UserMemory).filter(UserMemory.user_id == user.id).count()
    if total >= MAX_PER_USER:
        raise MemoryError_(f"助手最多记 {MAX_PER_USER} 条，请先在「我的」里删掉一些")
    m = UserMemory(user_id=user.id, kind=kind, key=key, value=value, reason=reason[:255], session_id=session_id,
                   status="active" if explicit else "pending", trust="user_asserted" if explicit else "model_inferred")
    db.add(m)
    db.flush()
    return m, "已记住" if explicit else "待确认"


def get_own(db: Session, user: User, memory_id: int) -> UserMemory:
    m = db.get(UserMemory, memory_id)
    if m is None or m.user_id != user.id:
        raise LookupError("记忆不存在")
    return m


def confirm(db: Session, user: User, memory_id: int) -> UserMemory:
    m = get_own(db, user, memory_id)
    m.status, m.trust = "active", "user_confirmed"
    return m


def update(db: Session, user: User, memory_id: int, value=None, key: str | None = None) -> UserMemory:
    m = get_own(db, user, memory_id)
    if key is not None and norm_key(key) != m.key:
        k = norm_key(key)
        clash = db.scalars(select(UserMemory).where(UserMemory.user_id == user.id, UserMemory.kind == m.kind, UserMemory.key == k, UserMemory.scope == m.scope, UserMemory.id != m.id)).first()
        if clash:
            raise MemoryError_("已经有同名的记忆了")
        m.key = k
    if value is not None:
        m.value = _norm_value(m.kind, value)
    m.status, m.trust = "active", "user_asserted"  # 本人手动改的，直接生效
    return m


def remove(db: Session, user: User, memory_id: int) -> None:
    db.delete(get_own(db, user, memory_id))


def set_team(db: Session, user: User, memory_id: int, team: bool) -> UserMemory:
    """经理把自己的别名 / 偏好设为团队通用（或取消）；只有经理能设，且必须是已生效的"""
    m = get_own(db, user, memory_id)
    if user.role != "manager":
        raise MemoryError_("只有经理可以设为团队通用")
    if m.status != "active":
        raise MemoryError_("请先确认这条记忆")
    if m.kind == "lesson":
        raise MemoryError_("纠正过的做法只对自己生效")
    m.scope = "team" if team else "user"
    return m


def serialize(m: UserMemory, me: User) -> dict:
    return {"id": m.id, "kind": m.kind, "key": m.key, "value": m.value, "text": render(m), "scope": m.scope, "status": m.status,
            "trust": m.trust, "reason": m.reason, "hits": m.hits, "mine": m.user_id == me.id}
