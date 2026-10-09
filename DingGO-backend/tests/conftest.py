import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update({
    # 默认用临时 SQLite；想在 MySQL 上跑测试：设置 TEST_DATABASE_URL（会清空该库里的表，请用专门的测试库）
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db"),
    "STORAGE_DIR": f"{_tmp}/uploads",
    "ADMIN_TOKEN": "test-admin",
    "JWT_SECRET": "test-secret",
    "WX_SECRET": "",
    "DEV_LOGIN": "true",
    # AI 相关：假密钥让 configured() 为真；后台线程在测试里关闭，由用例直接调用 run_visit
    "LAS_API_KEY": "test-las",
    "LLM_API_URL": "http://llm.invalid/v1",
    "LLM_API_KEY": "test-llm",
    "AI_WORKER": "false",
    "KNOWLEDGE_ENABLED": "false",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def fresh_limiter():
    from app.ai.limits import limiter

    limiter.recent.clear(), limiter.active_users.clear(), limiter.waiting.clear()
    limiter.running = 0
    yield


@pytest.fixture
def client():
    return TestClient(app)


def login_user(client, code="alice", name="小熊"):
    """返回 (请求头, 用户信息)"""
    r = client.post("/auth/wx-login", json={"code": code, "name": name}).json()
    return {"Authorization": f"Bearer {r['token']}"}, r["user"]


def login(client, code="alice", name="小熊"):
    return login_user(client, code, name)[0]


@pytest.fixture
def auth(client):
    return login(client)
