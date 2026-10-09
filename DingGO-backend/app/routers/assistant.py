from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..ai import chat as ai_chat
from ..ai import worker
from ..config import get_settings
from ..models import User, utcnow
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


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """首页对话：选店 / 排今天的拜访（返回候选门店）或回答销售问题（大模型，只依据门店资料和知识库）"""
    if not worker.configured():
        raise HTTPException(status_code=503, detail="AI 服务还没有配置，请联系管理员")
    since = utcnow() - timedelta(days=1)
    key = f"chat:{user.id}"
    if _chat_count(key, since) >= get_settings().daily_chat_limit:
        raise HTTPException(status_code=429, detail="今天的提问次数已用完，请明天再来")
    try:
        history = [{"role": "user" if h.get("role") == "user" else "ai", "text": str(h.get("text", ""))} for h in body.history[-10:]]
        return ai_chat.chat(db, user, body.question, body.storeId, history)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 模型或网络失败，给销售一句明白话，不暴露内部错误
        raise HTTPException(status_code=502, detail="AI 暂时没有响应，请稍后再试") from e


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
