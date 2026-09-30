"""AI 录音链路测试：用桩替换火山 LAS 和大模型，走完 确认费用 → 转写 → 角色 → 有效性 → 分析 → 落库。
不联网、不花钱；只验证状态机、防重复提交、校验与落库的正确性。"""

import json

import pytest
from sqlalchemy import select

from app.ai import core, transcribe, worker
from app.db import SessionLocal
from app.models import Store, StoreProfileSection, Todo, Visit, VisitAnalysis, VisitPipeline, VisitSegment, VisitTranscript

from .conftest import login_user
from .test_flow import make_store, make_visit

LINES = [  # (说话人, 文本)：奇数句销售，偶数句客户
    ("0", "老板您好，我是 A2 奶粉的业务，今天想了解一下店里奶粉卖得怎么样，占用您十分钟。"),
    ("1", "你说吧，现在奶粉不好卖，出生率下来了，一个月也就走个三四十罐。"),
    ("0", "三四十罐主要是哪些牌子在走？"),
    ("1", "飞鹤、君乐宝走得多，进口的就爱他美偶尔有人要。进口的价格太透明了，网上一比我就没利润。"),
    ("0", "明白，您最担心的是线上比价把利润打穿，我们首单可以只拿两箱试销，卖不动的一个月内原价退。"),
    ("1", "能退的话可以考虑。那一罐我能赚多少？还有到货要几天？"),
    ("0", "单罐毛利大概六十到七十，本市仓发货，下单第二天到。"),
    ("1", "这个利润还行。你周五把价格表和退货政策发我看看，我跟我老婆商量一下。"),
]


def las_payload():
    utterances = [{"text": t, "start_time": i * 12000, "end_time": i * 12000 + 9000, "additions": {"speaker": s}} for i, (s, t) in enumerate(LINES)]
    return {"data": {"result": {"text": "".join(t for _, t in LINES), "utterances": utterances}, "business_code": "0", "task_status": "COMPLETED"}}


class FakeLas:
    def __init__(self):
        self.uploads, self.submits, self.polls = 0, 0, 0

    def upload(self, path):
        self.uploads += 1
        return "http://tos.invalid/audio"

    def submit(self, url, audio_format):
        self.submits += 1
        return {"data": {"task_id": f"T{self.submits}", "task_status": "PENDING"}}

    def poll(self, task_id):
        self.polls += 1
        return las_payload() | {"task_status": "COMPLETED"}


SCOPE = {"input_quality": "可用", "quality_reason": "", "role_corrections": [], "exclusions": []}
DIMS = [("开场目标与议程", 10, 8), ("需求挖掘", 20, 14), ("问题影响与经济意义", 20, 11), ("倾听与异议承接", 15, 12),
        ("方案与证据匹配", 10, 7), ("价格与价值建立", 10, 7), ("达成合作提问", 5, 3), ("下一步承诺", 10, 9)]
CONCLUSION = "整体推进较顺，开场目标清晰，准确抓住了客户对线上比价和压货的顾虑，并用小量试销加退货政策承接异议。核心缺口在于价格机制只停留在口头说明，尚未给出可验证的案例，客户信任还没有建立起来。"


def candidate(module, mode="first", user_msg=""):
    if module == "explicit-needs":
        v = {"items": [{"need": "首单可退、控制压货风险", "evidence_ids": ["U0006"], "scene": "销售提出试销方案后", "explanation": "客户表示能退的话可以考虑，退货是合作前提。"}]}
    elif module == "implicit-needs":
        v = {"items": []}
    elif module == "concerns":
        names = ["价格敏感", "物流时效", "控价防窜", "培训支持", "售后保障", "合作模式", "利润空间", "系统工具", "其他"]
        cats = []
        for n in names:
            on = n in ("价格敏感", "物流时效")
            cats.append({"name": n, "status": "是" if on else "否", "subtype": "", "evidence_ids": (["U0004"] if n == "价格敏感" else ["U0006"]) if on else [],
                         "scene": "谈到进口奶粉时" if on else "", "object": "进口奶粉价格" if on else "", "reason": "客户直接表达" if on else ""})
        v = {"categories": cats}
    elif module == "effectiveness":
        v = {"scores": [{"name": n, "score": s, "judgment": "有明确表现", "evidence_ids": ["U0001"]} for n, _, s in DIMS], "conclusion": CONCLUSION,
             "sales_result": "客户待定，约定周五发资料。"}
    elif module == "quotes":
        v = {"items": []}
    elif module == "store-profile":
        secs = {k: {"content": "未确认", "state_type": "未确认", "evidence_ids": []} for k in core.PROFILE_SECTIONS}
        secs["price_profit"] = {"content": "单罐利润认可，担心线上比价。", "state_type": "当前状态", "evidence_ids": ["U0004"]}
        v = {"one_line": "未确认", "one_line_evidence_ids": [], "sections": secs}
    else:  # next-action
        acts = {"confirmed_actions": [{"owner": "销售", "timeframe": "周五", "action": "把价格表和退货政策发给客户", "evidence_ids": ["U0007", "U0008"]}],
                "recommended_actions": [{"topic": "补齐价格证据", "owner": "销售", "timeframe": "周五前", "action": "整理近三个月的价格处理案例一起发送",
                                         "reason": "客户担心线上比价", "acceptance": "客户收到并回复", "evidence_ids": ["U0004"]}]}
        if mode == "first":
            v = {"cooperation_status": "未合作", "status_evidence_ids": [], "action_judgment": "触发", "judgment_reason": "客户索要价格表并约定后续沟通。", **acts,
                 "second_visit": {"value": "值得", "reason": "客户利润认可，复访有望促成首单。", "evidence_ids": ["U0008"]}}
        else:
            items = [{"action": "周五发送价格表和退货政策", "done": "已完成", "evidence_ids": ["U0002"], "reason": ""}] if "上次下一步行动策略" in user_msg else []
            v = {"closure": {"summary": "本次沟通了试销与退货安排。", "items": items}, "action_judgment": "触发", "judgment_reason": "客户索要价格表。", **acts,
                 "second_visit": {"value": "不适用", "reason": "日常拜访", "evidence_ids": []}}
    return {"_scope": SCOPE, module: v}


def fake_llm(system, user, model, temperature):
    usage = {"total_tokens": 1}
    if user.startswith("请根据以下 speaker 画像"):
        return json.dumps({"default": {"0": "销售", "1": "客户"}, "segments": [], "fallback": "旁人"}, ensure_ascii=False), usage
    if "以下为角色转写全文" in user:
        return json.dumps({"result": "有效", "reason": "谈订货与利润"}, ensure_ascii=False), usage
    module = user.split("本次唯一任务：", 1)[1].split("。", 1)[0]
    mode = "daily" if module == "next-action" and "closure" in system else "first"
    return json.dumps(candidate(module, mode, user), ensure_ascii=False), usage


@pytest.fixture
def ai(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "call_llm", fake_llm)
    monkeypatch.setenv("REVIEW_MODE", "off")
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "up"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def confirmed_visit(client, auth, stage="首访破冰", segments=1):
    store = make_store(client, auth)
    v = make_visit(client, auth, store["id"], stage=stage, durationSec=300)
    for i in range(segments):
        r = client.post(f"/visits/{v['id']}/segments", files={"file": (f"a{i}.mp3", b"ID3" + b"\0" * 2048, "audio/mpeg")}, data={"index": str(i)}, headers=auth)
        assert r.status_code == 200, r.text
    client.post(f"/visits/{v['id']}/segments/complete", json={"durationSec": 300}, headers=auth)
    r = client.post(f"/visits/{v['id']}/confirm-cost", headers=auth)
    assert r.status_code == 200, r.text
    return store, v


def test_full_pipeline(client, auth, ai):
    store, v = confirmed_visit(client, auth, segments=2)
    las = FakeLas()
    worker.run_visit(int(v["id"]), las)
    assert las.submits == 2 and las.uploads == 2  # 两段各提交一次
    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["status"] == "done", detail.get("pipeline")
    a = detail["analysis"]
    assert a["explicitNeeds"][0]["evidence"][0]["text"].startswith("能退的话")
    assert [c["state"] for c in a["concerns"]].count("是") == 2
    assert a["effectiveness"]["total"] == 71 and len(a["effectiveness"]["dims"]) == 8
    assert a["nextAction"]["actions"][0]["topic"] == "补齐价格证据" and a["nextAction"]["confirmed"][0].startswith("[销售｜周五]")
    assert len(detail["transcript"]) == 16 and detail["transcript"][0]["uid"] == "U0001" and detail["transcript"][1]["role"] == "客户"
    assert detail["pipeline"]["stage"] == "done" and detail["pipeline"]["validity"]["value"] == "有效"
    with SessionLocal() as db:
        topics = {t.topic: t for t in db.scalars(select(Todo).where(Todo.visit_id == int(v["id"]), Todo.source == "ai"))}
        assert set(topics) == {"补齐价格证据", "把价格表和退货政策发给客户"}  # 建议行动 + 录音里已确认的约定
        assert topics["把价格表和退货政策发给客户"].reason == "录音中已确认的约定" and topics["把价格表和退货政策发给客户"].owner == "销售"
        assert db.scalar(select(StoreProfileSection).where(StoreProfileSection.key == "price_profit")).state == "当前状态"
        assert db.scalars(select(VisitAnalysis.model).where(VisitAnalysis.module == "concerns")).first() != ""
        assert db.get(Visit, int(v["id"])).cooperated == "否"


def test_resume_does_not_resubmit(client, auth, ai):
    _, v = confirmed_visit(client, auth)
    las = FakeLas()

    class Boom(FakeLas):
        def poll(self, task_id):
            raise RuntimeError("网络断了")

    boom = Boom()
    worker.run_visit(int(v["id"]), boom)
    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["status"] == "failed" and detail["pipeline"]["errorStage"] == "asr_poll" and boom.submits == 1
    assert client.post(f"/visits/{v['id']}/retry", headers=auth).json()["status"] == "asr_running"
    worker.run_visit(int(v["id"]), las)
    assert las.submits == 0  # 重试只继续轮询，不会再提交
    assert client.get(f"/visits/{v['id']}", headers=auth).json()["status"] == "done"


def test_submit_uncertain_blocks_retry(client, auth, ai):
    _, v = confirmed_visit(client, auth)

    class Unsure(FakeLas):
        def submit(self, url, audio_format):
            raise transcribe.SubmitUncertain("超时")

    worker.run_visit(int(v["id"]), Unsure())
    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["status"] == "failed" and detail["pipeline"]["uncertain"] is True
    r = client.post(f"/visits/{v['id']}/retry", headers=auth)
    assert r.status_code == 409 and "重复计费" in r.json()["message"]
    from .conftest import ADMIN
    assert client.post(f"/admin/visits/{v['id']}/retry?force=true", headers=ADMIN).status_code == 200


def test_invalid_content_stops_analysis(client, auth, ai, monkeypatch):
    def llm(system, user, model, temperature):
        if "以下为角色转写全文" in user:
            return json.dumps({"result": "内容无效", "reason": "闲聊"}, ensure_ascii=False), {}
        return fake_llm(system, user, model, temperature)

    monkeypatch.setattr(core, "call_llm", llm)
    _, v = confirmed_visit(client, auth)
    worker.run_visit(int(v["id"]), FakeLas())
    assert client.get(f"/visits/{v['id']}", headers=auth).json()["status"] == "invalid_content"
    with SessionLocal() as db:
        assert db.scalars(select(VisitAnalysis)).first() is None
    # 销售认为判错了：人工判为有效，直接分析，不再重新转写
    las = FakeLas()
    assert client.post(f"/visits/{v['id']}/rejudge", headers=auth).json()["status"] == "analyzing"
    worker.run_visit(int(v["id"]), las)
    assert las.submits == 0 and client.get(f"/visits/{v['id']}", headers=auth).json()["status"] == "done"


def test_bad_evidence_never_written(client, auth, ai, monkeypatch):
    def llm(system, user, model, temperature):
        if "本次唯一任务：explicit-needs" in user:
            bad = candidate("explicit-needs")
            bad["explicit-needs"]["items"][0]["evidence_ids"] = ["U0099"]  # 不存在的发言
            return json.dumps(bad, ensure_ascii=False), {}
        return fake_llm(system, user, model, temperature)

    monkeypatch.setattr(core, "call_llm", llm)
    _, v = confirmed_visit(client, auth)
    worker.run_visit(int(v["id"]), FakeLas())
    detail = client.get(f"/visits/{v['id']}", headers=auth).json()
    assert detail["status"] == "partial_manual"  # 其他模块照常落库，只有这个模块留给人工
    assert detail["analysis"]["explicitNeeds"] == [] and detail["analysis"]["concerns"]
    with SessionLocal() as db:
        assert db.scalar(select(VisitAnalysis).where(VisitAnalysis.module == "explicit-needs")) is None


def test_unconfigured_and_daily_limit(client, auth, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv("LAS_API_KEY", "")
    get_settings.cache_clear()
    store = make_store(client, auth)
    v = make_visit(client, auth, store["id"])
    client.post(f"/visits/{v['id']}/segments", files={"file": ("a.mp3", b"ID3" + b"\0" * 2048, "audio/mpeg")}, data={"index": "0"}, headers=auth)
    client.post(f"/visits/{v['id']}/segments/complete", json={"durationSec": 300}, headers=auth)
    r = client.post(f"/visits/{v['id']}/confirm-cost", headers=auth)
    assert r.status_code == 503 and "还没有配置" in r.json()["message"]
    monkeypatch.setenv("LAS_API_KEY", "test-las")
    monkeypatch.setenv("DAILY_VISIT_LIMIT", "0")
    get_settings.cache_clear()
    r = client.post(f"/visits/{v['id']}/confirm-cost", headers=auth)
    assert r.status_code == 429
    get_settings.cache_clear()


def test_daily_visit_uses_last_actions_for_closure(client, auth, ai):
    store, v1 = confirmed_visit(client, auth)
    worker.run_visit(int(v1["id"]), FakeLas())
    import time
    time.sleep(0.01)
    v2 = make_visit(client, auth, store["id"], stage="日常维护", durationSec=300)
    client.post(f"/visits/{v2['id']}/segments", files={"file": ("a.mp3", b"ID3" + b"\0" * 2048, "audio/mpeg")}, data={"index": "0"}, headers=auth)
    client.post(f"/visits/{v2['id']}/segments/complete", json={"durationSec": 300}, headers=auth)
    client.post(f"/visits/{v2['id']}/confirm-cost", headers=auth)
    worker.run_visit(int(v2["id"]), FakeLas())
    detail = client.get(f"/visits/{v2['id']}", headers=auth).json()
    assert detail["status"] == "done", detail.get("pipeline")
    assert detail["mode"] == "daily" and detail["analysis"]["loop"]["items"][0]["status"] == "已完成"
    assert detail["analysis"]["effectiveness"] is None  # 日常拜访不做打分
