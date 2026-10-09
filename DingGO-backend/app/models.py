from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# MySQL 的 TEXT 最多 64KB，长录音的转写和分析可能超过，用 MEDIUMTEXT（16MB）
LongText = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
# 金额、数量：用定点小数，读出来是 float
Money = Numeric(10, 2, asdecimal=False)
Qty = Numeric(12, 2, asdecimal=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """人员：销售和经理。旧数据导入的人还没登录过，openid 先为空，之后由管理员并入其微信账号"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openid: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(64), default="", index=True)
    role: Mapped[str] = mapped_column(String(16), default="sales")  # sales | manager
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    region: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StoreDirectory(Base):
    """总门店清单（智生活 + 西港，8000+ 家）：平台侧的门店名录和每月销量，用于给门店匹配平台信息"""

    __tablename__ = "store_directory"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128), default="")
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="")  # 启用 | 禁用
    wecom_added: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 是否提报门店清单
    el_code: Mapped[str] = mapped_column(String(64), default="")
    service_station: Mapped[str] = mapped_column(String(128), default="")
    gift_tier: Mapped[str] = mapped_column(String(32), default="")
    monthly_sales: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"2026-01": 12, ...}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Store(Base):
    """门店（01 门店主档）：自己的主键；旧数据用 code（ST-xxxx）对应；平台 + 门店 ID 用来匹配总门店清单"""

    __tablename__ = "stores"
    # 平台或门店 ID 为空时不参与唯一判断
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)  # 旧门店编号 ST-0990
    name: Mapped[str] = mapped_column(String(128), index=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 西港 | 智生活 | 自有平台-登康；无平台为空
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    directory_id: Mapped[int | None] = mapped_column(ForeignKey("store_directory.id"), nullable=True)
    store_type: Mapped[str] = mapped_column(String(32), default="")  # 系统门店 | 自有资源门店 | ...
    cooperation_status: Mapped[str] = mapped_column(String(16), default="未触达")  # 已合作|已触达未合作|意向中|未触达
    primary_sales_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    district: Mapped[str] = mapped_column(String(32), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    grid: Mapped[str] = mapped_column(String(64), default="")
    contact_name: Mapped[str] = mapped_column(String(64), default="")
    contact_phone: Mapped[str] = mapped_column(String(32), default="")  # 个人信息：列表接口不返回
    installed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    wecom_added: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    photo_key: Mapped[str] = mapped_column(String(255), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    feishu_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StoreProfileSection(Base):
    """门店档案七维度，每个维度一行，保存当前最新内容"""

    __tablename__ = "store_profile_sections"
    __table_args__ = (UniqueConstraint("store_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="未确认")  # 稳定档案 | 当前状态 | 未确认
    content: Mapped[str] = mapped_column(LongText, default="")
    evidence: Mapped[str | None] = mapped_column(LongText, nullable=True)  # 原文证据
    source_visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StoreCorrection(Base):
    """门店档案纠正（DSR 手填的门店事实）：分析门店档案时作为权威输入，优先级最高。
    和 correction_events（谁把什么从什么改成什么）是两件事"""

    __tablename__ = "store_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="app")  # app | import
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Visit(Base):
    """拜访（02 门店拜访记录）。一人可拜访多店，一店可被多人拜访；拜访不一定有录音"""

    __tablename__ = "visits"
    __table_args__ = (
        Index("ix_visits_visitor_entered", "visitor_id", "entered_at"),
        Index("ix_visits_store_entered", "store_id", "entered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)  # 旧拜访编号 RA-0182
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    visitor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 实际拜访的人
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 拜访当时的区域经理
    stage: Mapped[str] = mapped_column(String(32))
    business_line: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 跨境招商 | 装机推广
    cooperated: Mapped[str | None] = mapped_column(String(4), nullable=True)  # 是 | 否，本次是否达成合作
    purposes: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 拜访目的（多选）
    note: Mapped[str] = mapped_column(Text, default="")  # 现场速记
    store_condition: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 正常运营|未找到对接人|已闭店|...
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkin_address: Mapped[str] = mapped_column(String(255), default="")
    checkin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    checkin_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    photo_key: Mapped[str] = mapped_column(String(255), default="")
    survey: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 调研：面积、月均销量、店铺类型、是否做跨境…
    recording_mode: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded | none
    no_recording_reason: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="uploading", index=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    est_cost: Mapped[float] = mapped_column(Money, default=0)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 销售看过分析结果的时间
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)  # 从飞书导入：分析结果是原文，不是结构化数据
    feishu_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VisitSegment(Base):
    __tablename__ = "visit_segments"
    __table_args__ = (UniqueConstraint("visit_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(255))
    original_name: Mapped[str] = mapped_column(String(255), default="")
    format: Mapped[str] = mapped_column(String(8), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    file_missing: Mapped[bool] = mapped_column(Boolean, default=False)  # 旧数据只有文件名，音频还没搬过来
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VisitTranscript(Base):
    __tablename__ = "visit_transcripts"

    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), primary_key=True)
    utterances: Mapped[list] = mapped_column(JSON, default=list)  # [{uid, role, startMs, endMs, text}]
    raw_text: Mapped[str | None] = mapped_column(LongText, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="asr")  # asr | import
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VisitPlan(Base):
    """今日计划：销售确定今天要去的门店。拜访创建后自动标为已去；到承诺日期的事项也可以加入计划"""

    __tablename__ = "visit_plans"
    __table_args__ = (UniqueConstraint("user_id", "plan_date", "store_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # district | recent | commitment | sales | manual
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned | visited
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatSession(Base):
    """一段对话（服务端保存，只有本人能看）：上下文由服务端从这里取，前端只传会话号"""

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ChatMessage(Base):
    """对话里的一条：user 提问 | ai 回答 | cands 候选门店卡片 | plan 今日计划卡片"""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    log_id: Mapped[int | None] = mapped_column(ForeignKey("chat_logs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatLog(Base):
    """首页对话的每一次提问：耗时、模型轮数、token 用量，以及销售的「有用 / 没用」反馈（用于优化提示词和控制成本）"""

    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(String(500), default="")
    reply: Mapped[str] = mapped_column(Text, default="")
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), nullable=True)
    tools: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{tool, args, ok}]
    rounds: Mapped[int] = mapped_column(Integer, default=0)  # 模型调用次数
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 流式：多久开始出字
    error: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 有用 | -1 没用
    feedback_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class VisitPipeline(Base):
    """AI 处理进度（每条有录音的拜访一行）：断点续跑、防重复提交转写。
    stage：queued | asr_submit | asr_poll | roles | validity | analysis | done | failed"""

    __tablename__ = "visit_pipeline"

    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    las_tasks: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{seq, taskId, status}]，有 taskId 就不再提交
    error_stage: Mapped[str] = mapped_column(String(16), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)  # 提交转写时无法确认是否已建任务：禁止自动重提
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 各步骤 token 用量、模型
    validity: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {value, reason, source}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VisitAnalysis(Base):
    """分析结果，每个模块一行（explicit-needs、concerns、store-profile……）。
    result 是模型原始结果；销售改过的放 corrected_result，展示时优先用后者"""

    __tablename__ = "visit_analysis"
    __table_args__ = (UniqueConstraint("visit_id", "module"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    module: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="written")
    result: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)  # 原文证据
    corrected_result: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    corrected_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CorrectionEvent(Base):
    """纠正记录：谁在什么时候把哪个分析结果或档案维度从什么改成了什么"""

    __tablename__ = "correction_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16))  # analysis | profile
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True, index=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), nullable=True, index=True)
    target_key: Mapped[str] = mapped_column(String(32))  # 分析模块名或档案维度 key
    before: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Todo(Base):
    """后续行动：AI 建议、主管指派、销售自建、旧数据导入（含月度目标跟踪）。各种关联都允许为空"""

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="ai")  # ai | manager | self | import
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True, index=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id"), nullable=True, index=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)  # 执行人
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    topic: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(32), default="销售")  # 责任人文字（AI 给出的）
    timeframe: Mapped[str] = mapped_column(String(64), default="")  # 原始时限文字：周五前
    reason: Mapped[str] = mapped_column(Text, default="")
    acceptance: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done | cancelled
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 月度目标跟踪（旧「后续行动」表）：差额不存，用 目标 - 达成 算
    period: Mapped[str | None] = mapped_column(String(7), nullable=True)  # 2026-09
    target_qty: Mapped[float | None] = mapped_column(Qty, nullable=True)
    achieved_qty: Mapped[float | None] = mapped_column(Qty, nullable=True)
    unit: Mapped[str] = mapped_column(String(16), default="")
    progress_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
