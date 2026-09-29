"""把数据库记录组装成小程序需要的数据结构（字段与小程序 model/ 演示数据一致）。

列表接口一律批量取数（不逐条查库），并且不带分析里体积大的部分；详情接口才带完整内容。
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Store, StoreCorrection, StoreProfileSection, Todo, User, Visit, VisitAnalysis, VisitSegment, VisitTranscript,
)
from . import storage
from .access import store_filter, team_ids, visit_filter
from .constants import ANALYZED, PROCESSING, PROFILE_SECTIONS, modules_for, visit_mode
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
MODULE_LABELS = {
    "ai-summary": "AI 拜访摘要",
    "explicit-needs": "显性需求",
    "implicit-needs": "隐性需求",
    "concerns": "客户关心点",
    "effectiveness": "合作进展评分",
    "quotes": "销售金句",
    "store-profile": "门店档案",
    "next-action": "下一步行动策略",
    "loop": "上次行动闭环",
}
# 列表里只带这几个模块（够卡片、仪表和今日待办用）；详情才带全部
LIGHT_MODULES = ("concerns", "effectiveness", "store-profile", "next-action")
CHUNK = 500


def _chunks(items: list, n: int = CHUNK):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _effective(row: VisitAnalysis):
    """展示用的结果：销售改过的优先"""
    return row.corrected_result if row.corrected_result is not None else row.result


def empty_analysis() -> dict:
    return {
        "explicitNeeds": [], "implicitNeeds": [], "concerns": [], "effectiveness": None, "quotes": [],
        "profile": {"sections": []},
        "nextAction": {"judgement": "", "reason": "", "confirmed": [], "actions": [], "revisitValue": ""},
    }


def assemble_analysis(rows: list[VisitAnalysis], legacy: bool = False) -> dict:
    by_module = {r.module: r for r in rows}
    analysis = empty_analysis()
    if legacy:
        # 从飞书导入的分析是排好版的文字，原文展示；结构化字段留空
        order = ["ai-summary", "explicit-needs", "implicit-needs", "concerns", "effectiveness", "quotes", "store-profile", "next-action", "loop"]
        analysis["legacy"] = True
        analysis["legacySections"] = [
            {"module": m, "title": MODULE_LABELS[m], "text": (_effective(by_module[m]) or {}).get("text", ""), "evidence": by_module[m].evidence or ""}
            for m in order if m in by_module
        ]
        return analysis
    for module, field in MODULE_FIELDS.items():
        if module in by_module and _effective(by_module[module]) is not None:
            analysis[field] = _effective(by_module[module])
    na = by_module.get("next-action")
    if na and _effective(na):
        analysis["nextAction"] = _effective(na).get("nextAction", analysis["nextAction"])
        if _effective(na).get("loop") is not None:
            analysis["loop"] = _effective(na)["loop"]
    return analysis


def _load_analysis(db: Session, visits: list[Visit], full: bool) -> dict[int, list[VisitAnalysis]]:
    ids = [v.id for v in visits if v.status in ANALYZED and (full or not v.legacy)]
    out: dict[int, list[VisitAnalysis]] = {}
    for chunk in _chunks(ids):
        q = select(VisitAnalysis).where(VisitAnalysis.visit_id.in_(chunk))
        if not full:
            q = q.where(VisitAnalysis.module.in_(LIGHT_MODULES))
        for row in db.scalars(q):
            out.setdefault(row.visit_id, []).append(row)
    return out


def _written_counts(db: Session, visits: list[Visit]) -> dict[int, int]:
    ids = [v.id for v in visits if v.status in PROCESSING]
    out: dict[int, int] = {}
    for chunk in _chunks(ids):
        q = (
            select(VisitAnalysis.visit_id, func.count(VisitAnalysis.id))
            .where(VisitAnalysis.visit_id.in_(chunk), VisitAnalysis.status == "written")
            .group_by(VisitAnalysis.visit_id)
        )
        out.update({vid: n for vid, n in db.execute(q).all()})
    return out


def _names(db: Session, model, ids: set[int]) -> dict[int, str]:
    ids = {i for i in ids if i}
    out: dict[int, str] = {}
    for chunk in _chunks(sorted(ids)):
        out.update({i: n for i, n in db.execute(select(model.id, model.name).where(model.id.in_(chunk))).all()})
    return out


def serialize_visits(db: Session, visits: list[Visit], full: bool = False) -> list[dict]:
    if not visits:
        return []
    store_names = _names(db, Store, {v.store_id for v in visits})
    user_names = _names(db, User, {v.visitor_id for v in visits})
    analysis_rows = _load_analysis(db, visits, full)
    written = _written_counts(db, visits)
    out = []
    for v in visits:
        modules = modules_for(v.stage)
        item = {
            "id": str(v.id),
            "code": v.code or "",
            "storeId": str(v.store_id),
            "storeName": store_names.get(v.store_id, ""),
            "visitorId": str(v.visitor_id) if v.visitor_id else "",
            "visitorName": user_names.get(v.visitor_id, ""),
            "stage": v.stage,
            "businessLine": v.business_line or "",
            "cooperated": v.cooperated,
            "purposes": v.purposes or [],
            "note": v.note,
            "storeCondition": v.store_condition or "",
            "createdAt": to_ms(v.entered_at),
            "leftAt": to_ms(v.left_at),
            "checkin": {"address": v.checkin_address, "lat": v.checkin_lat, "lng": v.checkin_lng},
            "survey": v.survey or {},
            "recordingMode": v.recording_mode,
            "noRecordingReason": v.no_recording_reason,
            "durationSec": v.duration_sec,
            "estCost": round(v.est_cost or 0, 2),
            "status": v.status,
            "mode": visit_mode(v.stage),
            "moduleKeys": modules,
            "moduleDone": len(modules) if v.status in ANALYZED else written.get(v.id, 0),
            "reviewed": v.reviewed_at is not None,
            "legacy": bool(v.legacy),
        }
        if v.status in ANALYZED and (full or not v.legacy):
            item["analysis"] = assemble_analysis(analysis_rows.get(v.id, []), legacy=bool(v.legacy))
        elif v.status in ANALYZED and v.legacy:
            item["analysis"] = empty_analysis() | {"legacy": True, "legacySections": []}
        out.append(item)
    if full:
        for item in out:
            vid = int(item["id"])
            segs = db.scalars(select(VisitSegment).where(VisitSegment.visit_id == vid).order_by(VisitSegment.seq)).all()
            playable = [s for s in segs if not s.file_missing]
            item["audioUrls"] = [storage.url_of(s.object_key) for s in playable]
            item["audioUrl"] = item["audioUrls"][0] if playable else ""
            item["audioMissing"] = len(segs) - len(playable)
            tr = db.get(VisitTranscript, vid)
            item["transcript"] = tr.utterances if tr else []
            item["transcriptRaw"] = tr.raw_text if tr and not tr.utterances else ""
    return out


def serialize_visit(db: Session, v: Visit, full: bool = False) -> dict:
    return serialize_visits(db, [v], full=full)[0]


def list_visits(db: Session, user: User, store_id: int | None = None, scope: str = "mine", full: bool = False) -> list[dict]:
    """scope：mine 自己和下属拜访的；visible 还包括别人去过我名下门店的。指定 store_id 时按 visible"""
    ids = team_ids(db, user)
    q = select(Visit)
    if store_id is not None:
        q = q.where(Visit.store_id == store_id, visit_filter(ids))
    elif scope == "visible":
        q = q.where(visit_filter(ids))
    else:
        q = q.where(Visit.visitor_id.in_(ids))
    q = q.order_by(Visit.entered_at.desc(), Visit.id.desc())
    return serialize_visits(db, list(db.scalars(q)), full=full)


def profile_sections(rows: list[StoreProfileSection], with_evidence: bool = False) -> list[dict]:
    if not rows:
        return []
    by_key = {r.key: r for r in rows}
    out = []
    for key, label in PROFILE_SECTIONS:
        r = by_key.get(key)
        item = {"key": key, "label": label, "state": r.state if r else "未确认", "content": r.content if r else "未确认"}
        if with_evidence:
            item["evidence"] = (r.evidence if r else None) or ""
        out.append(item)
    return out


def summarize_stores(db: Session, stores: list[Store], visits_by_store: dict[str, list[dict]], detail: bool = False) -> list[dict]:
    """门店摘要：档案、三个仪表（合作进展/关心点/行动闭环）、当前建议行动、历次闭环。
    detail=True 时带联系人和电话、档案原文证据"""
    ids = [s.id for s in stores]
    sections: dict[int, list[StoreProfileSection]] = {}
    corrections: dict[int, str] = {}
    for chunk in _chunks(ids):
        for r in db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id.in_(chunk))):
            sections.setdefault(r.store_id, []).append(r)
        for r in db.scalars(select(StoreCorrection).where(StoreCorrection.store_id.in_(chunk)).order_by(StoreCorrection.id)):
            corrections[r.store_id] = r.text
    sales_names = _names(db, User, {s.primary_sales_id for s in stores})

    out = []
    for store in stores:
        visits = visits_by_store.get(str(store.id), [])
        done = [v for v in visits if v["status"] in ANALYZED]
        structured = [v for v in done if not v["legacy"]]
        latest = structured[0] if structured else None
        latest_first = next((v for v in structured if v["analysis"].get("effectiveness")), None)
        latest_loop = next((v for v in structured if v["analysis"].get("loop")), None)
        loop_items = latest_loop["analysis"]["loop"].get("items", []) if latest_loop else []
        secs = profile_sections(sections.get(store.id, []), with_evidence=detail)
        item = {
            "id": str(store.id),
            "code": store.code or "",
            "name": store.name,
            "platform": store.platform or "",
            "externalId": store.external_id or "",
            "storeType": store.store_type,
            "cooperationStatus": store.cooperation_status,
            "cooperated": store.cooperation_status == "已合作",
            "primarySalesId": str(store.primary_sales_id) if store.primary_sales_id else "",
            "primarySalesName": sales_names.get(store.primary_sales_id, ""),
            "province": store.province,
            "city": store.city,
            "district": store.district,
            "address": store.address,
            "grid": store.grid,
            "installedAt": store.installed_at.isoformat() if store.installed_at else "",
            "wecomAdded": store.wecom_added,
            "createdAt": to_ms(store.created_at),
            "correction": corrections.get(store.id, ""),
            "visitCount": len(visits),
            "lastVisitAt": visits[0]["createdAt"] if visits else None,
            "profile": {"sections": secs, "sourceCount": len(done)} if secs else None,
            "oneLine": next((s["content"] for s in secs if s["key"] == "one_line"), ""),
            "metrics": {
                "score": latest_first["analysis"]["effectiveness"]["total"] if latest_first else None,
                "concernHits": sum(1 for c in latest["analysis"]["concerns"] if c.get("state") == "是") if latest else None,
                "loopRate": round(sum(1 for i in loop_items if i.get("status") == "已完成") / len(loop_items) * 100) if loop_items else None,
            },
            "actions": latest["analysis"]["nextAction"].get("actions", []) if latest else [],
            "loops": [
                {"visitId": v["id"], "createdAt": v["createdAt"], "items": v["analysis"]["loop"].get("items", [])}
                for v in structured if v["analysis"].get("loop")
            ],
        }
        if detail:
            item["contactName"] = store.contact_name
            item["contactPhone"] = store.contact_phone
        out.append(item)
    return out


def summarize_store(db: Session, store: Store, visits: list[dict], detail: bool = False) -> dict:
    return summarize_stores(db, [store], {str(store.id): visits}, detail=detail)[0]


def due_info(due: date | None) -> dict:
    if due is None:
        return {"text": "未定日期", "tone": "muted", "days": 9999}
    days = (due - today()).days
    if days < 0:
        return {"text": f"逾期 {-days} 天", "tone": "error", "days": days}
    if days == 0:
        return {"text": "今天到期", "tone": "warn", "days": days}
    if days == 1:
        return {"text": "明天到期", "tone": "info", "days": days}
    return {"text": f"{due.month}月{due.day}日", "tone": "muted", "days": days}


def serialize_todos(db: Session, todos: list[Todo]) -> list[dict]:
    store_names = _names(db, Store, {t.store_id for t in todos})
    user_names = _names(db, User, {t.assignee_id for t in todos})
    out = []
    for t in todos:
        due_ms = end_of_day_ms(t.due_date) if t.due_date else None
        gap = round(t.target_qty - t.achieved_qty, 2) if t.target_qty is not None and t.achieved_qty is not None else None
        out.append({
            "id": str(t.id), "source": t.source,
            "visitId": str(t.visit_id) if t.visit_id else "", "storeId": str(t.store_id) if t.store_id else "",
            "storeName": store_names.get(t.store_id, ""),
            "assigneeId": str(t.assignee_id) if t.assignee_id else "", "assigneeName": user_names.get(t.assignee_id, ""),
            "topic": t.topic, "action": t.action, "owner": t.owner, "timeframe": t.timeframe,
            "reason": t.reason, "acceptance": t.acceptance,
            "dueAt": due_ms, "due": due_info(t.due_date), "dueDate": fmt_date(due_ms).split(" ")[0] if due_ms else "",
            "status": t.status, "done": t.status == "done",
            "period": t.period or "", "targetQty": t.target_qty, "achievedQty": t.achieved_qty, "gapQty": gap,
            "unit": t.unit, "progressNote": t.progress_note,
        })
    return out


def serialize_todo(db: Session, t: Todo) -> dict:
    return serialize_todos(db, [t])[0]


def list_todos(db: Session, user: User, scope: str = "mine") -> list[dict]:
    """scope：mine 我的（执行人是我，或没有执行人但门店是我的）；team 包含下属的"""
    ids = team_ids(db, user) if scope == "team" else [user.id]
    own_stores = select(Store.id).where(Store.primary_sales_id.in_(ids))
    q = (
        select(Todo)
        .where((Todo.assignee_id.in_(ids)) | (Todo.assignee_id.is_(None) & Todo.store_id.in_(own_stores)))
        .where(Todo.status != "cancelled")
        .order_by(Todo.due_date.is_(None), Todo.due_date, Todo.id)
    )
    return serialize_todos(db, list(db.scalars(q)))


def list_stores(db: Session, user: User) -> list[dict]:
    """我能看到的门店（不含联系人电话），最近有拜访的排前面"""
    ids = team_ids(db, user)
    stores = list(db.scalars(select(Store).where(store_filter(ids))))
    by_store: dict[str, list[dict]] = {}
    for v in list_visits(db, user, scope="visible"):
        by_store.setdefault(v["storeId"], []).append(v)
    out = summarize_stores(db, stores, by_store)
    return sorted(out, key=lambda s: s["lastVisitAt"] or s["createdAt"] or 0, reverse=True)
