"""助手记忆：模型只能提议、销售确认才生效；别名解析；团队通用；隔离；会话摘要和上一轮状态；当前门店降权"""

import json

from app.ai import chat as ai_chat
from app.ai import core
from app.db import SessionLocal
from app.models import ChatSession, UserMemory

from .conftest import ADMIN, login, login_user
from .test_chat import scripted
from .test_flow import make_store


def chat(client, auth, question, **extra):
    return client.post("/chat", json={"question": question, **extra}, headers=auth).json()


def test_model_proposal_is_pending_until_user_confirms(client, auth, monkeypatch):
    scripted(monkeypatch, [
        {"action": "tool", "tool": "propose_memory", "args": {"kind": "alias", "key": "城东", "value": ["官渡区", "呈贡区"], "reason": "销售选的", "user_said": False}},
        {"action": "answer", "reply": "要记住「城东」指官渡区、呈贡区吗？", "suggestions": []},
    ])
    r = chat(client, auth, "我今天跑城东")
    assert r["memory"]["status"] == "pending" and "城东" in r["memory"]["text"]
    items = client.get("/memories", headers=auth).json()["items"]
    assert items[0]["status"] == "pending"
    seen = []
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "城东有哪些店")
    assert "助手记住的说法" not in seen[0]  # 没确认前，模型自己提议的不会被当作事实使用
    assert client.post(f"/memories/{r['memory']['id']}/confirm", headers=auth).json()["status"] == "active"
    seen.clear()
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "城东有哪些店")
    assert "「城东」指 官渡区、呈贡区" in seen[0]
    seen.clear()
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "渝中区有哪些店")
    assert "城东" not in seen[0]  # 只带和这句话相关的记忆


def test_user_said_explicitly_is_active_but_model_cannot_fake_it(client, auth, monkeypatch):
    scripted(monkeypatch, [
        {"action": "tool", "tool": "propose_memory", "args": {"kind": "alias", "key": "城南", "value": "南明区、花溪区", "user_said": True}},
        {"action": "answer", "reply": "记住了。", "suggestions": []},
    ])
    r = chat(client, auth, "以后城南就指南明区和花溪区")
    assert r["memory"]["status"] == "active"
    scripted(monkeypatch, [
        {"action": "tool", "tool": "propose_memory", "args": {"kind": "alias", "key": "城北", "value": "北区", "user_said": True}},
        {"action": "answer", "reply": "x", "suggestions": []},
    ])
    r = chat(client, auth, "今天天气不错")  # 销售这句话里根本没有「城北」：模型谎称用户说了也不算
    assert r["memory"]["status"] == "pending"


def test_alias_multi_district_search(client, auth, monkeypatch):
    make_store(client, auth, "官渡店", district="官渡区")
    make_store(client, auth, "呈贡店", district="呈贡区")
    make_store(client, auth, "西山店", district="西山区")
    scripted(monkeypatch, [
        {"action": "tool", "tool": "search_stores", "args": {"district": "官渡区|呈贡区"}},
        {"action": "show_stores", "reply": "城东 2 家"},
    ])
    r = chat(client, auth, "城东的店")
    assert {c["name"] for c in r["cands"]["items"]} == {"官渡店", "呈贡店"}


def test_memory_is_private_manageable_and_limited(client, auth, monkeypatch):
    mid = client.post("/memories", json={"kind": "alias", "key": "城东", "value": ["官渡区"]}, headers=auth).json()["id"]
    bob = login(client, "bob", "小李")
    assert client.get("/memories", headers=bob).json()["items"] == []
    assert client.post(f"/memories/{mid}/confirm", headers=bob).status_code == 404
    assert client.patch(f"/memories/{mid}", json={"value": ["x"]}, headers=bob).status_code == 404
    assert client.delete(f"/memories/{mid}", headers=bob).status_code == 404
    assert client.patch(f"/memories/{mid}", json={"value": ["官渡区", "呈贡区"]}, headers=auth).json()["text"] == "「城东」指 官渡区、呈贡区"
    assert client.post("/memories", json={"kind": "bad", "key": "x", "value": "y"}, headers=auth).status_code == 400
    assert client.delete(f"/memories/{mid}", headers=auth).json() is True
    for i in range(30):
        assert client.post("/memories", json={"kind": "alias", "key": f"说法{i}", "value": ["甲区"]}, headers=auth).status_code == 200
    assert client.post("/memories", json={"kind": "alias", "key": "第31条", "value": ["甲区"]}, headers=auth).status_code == 400


def test_team_alias_shared_down_but_only_by_manager(client, monkeypatch):
    h_boss, boss = login_user(client, "boss", "老板")
    h_amy, amy = login_user(client, "amy", "小王")
    client.patch(f"/admin/users/{boss['id']}", json={"role": "manager"}, headers=ADMIN)
    client.patch(f"/admin/users/{amy['id']}", json={"managerId": boss["id"]}, headers=ADMIN)
    mid = client.post("/memories", json={"kind": "alias", "key": "城东", "value": ["官渡区", "呈贡区"]}, headers=h_boss).json()["id"]
    assert client.post(f"/memories/{mid}/team", json={"team": True}, headers=h_amy).status_code == 404  # 下属碰不到上级的记忆
    assert client.post(f"/memories/{mid}/team", json={"team": True}, headers=h_boss).json()["scope"] == "team"
    seen = []
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    client.post("/chat", json={"question": "城东的店"}, headers=h_amy)
    assert "「城东」指 官渡区、呈贡区（团队通用）" in seen[0]
    assert [i["mine"] for i in client.get("/memories", headers=h_amy).json()["items"]] == [False]
    amy_mid = client.post("/memories", json={"kind": "alias", "key": "城西", "value": ["西山区"]}, headers=h_amy).json()["id"]
    r = client.post(f"/memories/{amy_mid}/team", json={"team": True}, headers=h_amy)
    assert r.status_code == 400 and "只有经理" in r.json()["message"]
    assert client.get("/memories", headers=h_boss).json()["items"][0]["key"] == "城东"  # 经理看不到下属的私人记忆


def test_session_state_summary_and_store_focus(client, auth, monkeypatch):
    a = make_store(client, auth, "甲店", district="渝中区")
    seen = []
    scripted(monkeypatch, [
        {"action": "tool", "tool": "search_stores", "args": {"district": "渝中", "sort": "oldest"}},
        {"action": "show_stores", "reply": "找到"},
    ])
    sid = chat(client, auth, "渝中区最久没去的", storeId=a["id"])["sessionId"]
    with SessionLocal() as db:
        assert db.get(ChatSession, sid).state["last_search"] == {"args": {"district": "渝中", "sort": "oldest", "limit": 10}, "found": 1}
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "再多看几家", sessionId=sid, storeId=a["id"])
    assert "上一轮的查询：条件" in seen[0] and "找到 1 家" in seen[0]
    # 顶部选中了甲店，但这句话没提它：不展开资料，只作背景
    assert "顶部选中了「甲店」" in seen[0] and "门店：甲店" not in seen[0]
    seen.clear()
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "这家店最近怎么样", sessionId=sid, storeId=a["id"])
    assert "门店：甲店" in seen[0]  # 用了「这家店」，就展开
    seen.clear()
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "甲店怎么样", sessionId=sid)
    assert "门店：甲店" in seen[0]  # 直接点名也展开

    # 对话超过一定长度：早先的压成摘要，之后的提问带摘要 + 近几轮原文
    with SessionLocal() as db:
        from app.models import ChatMessage
        for i in range(20):
            db.add(ChatMessage(session_id=sid, role="user" if i % 2 == 0 else "ai", text=f"第{i}句"))
        db.commit()
    monkeypatch.setattr(core, "call_llm", lambda *a, **k: ("销售在看渝中区，已加入甲店。", {}))
    ai_chat.compact_session(sid)
    with SessionLocal() as db:
        s = db.get(ChatSession, sid)
        assert s.summary == "销售在看渝中区，已加入甲店。" and s.summary_upto > 0
    seen.clear()
    scripted(monkeypatch, [{"action": "answer", "reply": "好", "suggestions": []}], seen)
    chat(client, auth, "接着说", sessionId=sid)
    assert "更早对话的摘要：\n销售在看渝中区" in seen[0] and "第19句" in seen[0] and "第0句" not in seen[0]
