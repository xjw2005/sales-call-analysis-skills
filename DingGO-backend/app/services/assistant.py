"""首页「今日待办」大卡片与进店前简报（纯规则，与小程序 services/assistant.js 一致）"""

import time

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Store, User
from .constants import ANALYZED, PROCESSING
from .playbook import DIM_TIPS, GENERAL_QUESTIONS, OBJECTION_FOCUS, OBJECTIONS, PROFILE_QUESTIONS, grade, weakest_dim
from .summary import list_stores, list_todos, list_visits, summarize_store
from .timeutil import fmt_date, fmt_duration

DAY_MS = 24 * 3600 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stores(db: Session, user: User) -> list[dict]:
    return list_stores(db, user)


def compute_steps(store: dict | None, visits: list[dict], todos: list[dict]) -> dict:
    labels = ["进店前看简报", "进店中录音", "离店后看复盘", "按待办跟进"]
    current, hint = 0, (f"今天去 {store['name']}？先看一眼简报" if store else "先添加一家门店")
    if store:
        today_key = fmt_date(_now_ms()).split(" ")[0]
        v = next((x for x in visits if x["storeId"] == store["id"] and fmt_date(x["createdAt"]).split(" ")[0] == today_key), None)
        if v and ((v["status"] == "cost_pending" and not v["legacy"]) or v["status"] in PROCESSING):
            current = 2
            hint = "录音已上传，确认费用后开始分析" if v["status"] == "cost_pending" else "录音分析中，好了会出现在下方"
        elif v and v["status"] in ANALYZED:
            open_ = [t for t in todos if t["storeId"] == store["id"] and not t["done"]]
            current = 3 if open_ else 4
            hint = f"还有 {len(open_)} 件跟进事项" if open_ else "今天这家店的工作都完成了"
    return {"labels": labels, "current": current, "hint": hint}


def today_panel(db: Session, user: User, store_id: str | None) -> dict:
    visits = list_visits(db, user)  # 我和下属拜访的
    todos = list_todos(db, user)
    stores = _stores(db, user)
    store = next((s for s in stores if s["id"] == store_id), stores[0] if stores else None)
    now = _now_ms()
    stale_ms = get_settings().stale_days * DAY_MS
    sections = []

    open_ = [t for t in todos if not t["done"]]
    due = [t for t in open_ if t["due"]["days"] <= 0]
    follow = [{
        "key": f"todo-{t['id']}", "badge": t["due"]["text"], "tone": t["due"]["tone"],
        "text": f"{t['storeName']} · {t['topic']}",
        "sub": t["action"] or (f"目标 {t['targetQty']:g}，已达成 {(t['achievedQty'] or 0):g}，差 {t['gapQty']:g}" if t["gapQty"] is not None else ""),
        "btn": "完成", "action": "todoDone", "id": t["id"], "subBtn": "复制微信话术", "subAction": "copyMsg",
    } for t in (due or open_[:1])]
    if follow:
        sections.append({"title": "跟进事项", "rows": follow})

    rec = [{
        "key": f"cost-{v['id']}", "badge": "待确认", "tone": "warn",
        "text": f"{v['storeName']} · 录音 {fmt_duration(v['durationSec'])}", "sub": f"预估识别费 ¥{v['estCost']}，确认后自动分析",
        "btn": "去确认", "action": "openVisit", "id": v["id"],
    } for v in visits if v["status"] == "cost_pending" and not v["legacy"]]  # 飞书导入的历史录音文件还没迁移，不能确认识别，不放进待办
    rec += [{
        "key": f"short-{v['id']}", "badge": "过短", "tone": "muted",
        "text": f"{v['storeName']} · 录音不足 2 分钟", "sub": "下次多问开放式问题，让老板多说",
        "btn": "看怎么问", "action": "openBrief", "id": v["storeId"],
    } for v in visits if v["status"] == "invalid_short" and now - v["createdAt"] < 7 * DAY_MS]
    if rec:
        sections.append({"title": "待处理录音", "rows": rec})

    review = []
    for v in visits:
        if v["status"] not in ANALYZED or v["legacy"] or now - v["createdAt"] >= 3 * DAY_MS or v["reviewed"]:
            continue
        eff = v["analysis"].get("effectiveness")
        weak = weakest_dim(eff)
        tip = DIM_TIPS.get(weak["name"]) if weak else None
        topics = "、".join(a.get("topic", "") for a in v["analysis"]["nextAction"].get("actions", [])) or "无需新增行动"
        review.append({
            "key": f"review-{v['id']}", "badge": f"{eff['total']}分 {grade(eff['total'])}" if eff else "已分析", "tone": "primary",
            "text": f"{v['storeName']} · {v['stage']}",
            "sub": f"最该改：{weak['name']}。{tip}" if tip else f"下一步：{topics}",
            "btn": "看详情", "action": "openVisit", "id": v["id"],
        })
    if review:
        sections.append({"title": "拜访复盘", "rows": review})

    if store:
        profile = store["profile"]
        unconfirmed = sum(1 for x in profile["sections"] if x["state"] == "未确认") if profile else 0
        sections.append({"title": "进店前", "rows": [{
            "key": f"brief-{store['id']}", "badge": "简报", "tone": "info", "text": store["name"],
            "sub": (store["oneLine"] + (f"（{unconfirmed} 项情况待摸清）" if unconfirmed else "")) if profile else "第一次拜访，已备好开场白和要问的问题",
            "btn": "看简报", "action": "openBrief", "id": store["id"],
        }]})

    stale = [{
        "key": f"stale-{s['id']}", "badge": f"{(now - s['lastVisitAt']) // DAY_MS}天未访", "tone": "muted",
        "text": s["name"], "sub": f"上次卡在：{s['actions'][0].get('topic', '')}" if s["actions"] else "去看看最近情况",
        "btn": "看简报", "action": "openBrief", "id": s["id"],
    } for s in sorted((x for x in stores if x["lastVisitAt"] and now - x["lastVisitAt"] > stale_ms), key=lambda x: x["lastVisitAt"])[:3]]
    if stale:
        sections.append({"title": "久未拜访", "rows": stale})

    overdue = sum(1 for t in due if t["due"]["days"] < 0)
    stuck_name = due[0]["storeName"] if due else (stale[0]["text"] if stale else "")
    return {
        "count": len(due),
        "hint": f"今天有 {len(due)} 件事要跟进" + (f"，{overdue} 件已逾期" if overdue else "") if due else "今天暂无到期的跟进事项",
        "stat": f"{len(due)} 件跟进" + (f" · {overdue} 件逾期" if overdue else ""),
        "steps": compute_steps(store, visits, todos),
        "sections": sections,
        "suggestions": ["客户说网上更便宜怎么回？", f"{stuck_name}上次卡在哪？" if stuck_name else "下一步该做什么？", "这家店最关心什么？"],
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
