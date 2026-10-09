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

你必须只输出一个 JSON 对象，不要输出其他文字。两种形式：

1) 销售想选门店、排今天或这几天的拜访（例如“今天想去销量高的店”“渝中区有哪些久没去的”“最近承诺过什么”）：
{"action":"plan","kind":"district|gap|recent|commitments","district":"区县名或空串","reply":"一句话说明你给他挑的依据"}
- kind=district：按片区列出该区门店，district 必须是下面「可选区县」里的一个；
- kind=gap：本期销量低于平时水平的店，district 可为空（全部）或某个可选区县；
- kind=recent：最近拜访过的店；
- kind=commitments：有到期约定的店。
“销量高”之类你无法精确查的说法，选最接近的 kind 并在 reply 里如实说明依据（例如“按销量低于平时的店给你挑了，这是最有冲刺空间的”）。
如果销售没说区县又需要区县，district 留空，kind 用 gap 或 recent。

2) 其他问题（销售话术、异议怎么回、某家店的情况、下一步做什么）：
{"action":"answer","reply":"回答正文","suggestions":["最多3个可以接着问的短问题"]}
- 只能依据「门店资料」和「参考知识」回答，资料里没有的事实不要编造，直接说“目前资料里没有”；
- 涉及数字、日期、门店名，必须来自资料；
- 话术类问题给出 2-3 条要点和一句参考话术；
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
    dists = [d["district"] for d in plans.districts(db, user, limit=40)]
    parts = ["可选区县：" + ("、".join(dists) if dists else "（暂无）")]
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
    msg = build_user_message(db, user, question, store, history or [])
    text, _ = core.call_llm(SYSTEM, msg, st.llm_model, 0.3)
    try:
        out = parse_reply(text)
    except (ValueError, json.JSONDecodeError):
        return {"text": _clip(text, 800) or "我没听明白，可以换个说法吗？", "suggestions": []}
    reply = _clip(str(out.get("reply") or ""), 1200)
    if out.get("action") == "plan":
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
    sugg = [str(s)[:30] for s in (out.get("suggestions") or []) if isinstance(s, str)][:3]
    return {"text": reply or "目前资料里没有这方面的信息。", "suggestions": sugg}


def _cands(db: Session, user: User, kind: str, district: str | None) -> dict:
    res = plans.suggest(db, user, kind, district) if kind != "district" or district else {"title": "", "items": []}
    return {**res, "kind": kind}
