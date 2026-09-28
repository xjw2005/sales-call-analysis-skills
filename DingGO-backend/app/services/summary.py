"""把数据库记录组装成小程序需要的数据结构（与小程序 model/ 演示数据的字段一致）"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Store, StoreCorrection, StoreProfileSection, Todo, User, Visit, VisitAnalysis, VisitSegment, VisitTranscript
from . import storage
from .constants import ANALYZED, PROFILE_SECTIONS, modules_for, visit_mode
from .timeutil import end_of_day_ms, fmt_date, to_ms, today

# 分析模块 → 小程序 analysis 里的字段
MODULE_FIELDS = {
    "explicit-needs": "explicitNeeds",
    "implicit-needs": "implicitNeeds",
    "concerns": "concerns",
    "effectiveness": "effectiveness",
    "quotes": "quotes",
    "store-profile": "profile",
}


def assemble_analysis(rows: list[VisitAnalysis]) -> dict:
    by_module = {r.module: r.result for r in rows}
    analysis = {
        "explicitNeeds": [], "implicitNeeds": [], "concerns": [], "effectiveness": None,
        "quotes": [], "profile": {"sections": []}, "nextAction": {"judgement": "", "reason": "", "confirmed": [], "actions": [], "revisitValue": ""},
    }
    for module, field in MODULE_FIELDS.items():
        if by_module.get(module) is not None:
            analysis[field] = by_module[module]
    na = by_module.get("next-action")
    if na:
        analysis["nextAction"] = na.get("nextAction", analysis["nextAction"])
        if na.get("loop") is not None:
            analysis["loop"] = na["loop"]
    return analysis


def serialize_visit(db: Session, v: Visit, store_name: str, detail: bool = False) -> dict:
    modules = modules_for(v.stage)
    rows = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id == v.id)).all()
    written = sum(1 for r in rows if r.status == "written" and r.module in modules)
    out = {
        "id": str(v.id),
        "storeId": str(v.store_id),
        "storeName": store_name,
        "stage": v.stage,
        "cooperated": v.cooperated,
        "note": v.note,
        "createdAt": to_ms(v.created_at),
        "durationSec": v.duration_sec,
        "estCost": round(v.est_cost, 2),
        "status": v.status,
        "mode": visit_mode(v.stage),
        "moduleKeys": modules,
        "moduleDone": len(modules) if v.status in ANALYZED else written,
        "reviewed": v.reviewed_at is not None,
    }
    if v.status in ANALYZED:
        out["analysis"] = assemble_analysis(rows)
    if detail:
        segs = db.scalars(select(VisitSegment).where(VisitSegment.visit_id == v.id).order_by(VisitSegment.seq)).all()
        out["audioUrls"] = [storage.url_of(s.object_key) for s in segs]
        out["audioUrl"] = out["audioUrls"][0] if segs else ""
        tr = db.get(VisitTranscript, v.id)
        out["transcript"] = tr.utterances if tr else []
    return out


def list_visits(db: Session, user: User, store_id: int | None = None, detail: bool = False) -> list[dict]:
    q = select(Visit, Store.name).join(Store, Store.id == Visit.store_id).where(Visit.user_id == user.id)
    if store_id is not None:
        q = q.where(Visit.store_id == store_id)
    q = q.order_by(Visit.created_at.desc(), Visit.id.desc())
    return [serialize_visit(db, v, name, detail) for v, name in db.execute(q).all()]


def profile_sections(db: Session, store_id: int) -> list[dict]:
    rows = {r.key: r for r in db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == store_id))}
    if not rows:
        return []
    out = []
    for key, label in PROFILE_SECTIONS:
        r = rows.get(key)
        out.append({"key": key, "label": label, "state": r.state if r else "未确认", "content": r.content if r else "未确认"})
    return out


def summarize_store(db: Session, store: Store, visits: list[dict]) -> dict:
    """门店摘要：档案、三个仪表（合作进展/关心点/行动闭环）、当前建议行动、历次闭环"""
    done = [v for v in visits if v["status"] in ANALYZED]
    latest = done[0] if done else None
    latest_first = next((v for v in done if v["analysis"].get("effectiveness")), None)
    latest_loop = next((v for v in done if v["analysis"].get("loop")), None)
    loop_items = latest_loop["analysis"]["loop"].get("items", []) if latest_loop else []
    sections = profile_sections(db, store.id)
    correction = db.scalars(
        select(StoreCorrection).where(StoreCorrection.store_id == store.id).order_by(StoreCorrection.id.desc())
    ).first()
    return {
        "id": str(store.id),
        "name": store.name,
        "province": store.province,
        "city": store.city,
        "address": store.address,
        "cooperated": store.cooperated,
        "createdAt": to_ms(store.created_at),
        "correction": correction.text if correction else "",
        "visitCount": len(visits),
        "lastVisitAt": visits[0]["createdAt"] if visits else None,
        "profile": {"sections": sections, "sourceCount": len(done)} if sections else None,
        "oneLine": next((s["content"] for s in sections if s["key"] == "one_line"), ""),
        "metrics": {
            "score": latest_first["analysis"]["effectiveness"]["total"] if latest_first else None,
            "concernHits": sum(1 for c in latest["analysis"]["concerns"] if c.get("state") == "是") if latest else None,
            "loopRate": round(sum(1 for i in loop_items if i.get("status") == "已完成") / len(loop_items) * 100) if loop_items else None,
        },
        "actions": latest["analysis"]["nextAction"].get("actions", []) if latest else [],
        "loops": [
            {"visitId": v["id"], "createdAt": v["createdAt"], "items": v["analysis"]["loop"].get("items", [])}
            for v in done if v["analysis"].get("loop")
        ],
    }


def due_info(due) -> dict:
    days = (due - today()).days
    if days < 0:
        return {"text": f"逾期 {-days} 天", "tone": "error", "days": days}
    if days == 0:
        return {"text": "今天到期", "tone": "warn", "days": days}
    if days == 1:
        return {"text": "明天到期", "tone": "info", "days": days}
    return {"text": f"{due.month}月{due.day}日", "tone": "muted", "days": days}


def serialize_todo(t: Todo, store_name: str) -> dict:
    due_ms = end_of_day_ms(t.due_date)
    return {
        "id": str(t.id), "visitId": str(t.visit_id), "storeId": str(t.store_id), "storeName": store_name,
        "topic": t.topic, "action": t.action, "owner": t.owner, "timeframe": t.timeframe,
        "reason": t.reason, "acceptance": t.acceptance,
        "dueAt": due_ms, "due": due_info(t.due_date), "dueDate": fmt_date(due_ms).split(" ")[0],
        "done": t.done_at is not None,
    }


def list_todos(db: Session, user: User) -> list[dict]:
    q = (
        select(Todo, Store.name).join(Store, Store.id == Todo.store_id)
        .where(Todo.user_id == user.id).order_by(Todo.due_date, Todo.id)
    )
    return [serialize_todo(t, name) for t, name in db.execute(q).all()]

