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


def _upsert_module(db: Session, visit_id: int, module: str, result) -> None:
    row = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id == visit_id, VisitAnalysis.module == module)).first()
    if row is None:
        row = VisitAnalysis(visit_id=visit_id, module=module)
        db.add(row)
    row.result = result
    row.status = "written"


def apply_analysis(db: Session, visit: Visit, analysis: dict, transcript: list | None = None) -> None:
    """analysis 结构与小程序 model/analysis.js 一致；写入后拜访状态变为 done"""
    for field, module in FIELD_MODULES.items():
        if field in analysis and analysis[field] is not None:
            _upsert_module(db, visit.id, module, analysis[field])
    if analysis.get("nextAction") is not None:
        _upsert_module(db, visit.id, "next-action", {"nextAction": analysis["nextAction"], "loop": analysis.get("loop")})

    if transcript is not None:
        tr = db.get(VisitTranscript, visit.id)
        if tr is None:
            db.add(VisitTranscript(visit_id=visit.id, utterances=transcript))
        else:
            tr.utterances = transcript

    # 门店档案：用本次结果覆盖七维度（与原流水线同步 01 主档的规则一致）
    for s in (analysis.get("profile") or {}).get("sections", []):
        if s.get("key") not in PROFILE_LABELS:
            continue
        row = db.scalars(
            select(StoreProfileSection).where(StoreProfileSection.store_id == visit.store_id, StoreProfileSection.key == s["key"])
        ).first()
        if row is None:
            row = StoreProfileSection(store_id=visit.store_id, key=s["key"])
            db.add(row)
        row.state = s.get("state", "未确认")
        row.content = s.get("content", "")
        row.source_visit_id = visit.id

    # 待办：本次拜访未完成的旧待办重建；已完成的保留
    db.execute(delete(Todo).where(Todo.visit_id == visit.id, Todo.done_at.is_(None)))
    base = local_date(visit.created_at)
    for a in (analysis.get("nextAction") or {}).get("actions", []):
        db.add(Todo(
            visit_id=visit.id, store_id=visit.store_id, user_id=visit.user_id,
            topic=a.get("topic", "")[:128], action=a.get("action", ""), owner=a.get("owner", "销售"),
            timeframe=a.get("timeframe", ""), reason=a.get("reason", ""), acceptance=a.get("acceptance", ""),
            due_date=due_or_default(a.get("timeframe", ""), base, a.get("dueDays")),
        ))

    if visit.cooperated == "是":
        store = db.get(Store, visit.store_id)
        store.cooperated = True
        store.updated_at = utcnow()
    visit.status = "done"
    db.commit()
