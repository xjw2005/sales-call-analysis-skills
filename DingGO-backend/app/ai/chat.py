"""首页对话：销售用自然语言提问或安排今天去哪。

一次模型调用，返回 JSON：
- {"action": "plan", "kind": "district|gap|recent|commitments", "district": "渝中区", "reply": "…"}：
  想选店 / 排今天的拜访。模型只负责把话翻译成条件，真正的门店列表和数字由后端查库得到，不会编造门店；
- {"action": "answer", "reply": "…", "suggestions": ["…"]}：回答问题（销售话术、这家店的情况等），
  只能依据下面给出的门店资料和知识，没有依据就如实说不知道。
"""

import json
import logging
import re
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import ChatLog, ChatMessage, ChatSession, Store, User, utcnow
from ..services import plans
from ..services.access import store_filter, team_ids
from ..services.playbook import OBJECTIONS
from ..services.summary import list_todos, list_visits, summarize_store
from . import core, memory
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

{"action":"tool","tool":"add_to_plan","args":{"positions":[1,2],"names":["店名"]}}
  → 把门店加入销售的「今日计划」。positions 是「刚才给销售看的门店」里的序号（销售说“前三家”“第二家”“都加上”时用序号）；
    names 是店名（销售直接点名时用）。两者可同时给。只有销售明确说要去、要加入、要安排时才调用，不要擅自加。
{"action":"tool","tool":"remove_from_plan","args":{"names":["店名"]}}
  → 把门店从今日计划里移出（销售说“XX不去了”“算了”时）。

{"action":"tool","tool":"propose_memory","args":{"kind":"alias|preference","key":"说法","value":["官渡区","呈贡区"],"reason":"为什么记","user_said":false}}
  → 记住销售的说法或习惯。只记「这个人怎么说话、怎么用」，例如别名（「城东」指哪几个区）、常跑区域；不记销量、计划、拜访这些会变的业务事实。
    销售在这句话里明确说了“以后……就指……”“记住……”：user_said=true；你自己推测的：user_said=false（会让销售点确认后才生效）。
    别名的 value 写地区名（多个用数组）；之后用 search_stores 查这类别名时，多个地区用「官渡区|呈贡区」这样的写法放进 district（竖线表示任意一个）。

B) 把最近一次 search_stores 找到的门店以卡片展示给销售（销售可勾选加入今日计划）：
{"action":"show_stores","reply":"一句话说明你按什么条件挑的、有几家"}
  找到的门店为空时不要用它，用 answer 如实告诉销售没有，并说明我们有哪些地方的门店（先查 list_regions）。

C) 其他问题（销售话术、异议怎么回、某家店的情况、下一步做什么），或查询之后的文字回答：
{"action":"answer","reply":"回答正文","suggestions":["最多3个可以接着问的短问题"]}
- 只能依据「门店资料」「参考知识」和工具结果回答，没有的事实不要编造，直接说“目前资料里没有”；
- 涉及数字、日期、门店名，必须来自资料或工具结果；
- 话术类问题给出 2-3 条要点和一句参考话术；
- 「刚才给销售看的门店」和「今日计划」是上下文，销售说“第二家”“前三家”“这几家”指的就是它们；
- 调用 add_to_plan / remove_from_plan 成功后，用 answer 简短确认（说清加了几家、现在计划共几家）；失败或有歧义，如实告诉销售并让他说清楚；
- 销售说的地区你在库里找不到、又像是一个说法（城东、大学城、机场路一带）时：先看「助手记住的说法」有没有；没有就问销售指哪几个区（可用 list_regions 给他选项），他回答后再用 propose_memory 记下；
- 「助手记住的说法」里已经有的别名，直接按它理解，不要再追问；
- 销售问我们有没有某地的门店、有哪些区，先用 list_regions 查，不要凭印象回答；
- 不知道当前是哪家店、问题又和某家店有关时，提醒他先说店名或在顶部选择门店。
"""

LIMIT_CHARS = 6000


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


POINTER = re.compile(r"这家|那家|该店|这个店|这店|它的|它最|它上次|当前门店|这家店")


def find_store(db: Session, user: User, question: str, store_id: str | None) -> tuple[Store | None, bool]:
    """问题里提到的店名优先；其次是首页当前选中的门店。返回 (门店, 这句话是否真的在说它)：
    没提店名也没用「这家 / 它」时，选中的门店只作为背景，不展开资料，免得把话题带偏"""
    ids = team_ids(db, user)
    rows = db.execute(select(Store.id, Store.name).where(store_filter(ids))).all()
    hits = [(len(name), sid) for sid, name in rows if name and len(name) >= 2 and name in question]
    if hits:
        return db.get(Store, max(hits)[1]), True
    if store_id and str(store_id).isdigit():
        return db.scalars(select(Store).where(Store.id == int(store_id), store_filter(ids))).first(), bool(POINTER.search(question))
    return None, False


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


def build_user_message(db: Session, user: User, question: str, store: Store | None, history: list[dict], shown: list[Store] | None = None,
                       memory: str = "", summary: str = "", last_search: dict | None = None, focus: bool = True) -> str:
    parts = []
    if store is not None and focus:
        parts.append("门店资料：\n" + store_context(db, user, store))
    elif store is not None:
        parts.append(f"门店资料：顶部选中了「{store.name}」，但这句话没有提到它；销售问到它时再说“请问是指 {store.name} 吗”，不要主动展开。")
    else:
        parts.append("门店资料：当前没有选中门店，问题里也没有提到门店名。")
    if memory:
        parts.append("助手记住的说法（销售确认过的，直接按它理解）：\n" + memory)
    if summary:
        parts.append("更早对话的摘要：\n" + _clip(summary, 600))
    if last_search:
        parts.append(f"上一轮的查询：条件 {json.dumps(last_search.get('args', {}), ensure_ascii=False)}，找到 {last_search.get('found', 0)} 家")
    if shown:
        parts.append("刚才给销售看的门店（按顺序）：\n" + "\n".join(f"{i}. {x.name}" for i, x in enumerate(shown, 1)))
    plan = plans.today_items(db, user)
    parts.append("今日计划：" + ("；".join(f"{p['name']}（{'已去' if p['status'] == 'visited' else '待去'}）" for p in plan) if plan else "还没有"))
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


class ReplyExtractor:
    """从模型一边生成一边输出的 JSON 里，把 "reply" 字段的文字一个字一个字取出来（流式显示用）。
    只有动作是 answer / show_stores 时才输出；tool 动作没有要给销售看的文字。"""

    ESC = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "r": "", "b": "", "f": ""}

    def __init__(self) -> None:
        self.buf, self.pos, self.action, self.started, self.done, self.sent = "", 0, "", False, False, 0

    def feed(self, piece: str) -> str:
        self.buf += piece
        if not self.action:
            m = re.search(r'"action"\s*:\s*"(\w+)"', self.buf)
            if m:
                self.action = m.group(1)
        if self.action not in ("answer", "show_stores") or self.done:
            return ""
        if not self.started:
            m = re.search(r'"reply"\s*:\s*"', self.buf)
            if not m:
                return ""
            self.started, self.pos = True, m.end()
        out = []
        while self.pos < len(self.buf):
            ch = self.buf[self.pos]
            if ch == '"':
                self.done = True
                break
            if ch == "\\":
                if self.pos + 1 >= len(self.buf):
                    break
                nxt = self.buf[self.pos + 1]
                if nxt == "u":
                    if self.pos + 6 > len(self.buf):
                        break
                    try:
                        out.append(chr(int(self.buf[self.pos + 2:self.pos + 6], 16)))
                    except ValueError:
                        pass
                    self.pos += 6
                    continue
                out.append(self.ESC.get(nxt, nxt))
                self.pos += 2
                continue
            out.append(ch)
            self.pos += 1
        text = "".join(out)
        self.sent += len(text)
        return text


def resolve_stores(db: Session, user: User, names: list, shown: list[Store]) -> tuple[list[Store], list[str]]:
    """把店名解析成门店（只在销售看得到的范围内）。返回 (找到的, 没找到或有歧义的说明)"""
    ids = team_ids(db, user)
    out: list[Store] = []
    problems: list[str] = []
    for name in names:
        name = str(name or "").strip()
        if not name:
            continue
        exact = [x for x in shown if x.name == name]
        if not exact:
            exact = list(db.scalars(select(Store).where(store_filter(ids), Store.name == name)))
        if not exact:
            exact = list(db.scalars(select(Store).where(store_filter(ids), Store.name.contains(name)).limit(6)))
        if len(exact) == 1:
            out.append(exact[0])
        elif not exact:
            problems.append(f"没有找到「{name}」")
        else:
            problems.append(f"「{name}」有多家：" + "、".join(f"{x.name}（{x.code or x.id}）" for x in exact[:5]))
    return out, problems


class Run:
    """一次提问的执行过程：多轮工具调用 → 回答。emit 用来把进度和文字实时推给前端"""

    def __init__(self, db: Session, user: User, emit=None, shown: list[Store] | None = None):
        self.db, self.user, self.emit = db, user, emit or (lambda e: None)
        self.shown = shown or []
        self.found: dict | None = None
        self.plan_changed: list[dict] | None = None
        self.tools: list[dict] = []
        self.rounds, self.tokens = 0, 0
        self.first_token_ms: int | None = None
        self.t0 = time.time()
        self.question, self.session_id = "", None
        self.memory_result: dict | None = None
        self.last_search: dict | None = None

    # ---- 模型调用（流式 / 非流式）
    def llm(self, system: str, user_msg: str) -> str:
        st = get_settings()
        self.rounds += 1
        if not self.emit_stream:
            text, usage = core.call_llm(system, user_msg, st.llm_model, 0.3)
            self.tokens += int((usage or {}).get("total_tokens") or 0)
            return text
        ex, parts = ReplyExtractor(), []
        for kind, val in core.call_llm_stream(system, user_msg, st.llm_model, 0.3):
            if kind == "usage":
                self.tokens += int((val or {}).get("total_tokens") or 0)
                continue
            parts.append(val)
            delta = ex.feed(val)
            if delta:
                if self.first_token_ms is None:
                    self.first_token_ms = int((time.time() - self.t0) * 1000)
                self.emit({"type": "delta", "text": delta})
        return "".join(parts)

    emit_stream = False

    # ---- 工具
    def tool(self, name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        db, user = self.db, self.user
        ok = True
        if name == "list_regions":
            self.emit({"type": "status", "text": "正在查地区…"})
            level = str(args.get("level") or "province")
            try:
                result = json.dumps({"level": level, "regions": plans.regions(db, user, level, str(args.get("parent") or ""))}, ensure_ascii=False)
            except ValueError as e:
                ok, result = False, json.dumps({"error": str(e)}, ensure_ascii=False)
        elif name == "search_stores":
            self.emit({"type": "status", "text": "正在查门店…"})
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
            self.found = {"title": _title(kw, len(items)), "items": items, "kind": "search"}
            self.last_search = {"args": {k: v for k, v in kw.items() if v and v not in ("any", "priority")}, "found": len(items)}
            self.shown = [x for x in (db.get(Store, int(c["storeId"])) for c in items) if x]
            result = json.dumps({"found": len(items), "stores": [{"position": i, "name": c["name"], "district": c["district"], "reason": c["reason"]} for i, c in enumerate(items, 1)]}, ensure_ascii=False)
        elif name == "add_to_plan":
            self.emit({"type": "status", "text": "正在加入今日计划…"})
            picked: list[Store] = []
            problems: list[str] = []
            for pos in args.get("positions") or []:
                try:
                    picked.append(self.shown[int(pos) - 1])
                except (ValueError, TypeError, IndexError):
                    problems.append(f"没有第 {pos} 家")
            more, probs = resolve_stores(db, user, args.get("names") or [], self.shown)
            picked += more
            problems += probs
            if picked:
                plan = plans.add(db, user, [x.id for x in picked], "chat")
                self.plan_changed = plan
                result = json.dumps({"added": [x.name for x in picked], "planTotal": len(plan), "problems": problems}, ensure_ascii=False)
            else:
                ok, result = False, json.dumps({"added": [], "problems": problems or ["没有指定要加入的门店"]}, ensure_ascii=False)
        elif name == "remove_from_plan":
            self.emit({"type": "status", "text": "正在更新今日计划…"})
            stores, problems = resolve_stores(db, user, args.get("names") or [], self.shown)
            current = {int(p["storeId"]): p for p in plans.today_items(db, user)}
            removed = []
            for x in stores:
                p = current.get(x.id)
                if p and plans.remove(db, user, int(p["id"])):
                    removed.append(x.name)
                elif not p:
                    problems.append(f"「{x.name}」不在今日计划里")
            self.plan_changed = plans.today_items(db, user)
            ok = bool(removed)
            result = json.dumps({"removed": removed, "planTotal": len(self.plan_changed), "problems": problems}, ensure_ascii=False)
        elif name == "propose_memory":
            try:
                m, state = memory.propose(db, user, str(args.get("kind") or ""), str(args.get("key") or ""), args.get("value"),
                                          str(args.get("reason") or ""), bool(args.get("user_said")), self.question, self.session_id)
                self.memory_result = {"id": m.id, "text": memory.render(m), "status": m.status}
                result = json.dumps({"status": state, "text": memory.render(m), "needConfirm": m.status == "pending"}, ensure_ascii=False)
            except memory.MemoryError_ as e:
                ok, result = False, json.dumps({"error": str(e)}, ensure_ascii=False)
        else:
            ok, result = False, json.dumps({"error": "没有这个工具"}, ensure_ascii=False)
        self.tools.append({"tool": name, "args": args, "ok": ok})
        return result

    def run(self, question: str, store: Store | None, history: list[dict], focus: bool = True, summary: str = "", last_search: dict | None = None) -> dict:
        self.question = question
        base = build_user_message(self.db, self.user, question, store, history, self.shown,
                                  memory=memory.relevant_text(self.db, self.user, question), summary=summary, last_search=last_search, focus=focus)
        transcript = ""
        self.emit({"type": "status", "text": "思考中…"})
        for step in range(MAX_TOOL_ROUNDS + 1):
            text = self.llm(SYSTEM, base + transcript)
            try:
                out = parse_reply(text)
            except (ValueError, json.JSONDecodeError):
                return {"text": _clip(text, 800) or "我没听明白，可以换个说法吗？", "suggestions": []}
            action = out.get("action")
            if action == "tool" and step < MAX_TOOL_ROUNDS:
                name = str(out.get("tool") or "")
                result = self.tool(name, out.get("args") or {})
                transcript += f"\n\n[你调用了 {name}，参数 {json.dumps(out.get('args') or {}, ensure_ascii=False)}，结果：{_clip(result, 3000)}]\n请继续，输出下一步 JSON。"
                continue
            if action == "tool":
                break
            reply = _clip(str(out.get("reply") or ""), 1200)
            if action == "show_stores":
                if self.found and self.found["items"]:
                    res = {"text": reply or self.found["title"], "suggestions": [], "cands": self.found}
                else:
                    res = {"text": (reply + "\n" if reply else "") + "没有找到符合条件的门店，换个条件试试？", "suggestions": []}
            elif action == "plan":
                res = _legacy_plan(self.db, self.user, out, reply)
            else:
                sugg = [str(x)[:30] for x in (out.get("suggestions") or []) if isinstance(x, str)][:3]
                res = {"text": reply or "目前资料里没有这方面的信息。", "suggestions": sugg}
            if self.plan_changed is not None:
                res["plan"] = self.plan_changed
            if self.memory_result is not None:
                res["memory"] = self.memory_result  # 待确认的，前端显示「记住 / 不用」；已生效的，前端提示「已记住」
            return res
        return {"text": "这个问题我查了几轮还没理清，可以说得再具体一点吗？", "suggestions": []}


def shown_stores(db: Session, user: User, context: dict | None) -> list[Store]:
    """前端回传的「刚给销售看的门店」：只认销售有权限看的"""
    out: list[Store] = []
    ids = team_ids(db, user)
    for item in ((context or {}).get("shown") or [])[:20]:
        sid = str((item or {}).get("storeId", ""))
        if sid.isdigit():
            st = db.scalars(select(Store).where(Store.id == int(sid), store_filter(ids))).first()
            if st:
                out.append(st)
    return out


def _load_session(db: Session, user: User, session_id: int | None, question: str) -> ChatSession:
    """取（或新建）本人的对话。别人的会话号一律当作不存在，不泄露是否存在"""
    if session_id:
        sess = db.get(ChatSession, session_id)
        if sess is None or sess.user_id != user.id:
            raise LookupError("对话不存在")
        return sess
    sess = ChatSession(user_id=user.id, title=_clip(question, 20))
    db.add(sess)
    db.flush()
    return sess


def _stored_history(db: Session, sess: ChatSession, limit: int = 6) -> list[dict]:
    rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sess.id, ChatMessage.id > sess.summary_upto, ChatMessage.role.in_(["user", "ai"]), ChatMessage.text != "")
                      .order_by(ChatMessage.id.desc()).limit(limit)).all()
    return [{"role": r.role, "text": r.text} for r in reversed(rows)]


def _stored_shown(db: Session, user: User, sess: ChatSession) -> list[Store]:
    """这段对话里最近一次给销售看的候选门店（服务端自己记的，不信前端回传）"""
    row = db.scalars(select(ChatMessage).where(ChatMessage.session_id == sess.id, ChatMessage.role == "cands").order_by(ChatMessage.id.desc())).first()
    items = ((row.payload or {}).get("items") if row else None) or []
    return shown_stores(db, user, {"shown": [{"storeId": it.get("storeId")} for it in items]})


def chat(db: Session, user: User, question: str, store_id: str | None = None, session_id: int | None = None, emit=None) -> dict:
    """一次提问。对话上下文（历史、刚给销售看的门店）由服务端按会话取；会话只有本人能访问。
    emit 不为空时走流式：进度和回答文字实时推给 emit；返回值始终是完整结果。
    每次提问都记一条 chat_logs（耗时、模型轮数、token、用到的工具、出错信息）。"""
    question = _clip(question, 500)
    sess = _load_session(db, user, session_id, question)
    history = _stored_history(db, sess)
    store, focus = find_store(db, user, question, store_id)
    run = Run(db, user, emit, _stored_shown(db, user, sess))
    run.emit_stream = emit is not None
    run.session_id = sess.id
    error, result = "", {"text": "", "suggestions": []}
    try:
        result = run.run(question, store, history, focus, sess.summary, (sess.state or {}).get("last_search"))
    except Exception as e:  # noqa: BLE001 记下来再抛给上层处理
        error = str(e)[:500]
        raise
    finally:
        log = ChatLog(user_id=user.id, question=question, reply=result.get("text", "")[:2000], store_id=store.id if store else None, tools=run.tools or None,
                      rounds=run.rounds, tokens=run.tokens, duration_ms=int((time.time() - run.t0) * 1000), first_token_ms=run.first_token_ms, error=error)
        db.add(log)
        db.flush()
        db.add(ChatMessage(session_id=sess.id, role="user", text=question))
        if not error:
            db.add(ChatMessage(session_id=sess.id, role="ai", text=result.get("text", ""), log_id=log.id,
                               payload={"suggestions": result.get("suggestions") or []}))
            if result.get("cands"):
                db.add(ChatMessage(session_id=sess.id, role="cands", payload=result["cands"]))
            if result.get("plan") is not None:
                db.add(ChatMessage(session_id=sess.id, role="plan", payload={"plan": result["plan"]}))
        sess.updated_at = utcnow()
        if run.last_search is not None:
            sess.state = {"last_search": run.last_search}
        db.commit()
        result["logId"], result["sessionId"] = log.id, sess.id
        if not error:
            threading.Thread(target=compact_session, args=(sess.id,), daemon=True).start()
    return result


COMPACT_AFTER = 14  # 摘要之后又累计了这么多条文字消息，就把更早的压成摘要
KEEP_RAW = 6  # 压缩时保留最近这几条原文


def compact_session(session_id: int) -> None:
    """对话变长时，把早先的内容压成一段摘要存在会话里；近几轮保留原文。失败不影响对话"""
    try:
        with SessionLocal() as db:
            sess = db.get(ChatSession, session_id)
            if sess is None:
                return
            rows = list(db.scalars(select(ChatMessage).where(ChatMessage.session_id == sess.id, ChatMessage.id > sess.summary_upto,
                                                             ChatMessage.role.in_(["user", "ai"]), ChatMessage.text != "").order_by(ChatMessage.id)))
            if len(rows) <= COMPACT_AFTER:
                return
            old = rows[:-KEEP_RAW]
            transcript = "\n".join(f"{'销售' if m.role == 'user' else '助手'}：{_clip(m.text, 300)}" for m in old)
            prompt = ("把下面的对话压缩成不超过 300 字的摘要，只保留：销售关心的地区和门店、已经做出的决定（加入/移出计划）、"
                      "还没解决的问题。不要保留寒暄。直接输出摘要文字，不要 JSON。\n\n"
                      + (f"已有摘要：{sess.summary}\n\n" if sess.summary else "") + "新增对话：\n" + transcript)
            text, _ = core.call_llm("你是对话摘要助手。", prompt, get_settings().llm_model, 0.1)
            sess.summary, sess.summary_upto = _clip(text.replace("{", "").replace("}", ""), 500), old[-1].id
            db.commit()
    except Exception:  # noqa: BLE001
        logging.getLogger("dinggo.ai").exception("compact session failed")


def session_messages(db: Session, user: User, session_id: int) -> list[dict]:
    """取一段对话的全部消息（给小程序还原对话用）；不是本人的对话返回 LookupError"""
    sess = db.get(ChatSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise LookupError("对话不存在")
    out = []
    for m in db.scalars(select(ChatMessage).where(ChatMessage.session_id == sess.id).order_by(ChatMessage.id)):
        out.append({"id": m.id, "role": m.role, "text": m.text, "payload": m.payload, "logId": m.log_id})
    return out


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
