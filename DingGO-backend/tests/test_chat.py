"""首页对话：模型只翻译条件，门店和数字由后端查库；用桩替换大模型"""

import json

from app.ai import core

from .test_flow import make_store, make_visit
from .test_plans import month_key, linked_store, setup_stores


def stub(monkeypatch, reply, seen=None):
    def fake(system, user, model, temperature):
        if seen is not None:
            seen.append(user)
        return (reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)), {}

    monkeypatch.setattr(core, "call_llm", fake)


def test_plan_intent_returns_real_candidates(client, auth, monkeypatch):
    setup_stores(client, auth)
    stub(monkeypatch, {"action": "plan", "kind": "district", "district": "渝中区", "reply": "渝中区先看没去过的。"})
    r = client.post("/chat", json={"question": "今天想跑渝中区"}, headers=auth).json()
    assert r["text"] == "渝中区先看没去过的。" and r["cands"]["kind"] == "district"
    assert [c["name"] for c in r["cands"]["items"]] == ["新店B", "老店A"]


def test_plan_gap_and_invalid_district_falls_back(client, auth, monkeypatch):
    cur, m1, m2, m3 = month_key(0), month_key(-1), month_key(-2), month_key(-3)
    linked_store(client, auth, "掉量店", "渝中区", "1", {m3: 20, m2: 20, m1: 20, cur: 4})
    linked_store(client, auth, "正常店1", "渝中区", "2", {m3: 20, m2: 20, m1: 20, cur: 20})
    linked_store(client, auth, "正常店2", "渝中区", "3", {m3: 20, m2: 20, m1: 20, cur: 20})
    stub(monkeypatch, {"action": "plan", "kind": "gap", "district": "", "reply": "按销量低于平时挑的。"})
    r = client.post("/chat", json={"question": "想去销量好冲一冲的店"}, headers=auth).json()
    assert [c["name"] for c in r["cands"]["items"]] == ["掉量店"]
    stub(monkeypatch, {"action": "plan", "kind": "district", "district": "不存在区", "reply": "好的"})
    r = client.post("/chat", json={"question": "去不存在区"}, headers=auth).json()
    assert "没找到这个区" in r["text"] and r["cands"]["kind"] == "gap"  # 不会拿编造的区去查


def test_answer_uses_store_context_and_never_leaks_other_stores(client, auth, monkeypatch):
    a, b, c = setup_stores(client, auth)
    seen = []
    stub(monkeypatch, {"action": "answer", "reply": "上次是 20 天前去的。", "suggestions": ["下一步做什么？"]}, seen)
    r = client.post("/chat", json={"question": "老店A上次什么时候去的？"}, headers=auth).json()
    assert r["text"] == "上次是 20 天前去的。" and r["suggestions"] == ["下一步做什么？"] and "cands" not in r
    assert "门店：老店A" in seen[0] and "远店C" not in seen[0]  # 只带提到的这家店的资料
    from .conftest import login
    bob = login(client, "bob", "小李")
    seen.clear()
    client.post("/chat", json={"question": "老店A怎么样", "storeId": a["id"]}, headers=bob)
    assert "没有选中门店" in seen[0]  # 看不到的门店，资料不会进模型


def test_bad_json_and_failures_are_handled(client, auth, monkeypatch):
    stub(monkeypatch, "我觉得你可以先问问老板最近生意怎么样")
    r = client.post("/chat", json={"question": "怎么开场"}, headers=auth)
    assert r.status_code == 200 and "老板" in r.json()["text"]

    def boom(*a, **k):
        raise RuntimeError("network")

    monkeypatch.setattr(core, "call_llm", boom)
    r = client.post("/chat", json={"question": "你好"}, headers=auth)
    assert r.status_code == 502 and "暂时没有响应" in r.json()["message"]
    assert client.post("/chat", json={"question": ""}, headers=auth).status_code == 422


def scripted(monkeypatch, steps, seen=None):
    """按顺序返回预设的模型输出，模拟多轮工具调用"""
    it = iter(steps)

    def fake(system, user, model, temperature):
        if seen is not None:
            seen.append(user)
        step = next(it)
        return json.dumps(step, ensure_ascii=False), {}

    monkeypatch.setattr(core, "call_llm", fake)


def test_tool_search_by_province_then_show(client, auth, monkeypatch):
    make_store(client, auth, "重庆甲", province="重庆市", city="重庆市", district="渝中区")
    make_store(client, auth, "重庆乙", province="重庆市", city="重庆市", district="九龙坡区")
    make_store(client, auth, "贵阳丙", province="贵州省", city="贵阳市", district="云岩区")
    seen = []
    scripted(monkeypatch, [
        {"action": "tool", "tool": "search_stores", "args": {"province": "重庆", "visit": "never", "limit": 10}},
        {"action": "show_stores", "reply": "重庆有 2 家都没去过。"},
    ], seen)
    r = client.post("/chat", json={"question": "帮我找重庆地区的门店"}, headers=auth).json()
    assert r["text"] == "重庆有 2 家都没去过。" and {c["name"] for c in r["cands"]["items"]} == {"重庆甲", "重庆乙"}
    assert "found" in seen[1] and "重庆甲" in seen[1] and "贵阳丙" not in seen[1]  # 工具结果回传给模型，且只含符合条件的店


def test_tool_list_regions_and_honest_empty(client, auth, monkeypatch):
    make_store(client, auth, "贵阳丙", province="贵州省", city="贵阳市", district="云岩区")
    seen = []
    scripted(monkeypatch, [
        {"action": "tool", "tool": "list_regions", "args": {"level": "province"}},
        {"action": "tool", "tool": "search_stores", "args": {"province": "重庆"}},
        {"action": "answer", "reply": "没有重庆的门店，目前有贵州省的。", "suggestions": []},
    ], seen)
    r = client.post("/chat", json={"question": "重庆有哪些区"}, headers=auth).json()
    assert r["text"].startswith("没有重庆") and "cands" not in r
    assert "贵州省" in seen[1] and '"found": 0' in seen[2]


def test_tool_args_are_sanitized_and_loop_is_bounded(client, auth, monkeypatch):
    make_store(client, auth, "甲", district="渝中区")
    n = {"c": 0}

    def forever(system, user, model, temperature):
        n["c"] += 1
        return json.dumps({"action": "tool", "tool": "search_stores", "args": {"sort": "drop table", "visit": "x", "limit": "abc", "cooperation": "乱写"}}), {}

    monkeypatch.setattr(core, "call_llm", forever)
    r = client.post("/chat", json={"question": "找店"}, headers=auth).json()
    assert n["c"] == 5 and "没理清" in r["text"]  # 最多 4 轮工具 + 1 次收尾，不会死循环
    scripted(monkeypatch, [{"action": "tool", "tool": "rm_rf", "args": {}}, {"action": "answer", "reply": "好", "suggestions": []}])
    assert client.post("/chat", json={"question": "x"}, headers=auth).json()["text"] == "好"


# ---------------------------------------------------------------- 上下文、计划工具、流式、日志与反馈

from app.ai.chat import ReplyExtractor
from app.db import SessionLocal
from app.models import ChatLog


def test_reply_extractor_streams_only_answer_text_across_chunk_boundaries():
    full = '{"action":"answer","reply":"第一行\\n带\\"引号\\"和中文，\\u4f60好","suggestions":["a"]}'
    for size in (1, 3, 7, 50):
        ex = ReplyExtractor()
        out = "".join(ex.feed(full[i:i + size]) for i in range(0, len(full), size))
        assert out == '第一行\n带"引号"和中文，你好', size
    tool = ReplyExtractor()
    assert tool.feed('{"action":"tool","tool":"search_stores","args":{"reply":"x"}}') == ""  # 工具调用不输出文字
    late = ReplyExtractor()  # reply 写在 action 前面：等 action 出现后再一次性补出
    assert late.feed('{"reply":"先写了回复","action"') == "" and late.feed(':"answer"}') == "先写了回复"


def test_context_shown_stores_and_plan_tools(client, auth, monkeypatch):
    a = make_store(client, auth, "甲店", district="渝中区")
    b = make_store(client, auth, "乙店", district="渝中区")
    c = make_store(client, auth, "丙店", district="渝中区")
    seen = []
    scripted(monkeypatch, [
        {"action": "tool", "tool": "add_to_plan", "args": {"positions": [1, 3]}},
        {"action": "answer", "reply": "已把甲店、丙店加入今天。", "suggestions": []},
    ], seen)
    ctx = {"shown": [{"storeId": a["id"]}, {"storeId": b["id"]}, {"storeId": c["id"]}]}
    r = client.post("/chat", json={"question": "把第一家和第三家加进今天", "context": ctx}, headers=auth).json()
    assert "1. 甲店" in seen[0] and "3. 丙店" in seen[0]  # 刚给看的门店带进了上下文
    assert [p["name"] for p in r["plan"]] == ["甲店", "丙店"] and r["logId"]
    assert [p["name"] for p in client.get("/plans/today", headers=auth).json()] == ["甲店", "丙店"]
    seen.clear()
    scripted(monkeypatch, [
        {"action": "tool", "tool": "remove_from_plan", "args": {"names": ["甲店"]}},
        {"action": "tool", "tool": "add_to_plan", "args": {"names": ["乙店", "不存在的店"]}},
        {"action": "answer", "reply": "好了", "suggestions": []},
    ], seen)
    r = client.post("/chat", json={"question": "甲店不去了，加乙店"}, headers=auth).json()
    assert "今日计划：甲店（待去）；丙店（待去）" in seen[0]  # 今天的计划也在上下文里
    assert '"removed": ["甲店"]' in seen[1] and "没有找到「不存在的店」" in seen[2]
    assert [p["name"] for p in r["plan"]] == ["丙店", "乙店"]


def test_add_to_plan_cannot_reach_invisible_stores(client, auth, monkeypatch):
    from .conftest import login
    a = make_store(client, auth, "甲店", district="渝中区")
    bob = login(client, "bob", "小李")
    scripted(monkeypatch, [
        {"action": "tool", "tool": "add_to_plan", "args": {"names": ["甲店"], "positions": [1]}},
        {"action": "answer", "reply": "没加上", "suggestions": []},
    ])
    r = client.post("/chat", json={"question": "加甲店", "context": {"shown": [{"storeId": a["id"]}]}}, headers=bob).json()
    assert "plan" not in r and client.get("/plans/today", headers=bob).json() == []


def test_stream_endpoint_events_log_and_feedback(client, auth, monkeypatch):
    make_store(client, auth, "甲店", district="渝中区")
    pieces = ['{"action":"tool","tool":"search_stores","args":{"district":"渝中"}}'], ['{"action":"show_stores","re', 'ply":"找到 1 家，', '先看这家。"}']
    calls = iter(pieces)

    def fake_stream(system, user, model, temperature):
        for p in next(calls):
            yield "delta", p
        yield "usage", {"total_tokens": 100}

    monkeypatch.setattr(core, "call_llm_stream", fake_stream)
    r = client.post("/chat/stream", json={"question": "渝中区有什么店"}, headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    types = [e["type"] for e in events]
    assert types[0] == "status" and "delta" in types and types[-1] == "final"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "找到 1 家，先看这家。"
    final = events[-1]
    assert final["cands"]["items"][0]["name"] == "甲店" and final["logId"]
    with SessionLocal() as db:
        log = db.get(ChatLog, final["logId"])
        assert log.rounds == 2 and log.tokens == 200 and log.tools[0]["tool"] == "search_stores" and log.first_token_ms is not None and log.error == ""
    assert client.post("/chat/feedback", json={"logId": final["logId"], "rating": -1, "note": "没找到想要的"}, headers=auth).json() is True
    with SessionLocal() as db:
        assert db.get(ChatLog, final["logId"]).rating == -1
    from .conftest import login
    assert client.post("/chat/feedback", json={"logId": final["logId"], "rating": 1}, headers=login(client, "bob")).status_code == 404
    assert client.post("/chat/feedback", json={"logId": final["logId"], "rating": 5}, headers=auth).status_code == 422


def test_stream_failure_becomes_error_event_and_is_not_leaky(client, auth, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internals")
        yield

    monkeypatch.setattr(core, "call_llm_stream", boom)
    r = client.post("/chat/stream", json={"question": "你好"}, headers=auth)
    events = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    assert events[-1] == {"type": "error", "message": "AI 暂时没有响应，请稍后再试"}
    assert "secret" not in r.text
