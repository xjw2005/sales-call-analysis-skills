"""写入分析结果：以后 AI 流水线跑完直接调用；现在由导入脚本 / 管理接口调用"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Store, StoreProfileSection, Todo, Visit, VisitAnalysis, VisitTranscript, utcnow
from .constants import PROFILE_LABELS
from .due_date import due_or_default
from .timeutil import local_date

FIELD_MODULES = {
    "explicitNeeds": "explicit-needs",
    "implicitNeeds": "implicit-needs",
    "concerns": "concerns",
    "effectiveness": "effectiveness",
    "quotes": "quotes",
    "profile": "store-profile",
}


def upsert_module(db: Session, visit_id: int, module: str, result, evidence=None, model: str = "", prompt_version: str = "") -> VisitAnalysis:
    row = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id == visit_id, VisitAnalysis.module == module)).first()
    if row is None:
        row = VisitAnalysis(visit_id=visit_id, module=module)
        db.add(row)
    row.result = result
    row.evidence = evidence
    row.status = "written"
    if model:
        row.model = model
    if prompt_version:
        row.prompt_version = prompt_version
    return row


def upsert_transcript(db: Session, visit_id: int, utterances: list, raw_text: str | None = None, source: str = "asr") -> None:
    tr = db.get(VisitTranscript, visit_id)
    if tr is None:
        db.add(VisitTranscript(visit_id=visit_id, utterances=utterances, raw_text=raw_text, source=source))
    else:
        tr.utterances, tr.raw_text, tr.source = utterances, raw_text, source


def upsert_profile_section(db: Session, store_id: int, key: str, state: str, content: str,
                           evidence: str | None = None, source_visit_id: int | None = None, updated_by: int | None = None) -> StoreProfileSection:
    row = db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == store_id, StoreProfileSection.key == key)).first()
    if row is None:
        row = StoreProfileSection(store_id=store_id, key=key)
        db.add(row)
    row.state, row.content, row.evidence = state, content, evidence
    row.source_visit_id, row.updated_by = source_visit_id, updated_by
    return row


def apply_analysis(db: Session, visit: Visit, analysis: dict, transcript: list | None = None) -> None:
    """analysis 结构与小程序 model/analysis.js 一致；写入后拜访状态变为 done"""
    for field, module in FIELD_MODULES.items():
        if analysis.get(field) is not None:
            upsert_module(db, visit.id, module, analysis[field])
    if analysis.get("nextAction") is not None:
        upsert_module(db, visit.id, "next-action", {"nextAction": analysis["nextAction"], "loop": analysis.get("loop")})
    if transcript is not None:
        upsert_transcript(db, visit.id, transcript)

    # 门店档案：用本次结果覆盖七维度（与原流水线同步 01 主档的规则一致）
    for s in (analysis.get("profile") or {}).get("sections", []):
        if s.get("key") in PROFILE_LABELS:
            upsert_profile_section(db, visit.store_id, s["key"], s.get("state", "未确认"), s.get("content", ""),
                                   evidence=s.get("evidence") or None, source_visit_id=visit.id)

    # 待办：本次拜访还没做的 AI 待办重建；已完成的保留
    db.execute(delete(Todo).where(Todo.visit_id == visit.id, Todo.source == "ai", Todo.status == "open"))
    base = local_date(visit.entered_at)
    store = db.get(Store, visit.store_id)
    # 录音里已确认的约定（「过两天带空罐来」「老板不在，周五再来」）：销售、客户、双方的都记成待办，到期就会提醒
    for a in (analysis.get("nextAction") or {}).get("confirmedActions", []):
        db.add(Todo(
            source="ai", visit_id=visit.id, store_id=visit.store_id,
            assignee_id=visit.visitor_id or (store.primary_sales_id if store else None),
            topic=a.get("action", "")[:60], action=a.get("action", ""), owner=a.get("owner", "销售"),
            timeframe=a.get("timeframe", ""), reason="录音中已确认的约定",
            due_date=due_or_default(a.get("timeframe", ""), base, None),
        ))
    for a in (analysis.get("nextAction") or {}).get("actions", []):
        db.add(Todo(
            source="ai", visit_id=visit.id, store_id=visit.store_id,
            assignee_id=visit.visitor_id or (store.primary_sales_id if store else None),
            topic=a.get("topic", "")[:128], action=a.get("action", ""), owner=a.get("owner", "销售"),
            timeframe=a.get("timeframe", ""), reason=a.get("reason", ""), acceptance=a.get("acceptance", ""),
            due_date=due_or_default(a.get("timeframe", ""), base, a.get("dueDays")),
        ))

    if visit.cooperated == "是" and store:
        store.cooperation_status = "已合作"
        store.updated_at = utcnow()
    visit.status = "done"
    visit.legacy = False
    db.commit()
