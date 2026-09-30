"""首页「今日待办」大卡片与进店前简报（纯规则，与小程序 services/assistant.js 一致）"""

from sqlalchemy.orm import Session

from ..models import Store, User
from . import plans
from .constants import ANALYZED
from .playbook import GENERAL_QUESTIONS, OBJECTION_FOCUS, OBJECTIONS, PROFILE_QUESTIONS
from .summary import list_todos, list_visits, summarize_store
from .timeutil import fmt_date, fmt_duration

def today_panel(db: Session, user: User, store_id: str | None) -> dict:
    """「今日待办」= 今日计划（要去的店）+ 到期的约定 + 等你确认费用的录音。
    进店前简报、久未拜访之类的建议不放在这里，由新对话的主动问候和对话里的选项提供"""
    plan = plans.today_items(db, user)
    todos = list_todos(db, user)
    visits = list_visits(db, user)
    sections = []

    rows = []
    for p in plan:
        visited = p["status"] == "visited"
        rows.append({
            "key": f"plan-{p['id']}", "badge": "已去" if visited else "待去", "tone": "success" if visited else "info",
            "text": p["name"], "sub": p["reason"] or p["district"] or p["address"],
            "btn": "看拜访" if visited else "看简报", "action": "openVisit" if visited else "openBrief",
            "id": p["visitId"] if visited else p["storeId"],
            **({} if visited else {"subBtn": "到店录音", "subAction": "recordStore"}),
        })
    if rows:
        sections.append({"title": "今日计划", "rows": rows})

    due = [t for t in todos if not t["done"] and t["source"] != "import" and t["targetQty"] is None
           and t["dueAt"] is not None and t["due"]["days"] <= plans.DUE_SOON_DAYS]
    follow = [{
        "key": f"todo-{t['id']}", "badge": t["due"]["text"], "tone": t["due"]["tone"],
        "text": f"{t['storeName']} · {t['topic']}", "sub": t["action"] or t["reason"],
        "btn": "完成", "action": "todoDone", "id": t["id"], "subBtn": "复制微信话术", "subAction": "copyMsg",
    } for t in due]
    if follow:
        sections.append({"title": "到期的约定和跟进", "rows": follow})

    rec = [{
        "key": f"cost-{v['id']}", "badge": "待确认", "tone": "warn",
        "text": f"{v['storeName']} · 录音 {fmt_duration(v['durationSec'])}", "sub": f"预估识别费 ¥{v['estCost']}，确认后自动分析",
        "btn": "去确认", "action": "openVisit", "id": v["id"],
    } for v in visits if v["status"] == "cost_pending" and not v["legacy"]]  # 飞书导入的历史录音文件还没迁移，不能确认识别
    if rec:
        sections.append({"title": "待处理录音", "rows": rec})

    pending = sum(1 for p in plan if p["status"] == "planned")
    count = pending + len(follow) + len(rec)
    overdue = sum(1 for t in due if t["due"]["days"] < 0)
    parts = []
    if plan:
        parts.append(f"计划 {len(plan)} 家，已去 {len(plan) - pending} 家")
    if follow:
        parts.append(f"{len(follow)} 件约定" + (f"（{overdue} 件逾期）" if overdue else ""))
    if rec:
        parts.append(f"{len(rec)} 条录音待确认")
    return {
        "count": count,
        "hint": "，".join(parts) if parts else "今天还没有计划，新建对话告诉我要去哪",
        "stat": f"{count} 件待办" if count else "暂无待办",
        "steps": None,
        "sections": sections,
        "suggestions": ["客户说网上更便宜怎么回？", follow[0]["text"].split(" · ")[0] + "上次卡在哪？" if follow else "下一步该做什么？", "这家店最关心什么？"],
    }


def brief(db: Session, user: User, store: Store) -> dict:
    visits = list_visits(db, user, store_id=store.id)
    s = summarize_store(db, store, visits, detail=True)
    s["visits"] = visits
    todos = list_todos(db, user)
    done_any = next((v for v in visits if v["status"] in ANALYZED), None)  # 含从飞书导入的历史拜访
    last = next((v for v in visits if v["status"] in ANALYZED and not v["legacy"]), None)  # 有结构化分析的最近一次
    out = {
        "store": s,
        "isFirst": done_any is None,
        "lastText": f"{fmt_date(done_any['createdAt'])} · {done_any['stage']}" if done_any else "",
        "oneLine": s["oneLine"],
        "concerns": [c["name"] for c in last["analysis"]["concerns"] if c.get("state") == "是"] if last else [],
        "openTodos": [t for t in todos if t["storeId"] == s["id"] and not t["done"]],
        "goals": [], "questions": [], "objections": [],
    }
    if done_any is None:
        out["goals"] = ["了解门店基本情况和主营品牌", "找到老板最在意的 1–2 个问题", "约好下一次见面的时间"]
        out["questions"] = list(GENERAL_QUESTIONS)
        out["opening"] = "老板您好，我是 A2 奶粉的业务，今天想花十分钟了解下店里奶粉卖得怎么样，看看有没有能帮上忙的。"
        return out
    out["goals"] = [f"{a.get('topic', '')}：{a.get('acceptance', '')}" for a in last["analysis"]["nextAction"].get("actions", [])] if last else []
    if not out["goals"]:
        out["goals"] = ["回顾上次沟通，确认待办进展", "补全档案里还没摸清的情况", "约好下一次见面的时间"]
    sections = s["profile"]["sections"] if s["profile"] else []
    out["questions"] = [f"{PROFILE_QUESTIONS[x['key']]}（补全「{x['label']}」）" for x in sections if x["state"] == "未确认" and x["key"] in PROFILE_QUESTIONS]
    out["questions"] += GENERAL_QUESTIONS[: max(0, 2 - len(out["questions"]))]
    ordered = OBJECTION_FOCUS + [k for k in OBJECTIONS if k not in OBJECTION_FOCUS]
    out["objections"] = [{"name": c, **OBJECTIONS[c]} for c in ordered if c in out["concerns"]][:2]
    return out
