"""整改脚本测试：合并重复门店、补回门店 ID、待办挂门店（虚构数据）"""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Store, StoreCorrection, StoreDirectory, StoreProfileSection, Todo, User, Visit

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fix_legacy_data.py"
spec = importlib.util.spec_from_file_location("fix_legacy_data", SCRIPT)
fix = importlib.util.module_from_spec(spec)
sys.modules["fix_legacy_data"] = fix
spec.loader.exec_module(fix)


@pytest.fixture
def seeded():
    with SessionLocal() as db:
        u = User(name="甲", role="manager")
        db.add(u)
        db.flush()
        a = Store(code="ST-1", name="亲贝比", primary_sales_id=u.id, contact_phone="", address="")
        b = Store(code="ST-2", name="亲贝比", primary_sales_id=None, contact_phone="139", address="路1号", cooperation_status="已合作")
        c = Store(code="ST-3", name="登康贝比", primary_sales_id=u.id)
        db.add_all([a, b, c, StoreDirectory(platform="智生活", external_id="7378", name="亲贝比")])
        db.flush()
        for st, code in ((a, "RA-1"), (b, "RA-2")):
            db.add(Visit(code=code, store_id=st.id, visitor_id=u.id, entered_at=datetime(2026, 9, 1), stage="日常维护", status="done", legacy=True))
        db.add(StoreProfileSection(store_id=a.id, key="basic", state="未确认", content=""))
        db.add(StoreProfileSection(store_id=b.id, key="basic", state="稳定档案", content="夫妻店"))
        db.add(StoreProfileSection(store_id=b.id, key="price_profit", state="当前状态", content="怕被打穿"))
        db.add(StoreCorrection(store_id=b.id, text="约80平"))
        db.add(Todo(source="import", topic="登康：9月任务拆解", period="2026-09", target_qty=10))
        db.add(Todo(source="import", topic="合计", period="2026-09", target_qty=99))
        db.commit()


PLAN = {
    "merge_stores": [{"keep": "ST-1", "drop": "ST-2"}],
    "set_external_id": [{"code": "ST-1", "platform": "智生活", "externalId": "7378"}],
    "link_todo": [{"topic": "登康：9月任务拆解", "storeCode": "ST-3"}],
    "cancel_todo": [{"topic": "合计"}],
}


def test_plan_applies_and_is_idempotent(seeded):
    with SessionLocal() as db:
        log = fix.apply_plan(db, PLAN)
        db.commit()
        assert len(log) == 4
        keep = db.scalar(select(Store).where(Store.code == "ST-1"))
        assert db.scalar(select(Store).where(Store.code == "ST-2")) is None
        assert db.scalar(select(func.count()).select_from(Visit).where(Visit.store_id == keep.id)) == 2
        assert db.scalar(select(func.count()).select_from(StoreCorrection).where(StoreCorrection.store_id == keep.id)) == 1
        secs = {x.key: x for x in db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == keep.id))}
        assert secs["basic"].content == "夫妻店" and secs["price_profit"].content == "怕被打穿"  # keep 的空维度被补上
        assert keep.contact_phone == "139" and keep.address == "路1号" and keep.cooperation_status == "已合作"
        assert keep.external_id == "7378" and keep.directory_id is not None
        t = db.scalar(select(Todo).where(Todo.period == "2026-09", Todo.target_qty == 10))
        assert db.scalar(select(Todo.status).where(Todo.topic == "合计")) == "cancelled"
        assert t.store_id == db.scalar(select(Store.id).where(Store.code == "ST-3")) and t.topic == "9月任务拆解" and t.assignee_id is not None

        again = fix.apply_plan(db, PLAN)
        assert all("跳过" in x for x in again)


def test_dry_run_and_conflicts_do_not_write(seeded):
    with SessionLocal() as db:
        fix.apply_plan(db, PLAN)
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Store)) == 3
    with SessionLocal() as db:
        with pytest.raises(fix.PlanError):
            fix.apply_plan(db, {"merge_stores": [{"keep": "ST-1", "drop": "ST-2"}], "set_external_id": [{"code": "ST-3", "platform": "智生活", "externalId": "7378"}, {"code": "ST-1", "platform": "智生活", "externalId": "7378"}]})
        db.rollback()
        assert db.scalar(select(func.count()).select_from(Store)) == 3
