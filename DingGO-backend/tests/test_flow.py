from datetime import date

from app.services.due_date import parse_due

from .conftest import ADMIN, login, login_user
from .sample import ANALYSIS, TRANSCRIPT


def make_store(client, auth, name="宝贝乐母婴店", **extra):
    r = client.post("/stores", json={"name": name, "province": "重庆市", "city": "渝北区", "address": "新南路 88 号", **extra}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def make_visit(client, auth, store_id, stage="首访破冰", **extra):
    r = client.post("/visits", json={"storeId": store_id, "stage": stage, "cooperated": "否", "durationSec": 300, **extra}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def test_login_requires_token(client):
    assert client.get("/stores").status_code == 401
    assert client.get("/stores").json()["message"] == "请先登录"


def test_store_crud_status_and_correction(client, auth):
    s = make_store(client, auth)
    assert s["id"] and s["profile"] is None and s["visitCount"] == 0
    assert s["cooperationStatus"] == "未触达" and s["cooperated"] is False
    assert [x["name"] for x in client.get("/stores", headers=auth).json()] == ["宝贝乐母婴店"]
    assert client.post(f"/stores/{s['id']}/corrections", json={"text": "门店约 80 平"}, headers=auth).json() is True
    assert client.get(f"/stores/{s['id']}", headers=auth).json()["correction"] == "门店约 80 平"
    r = client.patch(f"/stores/{s['id']}", json={"cooperationStatus": "已合作"}, headers=auth).json()
    assert r["cooperationStatus"] == "已合作" and r["cooperated"] is True
    bad = client.patch(f"/stores/{s['id']}", json={"cooperationStatus": "随便"}, headers=auth)
    assert bad.status_code == 400 and "合作状态" in bad.json()["message"]


def test_store_list_is_slim(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS}, headers=ADMIN)
    item = client.get("/stores", headers=auth).json()[0]
    assert item["oneLine"] and item["hasProfile"] is True and item["metrics"]["score"] == 71
    assert "profile" not in item and "actions" not in item and "loops" not in item
    detail = client.get(f"/stores/{s['id']}", headers=auth).json()
    assert len(detail["profile"]["sections"]) == 7 and len(detail["actions"]) == 2


def test_contact_phone_hidden_in_list_but_in_detail(client, auth):
    s = make_store(client, auth, contactName="王老板", contactPhone="13800000000")
    assert "contactPhone" not in client.get("/stores", headers=auth).json()[0]
    assert client.get(f"/stores/{s['id']}", headers=auth).json()["contactPhone"] == "13800000000"


def test_platform_external_id_unique_and_directory_link(client, auth):
    from app.db import SessionLocal
    from app.models import StoreDirectory

    with SessionLocal() as db:
        db.add(StoreDirectory(platform="西港", external_id="9001", name="宝贝乐", province="重庆市", city="渝北区", district="龙溪", address="新南路", grid="A1"))
        db.commit()
    s = client.post("/stores", json={"name": "宝贝乐母婴店", "platform": "西港", "externalId": "9001"}, headers=auth).json()
    assert s["province"] == "重庆市" and s["district"] == "龙溪" and s["grid"] == "A1"  # 用总门店清单补全
    dup = client.post("/stores", json={"name": "另一家", "platform": "西港", "externalId": "9001"}, headers=auth)
    assert dup.status_code == 409


def test_users_cannot_see_each_other(client, auth):
    s = make_store(client, auth)
    bob = login(client, "bob")
    assert client.get("/stores", headers=bob).json() == []
    assert client.get(f"/stores/{s['id']}", headers=bob).status_code == 404
    assert client.post("/visits", json={"storeId": s["id"], "stage": "首访破冰"}, headers=bob).status_code == 404


def test_manager_sees_team_and_visitor_sees_visited_store(client, auth):
    mgr_h, mgr = login_user(client, "mgr", "经理")
    alice_h, alice = auth, client.get("/auth/me", headers=auth).json()
    assert client.patch(f"/admin/users/{mgr['id']}", json={"role": "manager"}, headers=ADMIN).status_code == 200
    assert client.patch(f"/admin/users/{alice['id']}", json={"managerId": mgr["id"]}, headers=ADMIN).status_code == 200
    s = make_store(client, alice_h)
    v = make_visit(client, alice_h, s["id"])
    # 经理能看到下属的门店、拜访，拜访记录里带着拜访人姓名
    assert [x["id"] for x in client.get("/stores", headers=mgr_h).json()] == [s["id"]]
    visits = client.get("/visits", headers=mgr_h).json()
    assert [x["id"] for x in visits] == [v["id"]] and visits[0]["visitorName"] == "小熊"
    # 经理指派待办给下属
    t = client.post("/todos", json={"topic": "月中回访", "assigneeId": alice["id"], "storeId": s["id"], "dueDate": "2026-10-10"}, headers=mgr_h)
    assert t.status_code == 200 and t.json()["source"] == "manager" and t.json()["assigneeName"] == "小熊"
    assert [x["topic"] for x in client.get("/todos", headers=alice_h).json()] == ["月中回访"]
    assert client.get("/todos", headers=mgr_h).json() == []  # 默认只看自己的
    assert len(client.get("/todos?scope=team", headers=mgr_h).json()) == 1
    # 普通销售不能指派别人
    assert client.post("/todos", json={"topic": "x", "assigneeId": mgr["id"]}, headers=alice_h).status_code == 403
    # 第三个人（无关系）什么都看不到；别人拜访过我的店：我（店主）能看到那条拜访
    other = login(client, "other", "路人")
    assert client.get("/stores", headers=other).json() == []
    assert client.get(f"/visits?storeId={s['id']}", headers=other).json() == []


def test_visit_upload_cost_and_status(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    assert v["status"] == "uploading" and v["upload"]["uploadUrl"].endswith(f"/visits/{v['id']}/segments")
    assert v["visitorName"] == "小熊" and v["recordingMode"] == "uploaded"
    assert client.post(f"/visits/{v['id']}/segments/complete", json={}, headers=auth).status_code == 400
    for i in range(2):
        r = client.post(f"/visits/{v['id']}/segments", data={"index": str(i)},
                        files={"file": (f"seg{i}.mp3", b"fake-audio-bytes", "audio/mpeg")}, headers=auth)
        assert r.status_code == 200, r.text
    assert client.post(f"/visits/{v['id']}/segments", files={"file": ("a.exe", b"x")}, headers=auth).status_code == 400
    assert client.post(f"/visits/{v['id']}/segments", data={"index": "2"}, files={"file": ("c.ogg", b"ogg-bytes")}, headers=auth).status_code == 200
    done = client.post(f"/visits/{v['id']}/segments/complete", json={"durationSec": 3600}, headers=auth).json()
    assert done["status"] == "cost_pending" and done["durationSec"] == 3600 and done["estCost"] == 1.26
    assert len(done["audioUrls"]) == 3
    url = done["audioUrl"].replace("http://localhost:8000", "")
    assert client.get(url).content == b"fake-audio-bytes"
    assert client.get(url[:-4] + "0000").status_code == 403
    assert client.post(f"/visits/{v['id']}/confirm-cost", headers=auth).json()["status"] == "asr_running"
    assert client.post(f"/visits/{v['id']}/confirm-cost", headers=auth).status_code == 409
    assert client.post(f"/visits/{v['id']}/rejudge", headers=auth).status_code == 409  # 还没被判无效，不能重新判定


def test_visit_without_recording(client, auth):
    s = make_store(client, auth)
    no_reason = client.post("/visits", json={"storeId": s["id"], "stage": "日常维护", "recordingMode": "none"}, headers=auth)
    assert no_reason.status_code == 400 and "原因" in no_reason.json()["message"]
    v = make_visit(client, auth, s["id"], stage="日常维护", recordingMode="none", noRecordingReason="店内太吵，不方便录音",
                   storeCondition="正常运营", purposes=["催动销", "陈列维护"], businessLine="跨境招商",
                   note="老板在店，聊了动销", survey={"area": "30-50平", "monthlyTotal": 120},
                   checkin={"address": "重庆市渝北区", "lat": 29.6, "lng": 106.5})
    assert v["status"] == "no_recording" and "upload" not in v
    assert v["storeCondition"] == "正常运营" and v["purposes"] == ["催动销", "陈列维护"] and v["businessLine"] == "跨境招商"
    assert v["survey"]["area"] == "30-50平" and v["checkin"]["lat"] == 29.6 and v["noRecordingReason"].startswith("店内")
    assert client.post(f"/visits/{v['id']}/segments", files={"file": ("a.mp3", b"x")}, headers=auth).status_code == 409
    upd = client.patch(f"/visits/{v['id']}", json={"note": "补充：下周再聊"}, headers=auth).json()
    assert upd["note"] == "补充：下周再聊"
    assert client.patch(f"/visits/{v['id']}", json={"storeCondition": "随便"}, headers=auth).status_code == 400


def test_bad_inputs_rejected(client, auth):
    s = make_store(client, auth)
    r = client.post("/visits", json={"storeId": s["id"], "stage": "随便聊聊"}, headers=auth)
    assert r.status_code == 400 and "拜访阶段" in r.json()["message"]
    assert client.post("/visits", json={"storeId": s["id"], "stage": "首访破冰", "businessLine": "乱写"}, headers=auth).status_code == 400
    assert client.post("/visits", json={"storeId": s["id"], "stage": "首访破冰", "enteredAt": 4102444800000}, headers=auth).status_code == 400


def test_import_analysis_builds_profile_todos_and_panel(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    assert client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS}).status_code == 403
    r = client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS, "transcript": TRANSCRIPT}, headers=ADMIN)
    assert r.status_code == 200, r.text

    panel = client.get(f"/assistant/today?storeId={s['id']}", headers=auth).json()
    titles = [x["title"] for x in panel["sections"]]
    assert "拜访复盘" in titles and "进店前" in titles
    review = next(x for x in panel["sections"] if x["title"] == "拜访复盘")["rows"][0]
    assert review["badge"] == "71分 B" and "问题影响与经济意义" in review["sub"]

    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["status"] == "done" and detail["moduleDone"] == 7
    assert detail["analysis"]["effectiveness"]["total"] == 71 and detail["transcript"][0]["uid"] == "U0001"
    panel = client.get(f"/assistant/today?storeId={s['id']}", headers=auth).json()
    assert "拜访复盘" not in [x["title"] for x in panel["sections"]]

    store = client.get(f"/stores/{s['id']}", headers=auth).json()
    assert store["oneLine"] == "社区母婴店，担心线上比价。"
    assert len(store["profile"]["sections"]) == 7 and store["metrics"] == {"score": 71, "concernHits": 2, "loopRate": None}

    todos = client.get("/todos", headers=auth).json()
    assert [t["topic"] for t in todos] == ["补齐控价证据", "复访促成试销"]
    assert todos[0]["source"] == "ai" and todos[0]["assigneeName"] == "小熊" and todos[0]["due"]["days"] <= 1
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


def test_first_visit_marks_store_cooperated(client, auth):
    s = make_store(client, auth)
    v = client.post("/visits", json={"storeId": s["id"], "stage": "首访破冰", "cooperated": "是"}, headers=auth).json()
    client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS}, headers=ADMIN)
    assert client.get(f"/stores/{s['id']}", headers=auth).json()["cooperationStatus"] == "已合作"


def test_correct_analysis_keeps_original_and_logs_event(client, auth):
    from app.db import SessionLocal
    from app.models import CorrectionEvent, VisitAnalysis

    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS}, headers=ADMIN)
    new_eff = {**ANALYSIS["effectiveness"], "total": 80}
    r = client.patch(f"/visits/{v['id']}/analysis/effectiveness", json={"result": new_eff}, headers=auth)
    assert r.status_code == 200 and r.json()["analysis"]["effectiveness"]["total"] == 80  # 展示改后的
    with SessionLocal() as db:
        row = db.query(VisitAnalysis).filter_by(visit_id=int(v["id"]), module="effectiveness").one()
        assert row.result["total"] == 71 and row.corrected_result["total"] == 80 and row.corrected_by is not None  # 原始结果保留
        ev = db.query(CorrectionEvent).one()
        assert ev.target_type == "analysis" and ev.before["total"] == 71 and ev.after["total"] == 80
    assert client.patch(f"/visits/{v['id']}/analysis/effectiveness", json={"result": new_eff}, headers=login(client, "bob")).status_code == 404
    assert client.patch(f"/visits/{v['id']}/analysis/nope", json={"result": {}}, headers=auth).status_code == 404


def test_edit_profile_section_logs_event(client, auth):
    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"])
    client.post(f"/admin/visits/{v['id']}/analysis", json={"analysis": ANALYSIS}, headers=ADMIN)
    r = client.patch(f"/stores/{s['id']}/profile/business_model", json={"content": "自己经营，夫妻店", "state": "当前状态"}, headers=auth).json()
    bm = next(x for x in r["profile"]["sections"] if x["key"] == "business_model")
    assert bm["content"] == "自己经营，夫妻店" and bm["state"] == "当前状态"
    assert client.patch(f"/stores/{s['id']}/profile/nope", json={"content": "x"}, headers=auth).status_code == 404
    assert client.patch(f"/stores/{s['id']}/profile/basic", json={"content": "x", "state": "乱写"}, headers=auth).status_code == 400


def test_legacy_visit_shows_raw_text(client, auth):
    """从飞书导入的拜访：分析是原文，不是结构化数据；不进入仪表和复盘提醒"""
    from app.db import SessionLocal
    from app.models import Visit
    from app.services.ingest import upsert_module, upsert_transcript

    s = make_store(client, auth)
    v = make_visit(client, auth, s["id"], stage="日常维护")
    with SessionLocal() as db:
        visit = db.get(Visit, int(v["id"]))
        visit.legacy, visit.status = True, "done"
        upsert_module(db, visit.id, "explicit-needs", {"text": "1. 需求点：想要陈列支持"}, evidence="[00:11] 客户：陈列")
        upsert_module(db, visit.id, "next-action", {"text": "周五前送陈列物料"})
        upsert_transcript(db, visit.id, [], raw_text="[00:11.450–00:12.290] 客户：陈列", source="import")
        db.commit()
    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["legacy"] is True and [x["title"] for x in detail["analysis"]["legacySections"]] == ["显性需求", "下一步行动策略"]
    assert detail["analysis"]["legacySections"][0]["text"].startswith("1. 需求点") and detail["transcriptRaw"].startswith("[00:11")
    lst = client.get("/visits", headers=auth).json()
    assert lst[0]["legacy"] is True and lst[0]["analysis"]["legacySections"] == []  # 列表不带大段原文
    st = client.get(f"/stores/{s['id']}", headers=auth).json()
    assert st["metrics"]["concernHits"] is None and st["visitCount"] == 1
    panel = client.get("/assistant/today", headers=auth).json()
    assert "拜访复盘" not in [x["title"] for x in panel["sections"]]
    assert client.patch(f"/visits/{v['id']}/analysis/explicit-needs", json={"result": {}}, headers=auth).status_code == 400


def test_todo_targets_and_patch(client, auth):
    s = make_store(client, auth)
    t = client.post("/todos", json={"topic": "9月任务拆解", "storeId": s["id"], "targetQty": 60, "unit": "听", "period": "2026-09"}, headers=auth).json()
    assert t["source"] == "self" and t["targetQty"] == 60 and t["gapQty"] is None and t["due"]["text"] == "未定日期"
    r = client.patch(f"/todos/{t['id']}", json={"achievedQty": 36, "progressNote": "月中下单", "dueDate": "2026-09-30"}, headers=auth).json()
    assert r["gapQty"] == 24 and r["progressNote"] == "月中下单" and r["dueDate"]
    assert client.patch(f"/todos/{t['id']}", json={"status": "cancelled"}, headers=auth).json()["status"] == "cancelled"
    assert client.get("/todos", headers=auth).json() == []  # 已取消的不显示
    assert client.patch(f"/todos/{t['id']}", json={"status": "乱写"}, headers=auth).status_code == 400


def test_merge_legacy_user_with_wechat_account(client):
    old = client.post("/admin/users", json={"name": "赵老板", "role": "manager"}, headers=ADMIN).json()
    assert client.get("/admin/users", headers=ADMIN).json()[0]["bound"] is False
    new_h, new = login_user(client, "zhao-wx", "赵")
    r = client.post("/admin/users/merge", json={"fromId": new["id"], "intoId": old["id"]}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["name"] == "赵老板"
    # 之后用同一个微信登录，进的是旧人员账号
    again = client.post("/auth/wx-login", json={"code": "zhao-wx"}).json()["user"]
    assert again["id"] == old["id"] and again["role"] == "manager"
    # 已经有业务数据的账号不能被合并
    h2, u2 = login_user(client, "busy", "忙")
    client.post("/stores", json={"name": "店"}, headers=h2)
    old2 = client.post("/admin/users", json={"name": "李"}, headers=ADMIN).json()
    assert client.post("/admin/users/merge", json={"fromId": u2["id"], "intoId": old2["id"]}, headers=ADMIN).status_code == 409


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
