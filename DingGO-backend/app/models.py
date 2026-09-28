from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="sales")  # sales | manager
    region: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Store(Base):
    """门店（替代飞书 01 门店主档）"""

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    cooperated: Mapped[bool] = mapped_column(Boolean, default=False)
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
    content: Mapped[str] = mapped_column(Text, default="")
    source_visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class StoreCorrection(Base):
    """门店档案纠正（DSR 人工填写，权威性最高）"""

    __tablename__ = "store_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Visit(Base):
    """拜访录音（替代飞书 02 门店拜访记录）"""

    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    cooperated: Mapped[str | None] = mapped_column(String(4), nullable=True)  # 是 | 否，仅首访
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="uploading", index=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    est_cost: Mapped[float] = mapped_column(Float, default=0)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 销售看过分析结果的时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class VisitSegment(Base):
    __tablename__ = "visit_segments"
    __table_args__ = (UniqueConstraint("visit_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VisitTranscript(Base):
    __tablename__ = "visit_transcripts"

    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), primary_key=True)
    utterances: Mapped[list] = mapped_column(JSON, default=list)  # [{uid, role, startMs, text}]
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VisitAnalysis(Base):
    """分析结果，每个模块一行（explicit-needs、concerns、store-profile……）"""

    __tablename__ = "visit_analysis"
    __table_args__ = (UniqueConstraint("visit_id", "module"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    module: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="written")
    result: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Todo(Base):
    """待办：由「下一步行动」的建议行动拆出"""

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    topic: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(32), default="销售")
    timeframe: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    acceptance: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date] = mapped_column(Date)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
