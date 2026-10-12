"""位置与「附近的店」：距离计算、按距离推荐、门店位置设置、助手按位置找店（位置不给模型、不入库）"""

import json

from app.ai import core
from app.db import SessionLocal
from app.models import ChatLog, ChatMessage
from app.services import geo

from .conftest import login
from .test_flow import make_store

# 重庆解放碑附近。纬度差 0.01 度约 1.11 公里
HERE = (29.5630, 106.5515)


def stores_around(client, auth):
    near = make_store(client, auth, "近店", district="渝中区", lat=HERE[0] + 0.005, lng=HERE[1])  # ≈0.56 公里
    mid = make_store(client, auth, "中店", district="江北区", lat=HERE[0] + 0.05, lng=HERE[1])  # ≈5.6 公里
    far = make_store(client, auth, "远店", district="渝北区", lat=HERE[0] + 0.2, lng=HERE[1])  # ≈22 公里
    nowhere = make_store(client, auth, "没位置的店", district="南岸区")
    return near, mid, far, nowhere


def suggest(client, auth, lat=HERE[0], lng=HERE[1], kind="nearby"):
    q = f"/plans/suggest?kind={kind}" + (f"&lat={lat}&lng={lng}" if lat is not None else "")
    return client.get(q, headers=auth)


# ---------- 距离计算 ----------
def test_geo_helpers():
    assert abs(geo.haversine_km(*HERE, HERE[0] + 0.01, HERE[1]) - 1.11) < 0.02
    assert geo.haversine_km(*HERE, *HERE) == 0
    assert geo.distance_text(0.32) == "320 米" and geo.distance_text(0.004) == "10 米"
    assert geo.distance_text(1.234) == "1.2 公里" and geo.distance_text(150.4) == "150 公里"
    assert geo.valid_point(29.5, 106.5) and not geo.valid_point(0, 0) and not geo.valid_point(91, 10) and not geo.valid_point("x", 1) and not geo.valid_point(float("nan"), 1)
    assert geo.in_china(29.5, 106.5) and not geo.in_china(40.7, -74.0) and not geo.in_china(106.5, 29.5)  # 经纬度填反了也要拦住


# ---------- 推荐近的店 ----------
def test_nearby_sorted_by_distance_with_missing_count(client, auth):
    near, mid, far, _ = stores_around(client, auth)
    r = suggest(client, auth).json()
    assert [c["name"] for c in r["items"]] == ["近店", "中店", "远店"]
    assert [c["distanceKm"] for c in r["items"]] == sorted(c["distanceKm"] for c in r["items"])
    assert r["items"][0]["reason"].startswith("距你 560 米") and r["items"][1]["reason"].startswith("距你 5.6 公里")
    assert r["missing"] == 1 and r["located"] == 3 and "最远 22.2 公里" in r["title"]
    assert "从未拜访" in r["items"][0]["reason"]  # 距离之外，原来的「几天没去了」等信息还在


def test_nearby_needs_a_location_and_validates_it(client, auth):
    stores_around(client, auth)
    assert suggest(client, auth, lat=None).status_code == 400
    assert "位置" in suggest(client, auth, lat=None).json()["message"]
    assert suggest(client, auth, lat=95, lng=106).status_code == 422
    assert suggest(client, auth, lat=29.5, lng=181).status_code == 422


def test_nearby_when_no_store_has_a_position_says_so(client, auth):
    make_store(client, auth, "只有地址的店", address="某街 1 号")
    r = suggest(client, auth).json()
    assert r["items"] == [] and r["missing"] == 1 and r["located"] == 0 and "没有门店记录位置" in r["title"]


def test_nearby_only_includes_stores_i_can_see(client, auth):
    stores_around(client, auth)
    bob = login(client, "bob", "小李")
    make_store(client, bob, "小李的店", lat=HERE[0] + 0.001, lng=HERE[1])
    assert [c["name"] for c in suggest(client, bob).json()["items"]] == ["小李的店"]  # 看不到别人的店
    assert "小李的店" not in [c["name"] for c in suggest(client, auth).json()["items"]]


def test_greeting_offers_nearby_only_when_some_store_has_a_position(client, auth):
    make_store(client, auth, "没位置", district="渝中区")
    assert "nearby" not in [o["kind"] for o in client.get("/plans/greeting", headers=auth).json()["options"]]
    make_store(client, auth, "有位置", district="渝中区", lat=HERE[0], lng=HERE[1])
    opts = client.get("/plans/greeting", headers=auth).json()["options"]
    assert {"label": "离我近的店", "kind": "nearby"} in opts


def test_nearby_candidates_can_be_added_to_the_plan(client, auth):
    near, *_ = stores_around(client, auth)
    r = client.post("/plans", json={"storeIds": [near["id"]], "source": "nearby", "reasons": {near["id"]: "距你 560 米"}}, headers=auth)
    assert r.status_code == 200 and r.json()[0]["source"] == "nearby"  # 来源要被接受（以前漏过 search / chat，会报「来源不正确」）


# ---------- 门店位置 ----------
def test_set_store_location_permissions_and_validation(client, auth):
    s = make_store(client, auth, "甲店")
    assert s["hasLocation"] is False
    r = client.post(f"/stores/{s['id']}/location", json={"lat": 29.56, "lng": 106.55}, headers=auth)
    assert r.status_code == 200 and r.json()["lat"] == 29.56 and r.json()["geoSource"] == "manual" and r.json()["hasLocation"] is True
    assert client.get("/stores", headers=auth).json()[0]["hasLocation"] is True
    assert "lat" not in client.get("/stores", headers=auth).json()[0]  # 列表只带有没有位置，不带坐标
    bob = login(client, "bob", "小李")
    assert client.post(f"/stores/{s['id']}/location", json={"lat": 29.0, "lng": 106.0}, headers=bob).status_code == 404  # 别人的店
    for bad in ({"lat": 40.7, "lng": -74.0}, {"lat": 0, "lng": 0}):
        assert client.post(f"/stores/{s['id']}/location", json=bad, headers=auth).status_code == 400
    assert client.post(f"/stores/{s['id']}/location", json={"lat": 100, "lng": 106}, headers=auth).status_code == 422


def test_create_store_with_map_pick_keeps_the_position(client, auth):
    s = make_store(client, auth, "地图选点的店", lat=29.56, lng=106.55)
    assert s["lat"] == 29.56 and s["geoSource"] == "manual"
    for bad in ({"lat": 29.56}, {"lat": 40.7, "lng": -74.0}):
        r = client.post("/stores", json={"name": "坏位置", **bad}, headers=auth)
        assert r.status_code == 400, bad  # 经纬度要成对、要在国内


# ---------- 助手按位置找店 ----------
def scripted(monkeypatch, steps, seen):
    """依次返回 steps 里的动作；seen 记下发给模型的每一次提示词（检查里面没有坐标）"""
    it = iter(steps)

    def fake(system, user, model, temperature):
        seen.append(system + "\n" + user)
        return json.dumps(next(it), ensure_ascii=False), {}

    monkeypatch.setattr(core, "call_llm", fake)


NEAR_STEPS = [
    {"action": "tool", "tool": "search_stores", "args": {"sort": "distance", "near": True, "radius_km": 10}},
    {"action": "show_stores", "reply": "离你最近的店我挑好了"},
]


def test_assistant_finds_nearby_stores_without_exposing_coordinates(client, auth, monkeypatch):
    stores_around(client, auth)
    seen = []
    scripted(monkeypatch, NEAR_STEPS, seen)
    r = client.post("/chat", json={"question": "我附近有哪些店", "location": {"lat": HERE[0], "lng": HERE[1]}}, headers=auth).json()
    assert [c["name"] for c in r["cands"]["items"]] == ["近店", "中店"]  # 10 公里内，远店不要
    assert r["cands"]["items"][0]["distanceKm"] < 1 and "needLocation" not in r
    blob = "\n".join(seen)
    assert "销售当前位置：已提供" in blob
    assert "29.56" not in blob and "106.55" not in blob  # 坐标不给模型
    with SessionLocal() as db:  # 也不写进对话记录和日志
        stored = json.dumps([m.payload for m in db.query(ChatMessage)] + [t.tools for t in db.query(ChatLog)], ensure_ascii=False)
        assert "29.56" not in stored and "106.55" not in stored
        assert all("location" not in json.dumps(m.text or "") for m in db.query(ChatMessage))


def test_assistant_without_location_asks_the_app_to_request_it(client, auth, monkeypatch):
    stores_around(client, auth)
    seen = []
    scripted(monkeypatch, [NEAR_STEPS[0], {"action": "answer", "reply": "需要先允许获取位置，点下面的按钮", "suggestions": []}], seen)
    r = client.post("/chat", json={"question": "我附近有哪些店"}, headers=auth).json()
    assert r["needLocation"] is True and "cands" not in r
    assert "销售当前位置：没有提供" in seen[0]
    with SessionLocal() as db:
        assert db.query(ChatLog).one().tools[0]["ok"] is False


def test_chat_rejects_invalid_location(client, auth, monkeypatch):
    scripted(monkeypatch, [{"action": "answer", "reply": "好"}], [])
    assert client.post("/chat", json={"question": "你好", "location": {"lat": 200, "lng": 1}}, headers=auth).status_code == 422
    ok = client.post("/chat", json={"question": "你好", "location": {"lat": 0, "lng": 0}}, headers=auth)
    assert ok.status_code == 200  # 没定位上时常见的 (0,0)：当作没有位置，不报错


def test_search_by_distance_requires_a_position_in_the_service(client, auth):
    import pytest

    from app.models import User
    from app.services import plans
    stores_around(client, auth)
    with SessionLocal() as db:
        user = db.query(User).first()
        with pytest.raises(ValueError):
            plans.search(db, user, sort="distance")
        items = plans.search(db, user, sort="distance", near=HERE, radius_km=1)
        assert [c["name"] for c in items] == ["近店"]  # 半径限定
        keep_order = plans.search(db, user, near=HERE)  # 只带上距离，不改顺序也不过滤
        assert {c["name"] for c in keep_order} == {"近店", "中店", "远店", "没位置的店"}
        assert all(("distanceKm" in c) == (c["name"] != "没位置的店") for c in keep_order)  # 没位置的店没有距离，其余都有
