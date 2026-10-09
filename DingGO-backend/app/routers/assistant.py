import json
import logging
import queue
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..ai import chat as ai_chat
from ..ai import worker
from ..config import get_settings
from ..db import SessionLocal
from ..models import ChatLog, User, utcnow
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
    history: list[dict] = []
    context: dict = {}  # {shown: [{storeId, name}]}：刚才给销售看的门店，销售说“第二家”时用


class FeedbackIn(BaseModel):
    logId: int
    rating: int = Field(ge=-1, le=1)
    note: str = Field(default="", max_length=500)


def _guard(user: User) -> None:
    if not worker.configured():
        raise HTTPException(status_code=503, detail="AI 服务还没有配置，请联系管理员")
    if _chat_count(f"chat:{user.id}", utcnow() - timedelta(days=1)) >= get_settings().daily_chat_limit:
        raise HTTPException(status_code=429, detail="今天的提问次数已用完，请明天再来")


def _history(body: ChatIn) -> list[dict]:
    return [{"role": "user" if h.get("role") == "user" else "ai", "text": str(h.get("text", ""))} for h in body.history[-10:]]


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """首页对话（一次返回）：大模型通过工具查真实数据后回答；小程序优先用 /chat/stream"""
    _guard(user)
    try:
        return ai_chat.chat(db, user, body.question, body.storeId, _history(body), body.context)
    except Exception as e:  # noqa: BLE001 模型或网络失败，给销售一句明白话，不暴露内部错误
        raise HTTPException(status_code=502, detail="AI 暂时没有响应，请稍后再试") from e


@router.post("/chat/stream")
def chat_stream(body: ChatIn, user: User = Depends(current_user)):
    """首页对话（流式）：逐行 JSON（NDJSON）。事件：
    {"type":"status","text":"正在查门店…"}  进度；{"type":"delta","text":"…"}  回答文字的一小段；
    {"type":"final", ...完整结果}  结束（含候选门店 cands、更新后的计划 plan、logId）；{"type":"error","message":"…"}。"""
    _guard(user)
    user_id, history = user.id, _history(body)
    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            with SessionLocal() as db:  # 流式响应期间依赖里的会话已经关闭，这里自己开
                me = db.get(User, user_id)
                events.put({"type": "final", **ai_chat.chat(db, me, body.question, body.storeId, history, body.context, emit=events.put)})
        except Exception:  # noqa: BLE001
            logging.getLogger("dinggo.ai").exception("chat stream failed")
            events.put({"type": "error", "message": "AI 暂时没有响应，请稍后再试"})
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


@router.post("/chat/feedback")
def chat_feedback(body: FeedbackIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """销售对一次回答点「有用 / 没用」（可附一句原因），用来优化提示词"""
    log = db.get(ChatLog, body.logId)
    if log is None or log.user_id != user.id:
        raise HTTPException(status_code=404, detail="没有这条记录")
    log.rating, log.feedback_note = (body.rating or None), body.note.strip()
    db.commit()
    return True


_CHAT_LOG: dict[str, list] = {}


def _chat_count(key: str, since) -> int:
    """简单的内存计数（进程重启清零）：防止误用刷爆模型费用；记一次并返回今天已提问次数"""
    now = utcnow()
    hits = [t for t in _CHAT_LOG.get(key, []) if t >= since]
    hits.append(now)
    _CHAT_LOG[key] = hits
    return len(hits) - 1


@router.api_route("/practice/{path:path}", methods=["GET", "POST"])
def practice(path: str, user: User = Depends(current_user)):
    raise HTTPException(status_code=501, detail="AI 陪练尚未接入")
