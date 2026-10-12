"""删除门店、拜访：只是隐藏，管理员可恢复"""

from .conftest import ADMIN, login
from .test_flow import make_store, make_visit


def test_delete_visit_hides_it_everywhere_and_admin_can_restore(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"], recordingMode="none", noRecordingReason="老板不在")
    assert client.post("/plans", json={"storeIds": [s["id"]], "source": "manual"}, headers=auth).json()[0]["status"] == "visited"
    todo = client.post("/todos", json={"topic": "送样品", "storeId": s["id"]}, headers=auth).json()

    assert client.delete(f"/visits/{v['id']}", headers=auth).json() == {"ok": True}
    assert client.get("/visits", headers=auth).json() == []
    assert client.get(f"/visits/{v['id']}", headers=auth).status_code == 404
    assert client.get(f"/stores/{s['id']}", headers=auth).json()["visitCount"] == 0
    plan = client.get("/plans/today", headers=auth).json()
    assert plan[0]["status"] == "planned" and plan[0]["visitId"] == ""  # 这家店改回「待去」
    assert any(t["id"] == todo["id"] for t in client.get("/todos", headers=auth).json())  # 手建的待办不受影响
    assert client.delete(f"/visits/{v['id']}", headers=auth).status_code == 404  # 删过的不能再操作

    deleted = client.get("/admin/deleted", headers=ADMIN).json()
    assert [x["id"] for x in deleted["visits"]] == [v["id"]] and deleted["visits"][0]["deletedBy"] == "小熊"
    assert client.post(f"/admin/visits/{v['id']}/restore", headers=ADMIN).json() == {"ok": True}
    assert [x["id"] for x in client.get("/visits", headers=auth).json()] == [v["id"]]


def test_delete_visit_cancels_its_open_todos(client, auth):
    from app.db import SessionLocal
    from app.models import Todo

    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    with SessionLocal() as db:
        db.add_all([Todo(visit_id=int(v["id"]), store_id=int(s["id"]), topic="AI 建议", status="open"),
                    Todo(visit_id=int(v["id"]), store_id=int(s["id"]), topic="已做完", status="done")])
        db.commit()
    client.delete(f"/visits/{v['id']}", headers=auth)
    with SessionLocal() as db:
        assert {t.topic: t.status for t in db.query(Todo)} == {"AI 建议": "cancelled", "已做完": "done"}


def test_cannot_delete_visit_while_processing_or_of_others(client, auth):
    from app.db import SessionLocal
    from app.models import Visit

    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    with SessionLocal() as db:
        db.get(Visit, int(v["id"])).status = "analyzing"
        db.commit()
    r = client.delete(f"/visits/{v['id']}", headers=auth)
    assert r.status_code == 409 and "正在识别分析" in r.json()["message"]
    assert client.delete(f"/visits/{v['id']}", headers=login(client, "bob")).status_code == 404


def test_delete_store_only_without_visits(client, auth):
    s = make_store(client, auth)
    other = make_store(client, auth, name="有拜访的店")
    make_visit(client, auth, other["id"], recordingMode="none", noRecordingReason="老板不在")
    client.post("/plans", json={"storeIds": [s["id"]], "source": "manual"}, headers=auth)
    todo = client.post("/todos", json={"topic": "送样品", "storeId": s["id"]}, headers=auth).json()

    r = client.delete(f"/stores/{other['id']}", headers=auth)
    assert r.status_code == 409 and "1 次拜访" in r.json()["message"]
    assert client.delete(f"/stores/{s['id']}", headers=auth).json() == {"ok": True}
    assert [x["name"] for x in client.get("/stores", headers=auth).json()] == ["有拜访的店"]
    assert client.get(f"/stores/{s['id']}", headers=auth).status_code == 404
    assert client.get("/plans/today", headers=auth).json() == []
    assert all(t["id"] != todo["id"] for t in client.get("/todos", headers=auth).json())

    assert client.post(f"/admin/stores/{s['id']}/restore", headers=ADMIN).json() == {"ok": True}
    assert len(client.get("/stores", headers=auth).json()) == 2


def test_store_detail_flags_and_coordinates(client, auth):
    s = make_store(client, auth)
    assert s["canManage"] is True
    r = client.patch(f"/stores/{s['id']}", json={"name": "宝贝乐（新南路店）", "lat": 29.6, "lng": 106.5}, headers=auth).json()
    assert r["name"] == "宝贝乐（新南路店）" and r["lat"] == 29.6 and r["lng"] == 106.5
    assert client.patch(f"/stores/{s['id']}", json={"lat": 120}, headers=auth).status_code == 422
    v = make_visit(client, auth, s["id"], recordingMode="none", noRecordingReason="老板不在")
    assert client.get(f"/visits/{v['id']}", headers=auth).json()["canEdit"] is True
