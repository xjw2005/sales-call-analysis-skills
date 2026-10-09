import json
import logging
import queue
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..ai import chat as ai_chat
from ..ai import worker
from ..config import get_settings
from ..db import SessionLocal
from ..ai.limits import Rejected, limiter
from ..models import ChatLog, ChatMessage, ChatSession, User, utcnow
from ..services.timeutil import to_ms
from ..services.access import visible_store
from ..services.assistant import brief, today_panel

router = APIRouter(tags=["助手"])


@router.get("/assistant/today")
def get_today(storeId: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return today_panel(db, user, storeId)


@router.get("/assistant/brief/{store_id}")
def get_brief(store_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return brief(db, user, visible_store(db, user, store_id))


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    storeId: str | None = None
    sessionId: int | None = None  # 不传就新开一段对话；上下文由服务端按会话取


class FeedbackIn(BaseModel):
    logId: int
    rating: int = Field(ge=-1, le=1)
    note: str = Field(default="", max_length=500)


def _guard(db: Session, user: User, body: ChatIn) -> None:
    """进入对话前的检查：服务是否配置、会话是不是本人的、每分钟 / 每天的提问次数"""
    if not worker.configured():
        raise HTTPException(status_code=503, detail="AI 服务还没有配置，请联系管理员")
    if body.sessionId is not None:
        sess = db.get(ChatSession, body.sessionId)
        if sess is None or sess.user_id != user.id:
            raise HTTPException(status_code=404, detail="对话不存在")
    since = utcnow() - timedelta(days=1)
    used = db.scalar(select(func.count()).select_from(ChatLog).where(ChatLog.user_id == user.id, ChatLog.created_at >= since)) or 0
    if used >= get_settings().daily_chat_limit:
        raise HTTPException(status_code=429, detail="今天的提问次数已用完，请明天再来")
    try:
        limiter.check_rate(user.id)
    except Rejected as e:
        raise HTTPException(status_code=e.status, detail=e.message)


def _fail_message(e: Exception) -> str:
    return "AI 现在请求比较多，请稍后再试" if "HTTP 429" in str(e) else "AI 暂时没有响应，请稍后再试"


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """首页对话（一次返回）：大模型通过工具查真实数据后回答；小程序优先用 /chat/stream"""
    _guard(db, user, body)
    try:
        limiter.acquire(user.id)
    except Rejected as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    try:
        return ai_chat.chat(db, user, body.question, body.storeId, body.sessionId)
    except Exception as e:  # noqa: BLE001 模型或网络失败，给销售一句明白话，不暴露内部错误
        raise HTTPException(status_code=502, detail=_fail_message(e)) from e
    finally:
        limiter.release(user.id)


@router.post("/chat/stream")
def chat_stream(body: ChatIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """首页对话（流式）：逐行 JSON（NDJSON）。事件：
    {"type":"status","text":"正在查门店…"} 进度（含排队：前面还有几人）；{"type":"delta","text":"…"} 回答文字的一小段；
    {"type":"final", ...完整结果}  结束（含 sessionId、候选门店 cands、更新后的计划 plan、logId）；{"type":"error","message":"…"}。"""
    _guard(db, user, body)
    user_id = user.id
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            limiter.acquire(user_id, on_wait=lambda n: events.put({"type": "status", "text": f"现在提问的人比较多，你排在第 {n} 位…"}))
        except Rejected as e:
            events.put({"type": "error", "message": e.message})
            events.put(None)
            return
        try:
            with SessionLocal() as sdb:  # 流式响应期间依赖里的会话已经关闭，这里自己开
                me = sdb.get(User, user_id)
                events.put({"type": "final", **ai_chat.chat(sdb, me, body.question, body.storeId, body.sessionId, emit=events.put)})
        except Exception as e:  # noqa: BLE001
            logging.getLogger("dinggo.ai").exception("chat stream failed")
            events.put({"type": "error", "message": _fail_message(e)})
        finally:
            limiter.release(user_id)
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            try:
                ev = events.get(timeout=15)
            except queue.Empty:
                yield b"\n"  # 心跳，防止中间代理断开
                continue
            if ev is None:
                return
            yield (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chat/sessions")
def chat_sessions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """我的对话列表（只有自己的），最近的在前"""
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(30)).all()
    return [{"id": r.id, "title": r.title, "updatedAt": to_ms(r.updated_at)} for r in rows]


@router.get("/chat/sessions/{session_id}")
def chat_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        return ai_chat.session_messages(db, user, session_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="对话不存在")


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    sess = db.get(ChatSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="对话不存在")
    db.execute(delete(ChatMessage).where(ChatMessage.session_id == sess.id))
    db.delete(sess)
    db.commit()
    return True


@router.post("/chat/feedback")
def chat_feedback(body: FeedbackIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """销售对一次回答点「有用 / 没用」（可附一句原因），用来优化提示词"""
    log = db.get(ChatLog, body.logId)
    if log is None or log.user_id != user.id:
        raise HTTPException(status_code=404, detail="没有这条记录")
    log.rating, log.feedback_note = (body.rating or None), body.note.strip()
    db.commit()
    return True


@router.api_route("/practice/{path:path}", methods=["GET", "POST"])
def practice(path: str, user: User = Depends(current_user)):
    raise HTTPException(status_code=501, detail="AI 陪练尚未接入")
