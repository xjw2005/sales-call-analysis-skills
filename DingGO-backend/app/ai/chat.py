"""首页对话：销售用自然语言提问或安排今天去哪。

一次模型调用，返回 JSON：
- {"action": "plan", "kind": "district|gap|recent|commitments", "district": "渝中区", "reply": "…"}：
  想选店 / 排今天的拜访。模型只负责把话翻译成条件，真正的门店列表和数字由后端查库得到，不会编造门店；
- {"action": "answer", "reply": "…", "suggestions": ["…"]}：回答问题（销售话术、这家店的情况等），
  只能依据下面给出的门店资料和知识，没有依据就如实说不知道。
"""

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Store, User
from ..services import plans
from ..services.access import store_filter, team_ids
from ..services.playbook import OBJECTIONS
from ..services.summary import list_todos, list_visits, summarize_store
from . import core
from . import knowledge as kb

SYSTEM = """你是「DingGo」，A2 奶粉销售团队的 AI 销售助手，和一线销售对话。回答要简短、口语化、可直接照着用。

你每次只输出一个 JSON 对象，不要输出其他文字。可用的动作：

A) 调用工具查真实数据（一次一个，结果会在下一轮发给你，最多查 4 次）：
{"action":"tool","tool":"list_regions","args":{"level":"province|city|district","parent":"上一级名称，可空"}}
  → 列出我们有门店的省/市/区县及门店数。不确定某个地方有没有门店、区县叫什么时，先查它。
{"action":"tool","tool":"search_stores","args":{"province":"","city":"","district":"","keyword":"","cooperation":"","visit":"any","sort":"priority","limit":10}}
  → 按条件找门店。省市区做包含匹配（填「重庆」就能匹配「重庆市」）；keyword 匹配店名或地址；
    cooperation 可填 已合作|已触达未合作|意向中|未触达；
    visit：any 不限 | never 从未拜访 | stale 30 天以上没去 | recent 14 天内去过；
    sort：priority 综合优先级（有到期约定、销量偏低、久未拜访的在前）| sales_gap 本期销量低于平时的（差额大的在前）| oldest 最久没去的在前 | recent 最近去过的在前。
  工具的结果全部来自数据库，门店名、数字只能用工具返回的。

B) 把最近一次 search_stores 找到的门店以卡片展示给销售（销售可勾选加入今日计划）：
{"action":"show_stores","reply":"一句话说明你按什么条件挑的、有几家"}
  找到的门店为空时不要用它，用 answer 如实告诉销售没有，并说明我们有哪些地方的门店（先查 list_regions）。

C) 其他问题（销售话术、异议怎么回、某家店的情况、下一步做什么），或查询之后的文字回答：
{"action":"answer","reply":"回答正文","suggestions":["最多3个可以接着问的短问题"]}
- 只能依据「门店资料」「参考知识」和工具结果回答，没有的事实不要编造，直接说“目前资料里没有”；
- 涉及数字、日期、门店名，必须来自资料或工具结果；
- 话术类问题给出 2-3 条要点和一句参考话术；
- 销售问我们有没有某地的门店、有哪些区，先用 list_regions 查，不要凭印象回答；
- 不知道当前是哪家店、问题又和某家店有关时，提醒他先说店名或在顶部选择门店。
"""

LIMIT_CHARS = 6000


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def find_store(db: Session, user: User, question: str, store_id: str | None) -> Store | None:
    """问题里提到的店名优先，其次是首页当前选中的门店"""
    ids = team_ids(db, user)
    rows = db.execute(select(Store.id, Store.name).where(store_filter(ids))).all()
    hits = [(len(name), sid) for sid, name in rows if name and len(name) >= 2 and name in question]
    if hits:
        return db.get(Store, max(hits)[1])
    if store_id and str(store_id).isdigit():
        return db.scalars(select(Store).where(Store.id == int(store_id), store_filter(ids))).first()
    return None


def store_context(db: Session, user: User, store: Store) -> str:
    visits = list_visits(db, user, store_id=store.id)
    s = summarize_store(db, store, visits)
    detail = summarize_store(db, store, visits, detail=True)
    lines = [f"门店：{s['name']}（{s['cooperationStatus']}，{s['city']}{s['district']}{s['address']}）", f"累计拜访 {len(visits)} 次"]
    if visits:
        v = visits[0]
        lines.append(f"最近一次拜访：{core_date(v['createdAt'])} {v['stage']}，状态 {v['status']}")
    if s.get("oneLine"):
        lines.append(f"一句话画像：{s['oneLine']}")
    for sec in (detail.get("profile") or {}).get("sections", []):
        if sec["state"] != "未确认" and sec.get("content"):
            lines.append(f"{sec['label']}：{_clip(sec['content'], 200)}")
    last = next((v for v in visits if v["status"] in ("done", "partial_manual") and not v["legacy"]), None)
    if last:
        a = last["analysis"]
        hits = [c["name"] for c in a["concerns"] if c.get("state") == "是"]
        if hits:
            lines.append("客户关心点：" + "、".join(hits))
        if a.get("effectiveness"):
            lines.append(f"上次合作进展评分：{a['effectiveness']['total']} 分；{_clip(a['effectiveness'].get('conclusion', ''), 150)}")
        for act in a["nextAction"].get("actions", [])[:3]:
            lines.append(f"建议行动：{act.get('topic', '')}——{act.get('action', '')}（{act.get('timeframe', '')}）")
    legacy = next((v for v in visits if v["legacy"] and v["status"] in ("done", "partial_manual")), None)
    if legacy and not last:
        lines.append("（这家店只有从飞书导入的历史分析，没有结构化结果）")
    todos = [t for t in list_todos(db, user, scope="team") if t["storeId"] == str(store.id) and not t["done"]]
    for t in todos[:5]:
        lines.append(f"待办：{t['topic']}（{t['due']['text']}）")
    return "\n".join(lines)


def core_date(ms: int) -> str:
    from ..services.timeutil import fmt_date
    return fmt_date(ms)


def build_user_message(db: Session, user: User, question: str, store: Store | None, history: list[dict]) -> str:
    parts = []
    if store is not None:
        parts.append("门店资料：\n" + store_context(db, user, store))
    else:
        parts.append("门店资料：当前没有选中门店，问题里也没有提到门店名。")
    core.init_knowledge()
    know = "\n\n".join(b for b in (kb.knowledge_block_for("concerns"), kb.knowledge_block_for("next-action")) if b)
    objections = "\n".join(f"- 客户说「{o['says']}」：{'；'.join(o['points'])}。参考话术：{o['script']}" for o in OBJECTIONS.values())
    parts.append("参考话术库：\n" + objections)
    if know:
        parts.append("参考知识（只用于理解和归类，不得据此编造门店事实）：\n" + _clip(know, 3000))
    if history:
        parts.append("此前对话：\n" + "\n".join(f"{'销售' if h['role'] == 'user' else '助手'}：{_clip(h['text'], 200)}" for h in history[-6:]))
    parts.append("销售现在说：" + question)
    return _clip("\n\n".join(parts), LIMIT_CHARS * 3)


MAX_TOOL_ROUNDS = 4
SEARCH_ARGS = ("province", "city", "district", "keyword", "cooperation", "visit", "sort")


def run_tool(db: Session, user: User, tool: str, args: dict):
    """执行模型请求的工具；返回 (给模型看的文字, 候选结果或 None)。参数一律校验，不信任模型"""
    args = args if isinstance(args, dict) else {}
    if tool == "list_regions":
        level = str(args.get("level") or "province")
        rows = plans.regions(db, user, level, str(args.get("parent") or ""))
        return json.dumps({"level": level, "regions": rows}, ensure_ascii=False), None
    if tool == "search_stores":
        kw = {k: str(args.get(k) or "")[:40] for k in SEARCH_ARGS}
        if kw["visit"] not in ("any", "never", "stale", "recent"):
            kw["visit"] = "any"
        if kw["sort"] not in ("priority", "sales_gap", "oldest", "recent"):
            kw["sort"] = "priority"
        if kw["cooperation"] not in ("", "已合作", "已触达未合作", "意向中", "未触达"):
            kw["cooperation"] = ""
        try:
            kw["limit"] = max(1, min(int(args.get("limit") or 10), 20))
        except (TypeError, ValueError):
            kw["limit"] = 10
        items = plans.search(db, user, **kw)
        brief = [{"name": c["name"], "district": c["district"], "reason": c["reason"]} for c in items]
        return json.dumps({"found": len(items), "stores": brief}, ensure_ascii=False), {"title": _title(kw, len(items)), "items": items, "kind": "search"}
    return json.dumps({"error": "没有这个工具"}, ensure_ascii=False), None


def _title(kw: dict, n: int) -> str:
    where = "".join(kw[k] for k in ("province", "city", "district")) or "全部地区"
    sort = {"priority": "优先级从高到低", "sales_gap": "销量低于平时、差额从大到小", "oldest": "最久没去的在前", "recent": "最近去过的在前"}[kw["sort"]]
    return f"{where}的门店（{n} 家，{sort}）"


def parse_reply(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回的不是对象")
    return value


def chat(db: Session, user: User, question: str, store_id: str | None = None, history: list[dict] | None = None) -> dict:
    st = get_settings()
    question = _clip(question, 500)
    store = find_store(db, user, question, store_id)
    base = build_user_message(db, user, question, store, history or [])
    transcript, found = "", None
    for step in range(MAX_TOOL_ROUNDS + 1):
        text, _ = core.call_llm(SYSTEM, base + transcript, st.llm_model, 0.3)
        try:
            out = parse_reply(text)
        except (ValueError, json.JSONDecodeError):
            return {"text": _clip(text, 800) or "我没听明白，可以换个说法吗？", "suggestions": []}
        action = out.get("action")
        if action == "tool" and step < MAX_TOOL_ROUNDS:
            tool = str(out.get("tool") or "")
            result, cands = run_tool(db, user, tool, out.get("args") or {})
            if cands is not None:
                found = cands
            transcript += f"\n\n[你调用了 {tool}，参数 {json.dumps(out.get('args') or {}, ensure_ascii=False)}，结果：{_clip(result, 3000)}]\n请继续，输出下一步 JSON。"
            continue
        if action == "tool":  # 工具轮数用完了模型还在查
            break
        reply = _clip(str(out.get("reply") or ""), 1200)
        if action == "show_stores":
            if found and found["items"]:
                return {"text": reply or found["title"], "suggestions": [], "cands": found}
            return {"text": (reply + "\n" if reply else "") + "没有找到符合条件的门店，换个条件试试？", "suggestions": []}
        if action == "plan":  # 兼容旧格式：直接给 kind
            return _legacy_plan(db, user, out, reply)
        sugg = [str(x)[:30] for x in (out.get("suggestions") or []) if isinstance(x, str)][:3]
        return {"text": reply or "目前资料里没有这方面的信息。", "suggestions": sugg}
    return {"text": "这个问题我查了几轮还没理清，可以说得再具体一点吗？", "suggestions": []}


def _legacy_plan(db: Session, user: User, out: dict, reply: str) -> dict:
    kind = out.get("kind")
    district = str(out.get("district") or "").strip() or None
    valid = {d["district"] for d in plans.districts(db, user, limit=200)}
    if kind == "district" and district not in valid:
        return {"text": reply + "\n（没找到这个区，下面按销量低于平时的店给你挑。）" if reply else "没找到这个区，我按销量低于平时的店给你挑。", "suggestions": [],
                "cands": _cands(db, user, "gap", None)}
    if kind not in ("district", "gap", "recent", "commitments"):
        kind = "recent"
    if district and district not in valid:
        district = None
    res = _cands(db, user, kind, district)
    if not res["items"]:
        return {"text": (reply + "\n" if reply else "") + "不过没有符合条件的门店，换个条件试试？", "suggestions": []}
    return {"text": reply or res["title"], "suggestions": [], "cands": res}


def _cands(db: Session, user: User, kind: str, district: str | None) -> dict:
    res = plans.suggest(db, user, kind, district) if kind != "district" or district else {"title": "", "items": []}
    return {**res, "kind": kind}
