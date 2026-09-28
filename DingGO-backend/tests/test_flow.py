from datetime import date

from app.services.due_date import parse_due

from .conftest import login
from .sample import ANALYSIS, TRANSCRIPT

ADMIN = {"X-Admin-Token": "test-admin"}


def make_store(client, auth, name="宝贝乐母婴店"):
    r = client.post("/stores", json={"name": name, "province": "重庆市", "city": "渝北区", "address": "新南路 88 号"}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def make_visit(client, auth, store_id, stage="首访破冰"):
    r = client.post("/visits", json={"storeId": store_id, "stage": stage, "cooperated": "否", "durationSec": 300}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def test_login_requires_token(client):
    assert client.get("/stores").status_code == 401
    assert client.get("/stores").json()["message"] == "请先登录"


def test_store_crud_and_correction(client, auth):
    s = make_store(client, auth)
    assert s["id"] and s["profile"] is None and s["visitCount"] == 0
    assert [x["name"] for x in client.get("/stores", headers=auth).json()] == ["宝贝乐母婴店"]
    assert client.post(f"/stores/{s['id']}/corrections", json={"text": "门店约 80 平"}, headers=auth).json() is True
    assert client.get(f"/stores/{s['id']}", headers=auth).json()["correction"] == "门店约 80 平"
    assert client.patch(f"/stores/{s['id']}", json={"cooperated": True}, headers=auth).json()["cooperated"] is True


def test_users_cannot_see_each_other(client, auth):
    s = make_store(client, auth)
    bob = login(client, "bob")
    assert client.get("/stores", headers=bob).json() == []
    assert client.get(f"/stores/{s['id']}", headers=bob).status_code == 404
    assert client.post("/visits", json={"storeId": s["id"], "stage": "首访破冰"}, headers=bob).status_code == 404


def test_visit_upload_cost_and_status(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    assert v["status"] == "uploading" and v["upload"]["uploadUrl"].endswith(f"/visits/{v['id']}/segments")
    assert client.post(f"/visits/{v['id']}/segments/complete", json={}, headers=auth).status_code == 400
    for i in range(2):
        r = client.post(f"/visits/{v['id']}/segments", data={"index": str(i)},
                        files={"file": (f"seg{i}.mp3", b"fake-audio-bytes", "audio/mpeg")}, headers=auth)
        assert r.status_code == 200, r.text
    assert client.post(f"/visits/{v['id']}/segments", files={"file": ("a.exe", b"x")}, headers=auth).status_code == 400
    done = client.post(f"/visits/{v['id']}/segments/complete", json={"durationSec": 3600}, headers=auth).json()
    assert done["status"] == "cost_pending" and done["durationSec"] == 3600 and done["estCost"] == 1.26
    assert len(done["audioUrls"]) == 2
    # 录音下载链接可用，篡改签名后失效
    url = done["audioUrl"].replace("http://localhost:8000", "")
    assert client.get(url).content == b"fake-audio-bytes"
    assert client.get(url[:-4] + "0000").status_code == 403
    confirmed = client.post(f"/visits/{v['id']}/confirm-cost", headers=auth).json()
    assert confirmed["status"] == "asr_running"
    assert client.post(f"/visits/{v['id']}/confirm-cost", headers=auth).status_code == 409
    assert client.post(f"/visits/{v['id']}/rejudge", headers=auth).status_code == 501


def test_bad_stage_rejected(client, auth):
    s = make_store(client, auth)
    r = client.post("/visits", json={"storeId": s["id"], "stage": "随便聊聊"}, headers=auth)
    assert r.status_code == 400 and "拜访阶段" in r.json()["message"]


def test_import_analysis_builds_profile_todos_and_panel(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    assert client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS}).status_code == 403
    r = client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS, "transcript": TRANSCRIPT}, headers=ADMIN)
    assert r.status_code == 200, r.text

    # 今日待办：复盘（未看过）、进店前简报；「明天」到期的待办不算今日到期
    panel = client.get(f"/assistant/today?storeId={s['id']}", headers=auth).json()
    titles = [x["title"] for x in panel["sections"]]
    assert "拜访复盘" in titles and "进店前" in titles
    review = next(x for x in panel["sections"] if x["title"] == "拜访复盘")["rows"][0]
    assert review["badge"] == "71分 B" and "问题影响与经济意义" in review["sub"]

    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["status"] == "done" and detail["moduleDone"] == 7
    assert detail["analysis"]["effectiveness"]["total"] == 71 and detail["transcript"][0]["uid"] == "U0001"
    # 看过详情后，复盘不再出现
    panel = client.get(f"/assistant/today?storeId={s['id']}", headers=auth).json()
    assert "拜访复盘" not in [x["title"] for x in panel["sections"]]

    store = client.get(f"/stores/{s['id']}", headers=auth).json()
    assert store["oneLine"] == "社区母婴店，担心线上比价。"
    assert len(store["profile"]["sections"]) == 7 and store["metrics"] == {"score": 71, "concernHits": 2, "loopRate": None}

    todos = client.get("/todos", headers=auth).json()
    assert [t["topic"] for t in todos] == ["补齐控价证据", "复访促成试销"]
    assert todos[0]["due"]["text"] == "明天到期" or todos[0]["due"]["days"] <= 1
    assert client.post(f"/todos/{todos[0]['id']}/done", headers=auth).json()["done"] is True
    assert client.post(f"/todos/{todos[0]['id']}/undo", headers=auth).json()["done"] is False
    assert client.post(f"/todos/{todos[0]['id']}/done", headers=login(client, "bob")).status_code == 404

    brief = client.get(f"/assistant/brief/{s['id']}", headers=auth).json()
    assert brief["isFirst"] is False and brief["concerns"] == ["控价防窜", "利润空间"]
    assert any("补全「经营模式」" in q for q in brief["questions"])
    assert [o["name"] for o in brief["objections"]] == ["控价防窜", "利润空间"]


def test_first_visit_brief_and_empty_panel(client, auth):
    s = make_store(client, auth)
    brief = client.get(f"/assistant/brief/{s['id']}", headers=auth).json()
    assert brief["isFirst"] is True and brief["opening"] and len(brief["questions"]) == 3
    panel = client.get("/assistant/today", headers=auth).json()
    assert panel["count"] == 0 and panel["hint"] == "今天暂无到期的跟进事项"


def test_ai_endpoints_not_ready(client, auth):
    assert client.post("/chat", json={"question": "hi"}, headers=auth).status_code == 501
    assert client.post("/practice/start", json={}, headers=auth).status_code == 501


def test_parse_due():
    base = date(2026, 9, 28)  # 周一
    assert parse_due("今天", base) == base
    assert parse_due("周五前", base) == date(2026, 10, 2)
    assert parse_due("下周二", base) == date(2026, 10, 6)
    assert parse_due("本周内", base) == date(2026, 10, 4)
    assert parse_due("3天内", base) == date(2026, 10, 1)
    assert parse_due("两周内", base) == date(2026, 10, 12)
    assert parse_due("10月8日", base) == date(2026, 10, 8)
    assert parse_due("1月5日前", base) == date(2027, 1, 5)
    assert parse_due("月底", base) == date(2026, 9, 30)
    assert parse_due("未明确时间/待确认", base) is None
