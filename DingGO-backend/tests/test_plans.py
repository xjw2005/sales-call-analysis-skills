"""今日计划：候选门店（按区、最近、到期约定）、确定计划、拜访后自动标为已去、新对话的主动问候"""

from datetime import date, datetime, timedelta, timezone

from .conftest import login
from .test_flow import make_store, make_visit


def ms_days_ago(n):
    return int((datetime.now(timezone.utc) - timedelta(days=n)).timestamp() * 1000)


def setup_stores(client, auth):
    a = make_store(client, auth, "老店A", district="渝中区")
    b = make_store(client, auth, "新店B", district="渝中区")
    c = make_store(client, auth, "远店C", district="九龙坡区")
    make_visit(client, auth, a["id"], stage="日常维护", cooperated=None, enteredAt=ms_days_ago(20))
    return a, b, c


def test_greeting_asks_where_and_lists_districts(client, auth):
    setup_stores(client, auth)
    g = client.get("/plans/greeting", headers=auth).json()
    assert g["mode"] == "ask" and g["plan"] == []
    labels = [o["label"] for o in g["options"]]
    assert {"渝中区", "九龙坡区", "最近拜访过的店", "我承诺过的事"} <= set(labels)
    assert next(o for o in g["options"] if o["label"] == "渝中区")["district"] == "渝中区"


def test_district_candidates_ranked_and_plan_flow(client, auth):
    a, b, c = setup_stores(client, auth)
    res = client.get("/plans/suggest?kind=district&district=渝中区", headers=auth).json()
    assert [x["name"] for x in res["items"]] == ["新店B", "老店A"]  # 从未拜访排在 20 天前去过的前面
    assert res["items"][0]["reason"] == "从未拜访" and "20 天没去了" in res["items"][1]["reason"]
    assert client.get("/plans/suggest?kind=district", headers=auth).status_code == 400
    assert client.get("/plans/suggest?kind=other", headers=auth).status_code == 400

    plan = client.post("/plans", json={"storeIds": [a["id"], b["id"]], "source": "district", "reasons": {b["id"]: "从未拜访"}}, headers=auth).json()
    assert [p["name"] for p in plan] == ["老店A", "新店B"] and plan[1]["reason"] == "从未拜访" and plan[0]["status"] == "planned"
    again = client.post("/plans", json={"storeIds": [a["id"]], "source": "district"}, headers=auth).json()
    assert len(again) == 2  # 重复加入不会多出一条

    g = client.get("/plans/greeting", headers=auth).json()
    assert g["mode"] == "plan" and "计划去 2 家店，已完成 0 家" in g["text"]

    # 到店录音（创建拜访）后，计划自动标为已去
    v = make_visit(client, auth, b["id"], stage="首访破冰")
    plan = client.get("/plans/today", headers=auth).json()
    assert [p["status"] for p in plan] == ["planned", "visited"] and plan[1]["visitId"] == v["id"]
    panel = client.get("/assistant/today", headers=auth).json()
    assert [s["title"] for s in panel["sections"]][0] == "今日计划" and panel["count"] == 1
    assert client.delete(f"/plans/{plan[0]['id']}", headers=auth).json() is True
    assert client.delete(f"/plans/{plan[0]['id']}", headers=auth).status_code == 404


def test_commitments_become_candidates_and_greeting(client, auth):
    a, b, c = setup_stores(client, auth)
    soon = (date.today() + timedelta(days=1)).isoformat()
    later = (date.today() + timedelta(days=20)).isoformat()
    assert client.post("/todos", json={"topic": "带空罐去陈列", "storeId": a["id"], "dueDate": soon}, headers=auth).status_code == 200
    client.post("/todos", json={"topic": "很久以后的事", "storeId": c["id"], "dueDate": later}, headers=auth)
    res = client.get("/plans/suggest?kind=commitments", headers=auth).json()
    assert [x["name"] for x in res["items"]] == ["老店A"] and "带空罐去陈列" in res["items"][0]["reason"] and res["items"][0]["checked"] is True
    g = client.get("/plans/greeting", headers=auth).json()
    assert g["mode"] == "commitments" and "老店A" in g["text"]
    # 渝中区候选里，有约定的店排最前
    top = client.get("/plans/suggest?kind=district&district=渝中区", headers=auth).json()["items"][0]
    assert top["name"] == "老店A" and "有约定" in top["tags"]
    panel = client.get("/assistant/today", headers=auth).json()
    assert [s["title"] for s in panel["sections"]] == ["到期的约定和跟进"]


def test_recent_and_permissions(client, auth):
    a, b, c = setup_stores(client, auth)
    make_visit(client, auth, c["id"], stage="日常维护", cooperated=None, enteredAt=ms_days_ago(2))
    recent = client.get("/plans/suggest?kind=recent", headers=auth).json()["items"]
    assert [x["name"] for x in recent] == ["远店C"]  # 只统计 14 天内去过的
    bob = login(client, "bob", "小李")
    assert client.get("/plans/suggest?kind=district&district=渝中区", headers=bob).json()["items"] == []
    added = client.post("/plans", json={"storeIds": [a["id"]], "source": "manual"}, headers=bob).json()
    assert added == []  # 看不到的门店不能加入自己的计划
    assert client.get("/plans/today", headers=bob).json() == []
    assert client.post("/plans", json={"storeIds": [a["id"]], "source": "bad"}, headers=auth).status_code == 400
