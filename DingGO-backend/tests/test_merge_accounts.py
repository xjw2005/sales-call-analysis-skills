"""账号合并：新账号名下的对话、记忆、计划、提问记录要迁到旧人员名下，不能丢、不能因外键报错；有业务数据的仍然拒绝"""

import json

from sqlalchemy import func, select

from app.ai import core
from app.db import SessionLocal
from app.models import ChatLog, ChatMessage, ChatSession, User, UserMemory, VisitPlan
from app.services.accounts import user_fk_columns

from .conftest import ADMIN, login_user
from .test_chat import scripted
from .test_flow import make_store, make_visit


def legacy_user(client, name="曹世茂"):
    r = client.post("/admin/users", json={"name": name, "role": "manager"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def test_merge_moves_assistant_data_and_drops_duplicates(client, monkeypatch):
    h_new, new = login_user(client, "wx-new", "新用户")
    old = legacy_user(client)
    # 旧人员已有一条「城东」别名；新账号也有同名的（要丢掉）和另一条「城西」（要迁过来）
    with SessionLocal() as db:
        db.add(UserMemory(user_id=int(old["id"]), kind="alias", key="城东", value=["官渡区"], status="active"))
        db.commit()
    client.post("/memories", json={"kind": "alias", "key": "城东", "value": ["呈贡区"]}, headers=h_new)
    client.post("/memories", json={"kind": "alias", "key": "城西", "value": ["西山区"]}, headers=h_new)
    scripted(monkeypatch, [{"action": "answer", "reply": "你好", "suggestions": []}])
    sid = client.post("/chat", json={"question": "你好"}, headers=h_new).json()["sessionId"]
    # 今日计划需要门店：新账号还没有业务数据，门店由旧人员的店提供（新账号看不到的店加不进计划，这里直接写库模拟）
    with SessionLocal() as db:
        from app.models import Store
        st = Store(name="某店", primary_sales_id=int(old["id"]))
        db.add(st)
        db.flush()
        from datetime import date
        db.add(VisitPlan(user_id=int(new["id"]), plan_date=date.today(), store_id=st.id))
        db.commit()

    r = client.post("/admin/users/merge", json={"fromId": new["id"], "intoId": old["id"]}, headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"] == old["id"] and out["moved"]["chat_sessions.user_id"] == 1 and out["moved"]["chat_logs.user_id"] == 1
    with SessionLocal() as db:
        assert db.get(User, int(new["id"])) is None and db.get(User, int(old["id"])).openid
        # 没有任何记录还指向被删掉的账号
        for table, col in user_fk_columns():
            assert db.scalar(select(func.count()).select_from(table).where(table.c[col] == int(new["id"]))) == 0, (table.name, col)
        assert db.get(ChatSession, sid).user_id == int(old["id"])
        keys = {(m.key, tuple(m.value)) for m in db.scalars(select(UserMemory).where(UserMemory.user_id == int(old["id"])))}
        assert keys == {("城东", ("官渡区",)), ("城西", ("西山区",))}  # 冲突的保留旧人员的，另一条迁了过来
        assert db.scalar(select(func.count()).select_from(VisitPlan).where(VisitPlan.user_id == int(old["id"]))) == 1
        assert db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.session_id == sid)) == 2
    # 合并后同事用原来的微信登录，直接就是旧人员，能看到合并前的对话
    h_after, who = login_user(client, "wx-new", "新用户")
    assert who["id"] == old["id"]
    assert client.get(f"/chat/sessions/{sid}", headers=h_after).status_code == 200


def test_merge_still_refuses_business_data_and_double_bind(client, monkeypatch):
    h_new, new = login_user(client, "wx-a", "新用户")
    old = legacy_user(client)
    store = make_store(client, h_new)
    make_visit(client, h_new, store["id"])
    r = client.post("/admin/users/merge", json={"fromId": new["id"], "intoId": old["id"]}, headers=ADMIN)
    assert r.status_code == 409 and "不能合并" in r.json()["message"]
    h2, new2 = login_user(client, "wx-b", "新用户2")
    assert client.post("/admin/users/merge", json={"fromId": new2["id"], "intoId": old["id"]}, headers=ADMIN).status_code == 200
    h3, new3 = login_user(client, "wx-c", "新用户3")
    r = client.post("/admin/users/merge", json={"fromId": new3["id"], "intoId": old["id"]}, headers=ADMIN)
    assert r.status_code == 409 and "已经绑定" in r.json()["message"]
    with SessionLocal() as db:
        assert db.get(User, int(new3["id"])) is not None and db.get(User, int(new["id"])) is not None  # 被拒绝时什么都没改
