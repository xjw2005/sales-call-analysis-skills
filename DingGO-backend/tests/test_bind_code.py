"""绑定码：不认识的微信不再自动建账号，输入管理员发的一次性码才能绑定到人员"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import BindCode, User, utcnow
from app.routers import auth as auth_router

from .conftest import ADMIN


@pytest.fixture
def real_login(monkeypatch):
    """模拟真实微信登录：开发登录关闭，openid 由 code 决定"""
    from app.config import get_settings
    monkeypatch.setenv("DEV_LOGIN", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(auth_router, "code_to_openid", lambda code: f"wx-{code}")
    auth_router._FAILS.clear()
    yield
    get_settings.cache_clear()


def new_person(client, name="曹世茂"):
    return client.post("/admin/users", json={"name": name, "role": "sales"}, headers=ADMIN).json()


def wx(client, code):
    return client.post("/auth/wx-login", json={"code": code}).json()


def test_unknown_wechat_gets_no_account_until_bound(client, real_login):
    amy = new_person(client)
    r = wx(client, "amy")
    assert r["needBind"] is True and "token" not in r and "bindToken" in r
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.openid.is_not(None))) == 0  # 没有自动建账号
    code = client.post(f"/admin/users/{amy['id']}/bind-code", headers=ADMIN).json()
    assert len(code["code"]) == 8 and code["user"]["name"] == "曹世茂"
    with SessionLocal() as db:
        assert code["code"] not in {c.code_hash for c in db.scalars(select(BindCode))}  # 库里只有摘要
    ok = client.post("/auth/bind", json={"bindToken": r["bindToken"], "code": code["code"]})
    assert ok.status_code == 200 and ok.json()["user"]["id"] == amy["id"]
    again = wx(client, "amy")  # 以后登录直接进入，就是曹世茂本人
    assert again["user"]["id"] == amy["id"] and "needBind" not in again
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {again['token']}"}).json()
    assert me["name"] == "曹世茂"
    assert [u["bound"] for u in client.get("/admin/users", headers=ADMIN).json() if u["id"] == amy["id"]] == [True]


def test_code_is_single_use_expires_and_replaced(client, real_login):
    amy, bob = new_person(client, "甲"), new_person(client, "乙")
    t_amy, t_bob = wx(client, "a")["bindToken"], wx(client, "b")["bindToken"]
    c1 = client.post(f"/admin/users/{amy['id']}/bind-code", headers=ADMIN).json()["code"]
    c2 = client.post(f"/admin/users/{amy['id']}/bind-code", headers=ADMIN).json()["code"]  # 重新生成：旧码作废
    assert client.post("/auth/bind", json={"bindToken": t_amy, "code": c1}).status_code == 400
    assert client.post("/auth/bind", json={"bindToken": t_amy, "code": c2}).status_code == 200
    r = client.post("/auth/bind", json={"bindToken": t_bob, "code": c2})  # 用过的码不能再用
    assert r.status_code == 400 and "不正确或已过期" in r.json()["message"]
    c3 = client.post(f"/admin/users/{bob['id']}/bind-code", headers=ADMIN).json()["code"]
    with SessionLocal() as db:
        for row in db.scalars(select(BindCode).where(BindCode.used_at.is_(None))):
            row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.post("/auth/bind", json={"bindToken": t_bob, "code": c3}).status_code == 400  # 过期
    assert client.post(f"/admin/users/{amy['id']}/bind-code", headers=ADMIN).status_code == 409  # 已绑定的不能再发码


def test_bruteforce_is_locked_and_tokens_are_not_interchangeable(client, real_login):
    amy = new_person(client)
    token = wx(client, "x")["bindToken"]
    code = client.post(f"/admin/users/{amy['id']}/bind-code", headers=ADMIN).json()["code"]
    for _ in range(5):
        assert client.post("/auth/bind", json={"bindToken": token, "code": "00000000"}).status_code == 400
    r = client.post("/auth/bind", json={"bindToken": token, "code": code})  # 错太多次后连正确的码也被锁
    assert r.status_code == 429 and "10 分钟" in r.json()["message"]
    assert client.post("/auth/bind", json={"bindToken": "garbage", "code": code}).status_code == 401
    # 登录令牌不能当绑定令牌；绑定令牌不能当登录令牌
    other = new_person(client, "乙")
    ok_token = wx(client, "y")["bindToken"]
    c2 = client.post(f"/admin/users/{other['id']}/bind-code", headers=ADMIN).json()["code"]
    login_token = client.post("/auth/bind", json={"bindToken": ok_token, "code": c2}).json()["token"]
    assert client.post("/auth/bind", json={"bindToken": login_token, "code": code}).status_code == 401
    assert client.get("/stores", headers={"Authorization": f"Bearer {ok_token}"}).status_code == 401


def test_dev_login_and_switch_off_still_work(client, monkeypatch):
    # 开发登录（DEV_LOGIN=true）仍然自动建账号；关掉绑定码要求时，也恢复自动建账号
    r = client.post("/auth/wx-login", json={"code": "dev1", "name": "测试"}).json()
    assert "token" in r
    from app.config import get_settings
    monkeypatch.setenv("DEV_LOGIN", "false")
    monkeypatch.setenv("REQUIRE_BIND_CODE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(auth_router, "code_to_openid", lambda code: f"wx-{code}")
    assert "token" in client.post("/auth/wx-login", json={"code": "z"}).json()
    get_settings.cache_clear()
