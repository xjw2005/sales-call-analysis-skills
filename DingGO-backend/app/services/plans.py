"""今日计划：销售先决定今天去哪几家店，再到店录音。

- 候选门店有三个来源：按片区（区县）、最近拜访过的、到期的承诺（临时约定）；
- 销售勾选后写入 visit_plans；创建拜访时自动把对应的计划标为「已去」。
"""

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Store, StoreDirectory, Todo, User, Visit, VisitPlan, utcnow
from .access import store_filter, team_ids
from .timeutil import local_date, today

DUE_SOON_DAYS = 2  # 承诺离到期还有几天就提醒


def district_expr():
    """门店区县：门店自己填的优先，没有就用总门店清单里的"""
    return func.coalesce(func.nullif(Store.district, ""), StoreDirectory.district)


def _store_rows(db: Session, user: User, district: str | None = None):
    q = (
        select(Store.id, Store.name, Store.address, Store.cooperation_status, district_expr().label("district"))
        .outerjoin(StoreDirectory, Store.directory_id == StoreDirectory.id)
        .where(store_filter(team_ids(db, user)))
    )
    if district:
        q = q.where(district_expr() == district)
    return db.execute(q).all()


def _last_visits(db: Session, user: User, store_ids: list[int]) -> dict[int, object]:
    """每家店最近一次拜访的时间（团队可见范围内）"""
    if not store_ids:
        return {}
    out: dict[int, object] = {}
    for i in range(0, len(store_ids), 500):
        chunk = store_ids[i:i + 500]
        for sid, at in db.execute(select(Visit.store_id, func.max(Visit.entered_at)).where(Visit.store_id.in_(chunk)).group_by(Visit.store_id)):
            out[sid] = at
    return out


def _open_todos(db: Session, user: User) -> list[Todo]:
    ids = team_ids(db, user) if user.role == "manager" else [user.id]
    own = select(Store.id).where(Store.primary_sales_id.in_(ids))
    return list(db.scalars(
        select(Todo).where(Todo.status == "open", Todo.store_id.is_not(None))
        .where(or_(Todo.assignee_id.in_(ids), (Todo.assignee_id.is_(None) & Todo.store_id.in_(own))))
        .order_by(Todo.due_date.is_(None), Todo.due_date, Todo.id)))


def _commitments(db: Session, user: User) -> dict[int, list[Todo]]:
    """到期或即将到期的待办（按门店分组）；没有日期的、月度目标类的不算承诺"""
    limit = today() + timedelta(days=DUE_SOON_DAYS)
    out: dict[int, list[Todo]] = {}
    for t in _open_todos(db, user):
        if t.due_date is not None and t.due_date <= limit and t.target_qty is None and t.source != "import":
            out.setdefault(t.store_id, []).append(t)
    return out


def _due_text(d: date) -> str:
    days = (d - today()).days
    return f"逾期 {-days} 天" if days < 0 else "今天" if days == 0 else "明天" if days == 1 else f"{d.month}月{d.day}日"


def _days_since(at) -> int | None:
    return None if at is None else (local_date(utcnow()) - local_date(at)).days


def _cand(row, reason: str, tags: list[str], last_days: int | None, checked: bool = False) -> dict:
    return {
        "storeId": str(row.id), "name": row.name, "district": row.district or "", "address": row.address or "",
        "cooperationStatus": row.cooperation_status, "reason": reason, "tags": tags,
        "lastVisitDays": last_days, "checked": checked,
    }


# ---------------------------------------------------------------- 销量缺口

SALES_BASE_MONTHS = 3      # 用前几个月的平均作为「平时水平」
SALES_MIN_BASELINE = 2     # 平时水平低于这个数的店不提醒（销量本来就很小，没有冲刺意义）
SALES_LOW_RATIO = 0.7      # 本期销量低于平时水平的 70% 算偏低
SALES_MIN_COVERAGE = 0.2   # 至少这么多比例的门店有该月数据，才认为该月的销量数据已经更新


def _num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def sales_gaps(db: Session, user: User) -> dict:
    """每家店「本期销量 vs 平时水平」。销量来自总门店清单里的按月销量（store_directory.monthly_sales）。

    「本期」取销量数据里最新的、且已有足够多门店填了数据的月份（不晚于当月）：
    数据没更新到当月时不会把所有店都当成 0 销量误报，而是拿最近一个有数据的月份对比。
    返回 {"refMonth": "2026-09" | None, "items": {store_id: {current, avg, gap, ratio}}}
    """
    rows = db.execute(
        select(Store.id, StoreDirectory.monthly_sales).join(StoreDirectory, Store.directory_id == StoreDirectory.id)
        .where(store_filter(team_ids(db, user)), StoreDirectory.monthly_sales.is_not(None))
    ).all()
    data = {sid: {m: n for m, v in (ms or {}).items() if (n := _num(v)) is not None} for sid, ms in rows if isinstance(ms, dict)}
    data = {sid: ms for sid, ms in data.items() if ms}
    if not data:
        return {"refMonth": None, "items": {}}
    this_month = f"{today().year}-{today().month:02d}"
    counts = Counter(m for ms in data.values() for m in ms if m <= this_month)
    need = max(3, int(len(data) * SALES_MIN_COVERAGE))
    ready = sorted(m for m, c in counts.items() if c >= need)
    if not ready:
        return {"refMonth": None, "items": {}}
    ref = ready[-1]
    items = {}
    for sid, ms in data.items():
        prior = [ms[m] for m in sorted(k for k in ms if k < ref)][-SALES_BASE_MONTHS:]
        if not prior:
            continue
        avg = sum(prior) / len(prior)
        cur = ms.get(ref, 0.0)
        items[sid] = {"current": cur, "avg": round(avg, 1), "gap": round(avg - cur, 1), "ratio": (cur / avg) if avg else 1.0}
    return {"refMonth": ref, "items": items}


def _low(sales: dict | None) -> bool:
    return bool(sales) and sales["avg"] >= SALES_MIN_BASELINE and sales["ratio"] < SALES_LOW_RATIO


def _gap_text(ref: str, sales: dict) -> str:
    return f"{int(ref[5:])}月销量 {sales['current']:g}，前几个月平均 {sales['avg']:g}，差 {sales['gap']:g}"


# ---------------------------------------------------------------- 候选门店

def districts(db: Session, user: User, limit: int = 8) -> list[dict]:
    """我能看到的门店按区县分组；最近 30 天我自己拜访得多的区排前面（「我常跑的区」）"""
    rows = _store_rows(db, user)
    last = _last_visits(db, user, [r.id for r in rows])
    since = utcnow() - timedelta(days=30)
    mine = {sid: n for sid, n in db.execute(
        select(Visit.store_id, func.count(Visit.id)).where(Visit.visitor_id == user.id, Visit.entered_at >= since).group_by(Visit.store_id))}
    groups: dict[str, dict] = {}
    for r in rows:
        if not r.district:
            continue
        g = groups.setdefault(r.district, {"district": r.district, "count": 0, "unvisited": 0, "stale": 0, "mine": 0})
        g["count"] += 1
        g["mine"] += mine.get(r.id, 0)
        d = _days_since(last.get(r.id))
        if d is None:
            g["unvisited"] += 1
        elif d > 30:
            g["stale"] += 1
    return sorted(groups.values(), key=lambda g: (-g["mine"], -g["count"]))[:limit]


def by_district(db: Session, user: User, district: str, limit: int = 15) -> list[dict]:
    rows = _store_rows(db, user, district)
    last = _last_visits(db, user, [r.id for r in rows])
    commits = _commitments(db, user)
    sales = sales_gaps(db, user)
    scored = []
    for r in rows:
        days = _days_since(last.get(r.id))
        reasons, tags, score = [], [], 0
        sg = sales["items"].get(r.id)
        if _low(sg):
            reasons.append(_gap_text(sales["refMonth"], sg))
            tags.append("销量偏低")
            score += 60 + min(sg["gap"], 60)
        for t in commits.get(r.id, []):
            reasons.append(f"约定：{t.topic}（{_due_text(t.due_date)}）")
            tags.append("有约定")
            score += 1000
        if days is None:
            reasons.append("从未拜访")
            tags.append("未拜访")
            score += 45
        elif days >= 14:
            reasons.append(f"{days} 天没去了")
            tags.append("久未拜访")
            score += min(days, 90)
        else:
            reasons.append(f"{days} 天前去过")
            score += 0
        if r.cooperation_status == "已合作":
            tags.append("已合作")
            score += 5
        scored.append((score, r, "；".join(reasons), tags, days))
    scored.sort(key=lambda x: -x[0])
    return [_cand(r, reason, tags, days) for _, r, reason, tags, days in scored[:limit]]


def recent(db: Session, user: User, days: int = 14, limit: int = 10) -> list[dict]:
    """最近拜访过的门店（团队可见范围），按拜访时间从近到远；提示上次留下的未完成事项"""
    ids = team_ids(db, user)
    rows = {r.id: r for r in _store_rows(db, user)}
    since = utcnow() - timedelta(days=days)
    last = {sid: at for sid, at in db.execute(
        select(Visit.store_id, func.max(Visit.entered_at)).where(Visit.visitor_id.in_(ids), Visit.entered_at >= since).group_by(Visit.store_id))}
    open_by_store: dict[int, list[Todo]] = {}
    for t in _open_todos(db, user):
        open_by_store.setdefault(t.store_id, []).append(t)
    out = []
    for sid, at in sorted(last.items(), key=lambda x: x[1], reverse=True):
        r = rows.get(sid)
        if r is None:
            continue
        d = _days_since(at)
        pending = [t for t in open_by_store.get(sid, []) if t.target_qty is None]
        reason = ("今天去过" if d == 0 else f"{d} 天前去过") + (f"；还有：{pending[0].topic}" if pending else "")
        out.append(_cand(r, reason, ["有未完成事项"] if pending else [], d))
        if len(out) >= limit:
            break
    return out


def commitment_stores(db: Session, user: User) -> list[dict]:
    """有到期 / 即将到期的承诺（例如「过两天带空罐来」）的门店"""
    commits = _commitments(db, user)
    if not commits:
        return []
    rows = {r.id: r for r in _store_rows(db, user)}
    last = _last_visits(db, user, list(commits))
    out = []
    for sid, todos in sorted(commits.items(), key=lambda kv: min(t.due_date for t in kv[1])):
        r = rows.get(sid)
        if r is None:
            continue
        reason = "；".join(f"{t.topic}（{_due_text(t.due_date)}）" for t in todos[:2])
        out.append(_cand(r, reason, ["有约定"], _days_since(last.get(sid)), checked=True))
    return out


def by_sales_gap(db: Session, user: User, district: str | None = None, limit: int = 15) -> dict:
    """本期销量低于平时水平的门店，按差额（冲刺潜力）从大到小"""
    sales = sales_gaps(db, user)
    if sales["refMonth"] is None:
        return {"title": "销量数据还没有可用的月份", "items": []}
    rows = {r.id: r for r in _store_rows(db, user, district)}
    last = _last_visits(db, user, list(rows))
    low = sorted(((sid, sg) for sid, sg in sales["items"].items() if sid in rows and _low(sg)), key=lambda x: -x[1]["gap"])
    items = []
    for sid, sg in low[:limit]:
        days = _days_since(last.get(sid))
        tail = "从未拜访" if days is None else f"{days} 天前去过"
        items.append(_cand(rows[sid], f"{_gap_text(sales['refMonth'], sg)}；{tail}", ["销量偏低"], days))
    where = f"{district}" if district else "全部片区"
    return {"title": f"{int(sales['refMonth'][5:])}月销量低于平时的门店（{where}，差额从大到小）", "items": items}


def suggest(db: Session, user: User, kind: str, district: str | None = None) -> dict:
    if kind == "district":
        if not district:
            raise ValueError("请指定区县")
        return {"title": f"{district}的门店（优先级从高到低）", "items": by_district(db, user, district)}
    if kind == "recent":
        return {"title": "最近两周拜访过的门店", "items": recent(db, user)}
    if kind == "commitments":
        return {"title": "有到期约定的门店", "items": commitment_stores(db, user)}
    if kind == "gap":
        return by_sales_gap(db, user, district)
    raise ValueError("不支持的类型")


# ---------------------------------------------------------------- 今日计划

def _serialize(db: Session, plans: list[VisitPlan]) -> list[dict]:
    out = []
    for p in plans:
        s = db.get(Store, p.store_id)
        dist = db.scalar(select(district_expr()).select_from(Store).outerjoin(StoreDirectory, Store.directory_id == StoreDirectory.id).where(Store.id == p.store_id))
        out.append({
            "id": str(p.id), "storeId": str(p.store_id), "name": s.name if s else "", "district": dist or "",
            "address": s.address if s else "", "reason": p.reason, "source": p.source, "status": p.status,
            "visitId": str(p.visit_id) if p.visit_id else "",
        })
    return out


def today_items(db: Session, user: User) -> list[dict]:
    plans = list(db.scalars(select(VisitPlan).where(VisitPlan.user_id == user.id, VisitPlan.plan_date == today()).order_by(VisitPlan.sort_order, VisitPlan.id)))
    return _serialize(db, plans)


def add(db: Session, user: User, store_ids: list[int], source: str, reasons: dict[int, str] | None = None) -> list[dict]:
    allowed = {r.id for r in _store_rows(db, user)}
    d = today()
    existing = {p.store_id for p in db.scalars(select(VisitPlan).where(VisitPlan.user_id == user.id, VisitPlan.plan_date == d))}
    order = db.scalar(select(func.coalesce(func.max(VisitPlan.sort_order), 0)).where(VisitPlan.user_id == user.id, VisitPlan.plan_date == d)) or 0
    for sid in dict.fromkeys(store_ids):
        if sid not in allowed or sid in existing:
            continue
        order += 1
        visited = db.scalar(select(Visit.id).where(Visit.visitor_id == user.id, Visit.store_id == sid).order_by(Visit.entered_at.desc()))
        v = db.get(Visit, visited) if visited else None
        done = v is not None and local_date(v.entered_at) == d
        db.add(VisitPlan(user_id=user.id, plan_date=d, store_id=sid, source=source, reason=(reasons or {}).get(sid, ""), sort_order=order,
                         status="visited" if done else "planned", visit_id=v.id if done else None))
    db.commit()
    return today_items(db, user)


def remove(db: Session, user: User, plan_id: int) -> bool:
    p = db.get(VisitPlan, plan_id)
    if p is None or p.user_id != user.id:
        return False
    db.delete(p)
    db.commit()
    return True


def mark_visited(db: Session, visit: Visit) -> None:
    """拜访创建时调用：把当天该销售、该门店的计划标为已去"""
    if visit.visitor_id is None:
        return
    p = db.scalars(select(VisitPlan).where(VisitPlan.user_id == visit.visitor_id, VisitPlan.store_id == visit.store_id,
                                           VisitPlan.plan_date == local_date(visit.entered_at))).first()
    if p is not None and p.status != "visited":
        p.status, p.visit_id = "visited", visit.id


# ---------------------------------------------------------------- 新对话的主动问候

def greeting(db: Session, user: User) -> dict:
    plan = today_items(db, user)
    commits = commitment_stores(db, user)
    dist = districts(db, user, limit=4)
    district_opts = [{"label": d["district"], "kind": "district", "district": d["district"]} for d in dist]
    sales = sales_gaps(db, user)
    tail = ([{"label": "销量低于平时的店", "kind": "gap"}] if any(_low(sg) for sg in sales["items"].values()) else [])
    tail += [{"label": "最近拜访过的店", "kind": "recent"}, {"label": "我承诺过的事", "kind": "commitments"}]
    if plan:
        done = sum(1 for p in plan if p["status"] == "visited")
        if done == len(plan):
            text = f"今天计划的 {len(plan)} 家店都去完了。还想再去哪里吗？"
        else:
            text = f"今天计划去 {len(plan)} 家店，已完成 {done} 家。"
        return {"mode": "plan", "text": text, "plan": plan, "commitments": commits, "options": district_opts + tail if done == len(plan) else tail}
    if commits:
        names = "、".join(c["name"] for c in commits[:3])
        text = f"你有 {len(commits)} 家店的约定到期了：{names}{'等' if len(commits) > 3 else ''}。要把它们安排进今天吗？也可以告诉我今天想去哪个区。"
        return {"mode": "commitments", "text": text, "plan": [], "commitments": commits,
                "options": [{"label": "把到期约定的店安排进今天", "kind": "commitments"}] + district_opts + [o for o in tail if o["kind"] != "commitments"]}
    return {"mode": "ask", "text": "今天准备去哪个区的门店？也可以从销量偏低的店、最近拜访过的店、承诺过的事里挑。", "plan": [], "commitments": [], "options": district_opts + tail}
