from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services.access import visible_store
from ..services.assistant import brief, today_panel

router = APIRouter(tags=["助手"])


@router.get("/assistant/today")
def get_today(storeId: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return today_panel(db, user, storeId)


@router.get("/assistant/brief/{store_id}")
def get_brief(store_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return brief(db, user, visible_store(db, user, store_id))


# 以下为 AI 功能，第一期不实现
@router.post("/chat")
def chat(user: User = Depends(current_user)):
    raise HTTPException(status_code=501, detail="AI 问答尚未接入")


@router.api_route("/practice/{path:path}", methods=["GET", "POST"])
def practice(path: str, user: User = Depends(current_user)):
    raise HTTPException(status_code=501, detail="AI 陪练尚未接入")
