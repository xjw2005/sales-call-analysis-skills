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
