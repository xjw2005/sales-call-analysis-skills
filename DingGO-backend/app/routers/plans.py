from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import User
from ..services import plans

router = APIRouter(prefix="/plans", tags=["今日计划"])

SOURCES = {"district", "recent", "commitments", "commitment", "gap", "search", "chat", "sales", "manual", "nearby"}  # search：对话里模型查出来的候选


class PlanIn(BaseModel):
    storeIds: list[str] = Field(min_length=1, max_length=20)
    source: str = "manual"
    reasons: dict[str, str] = {}


@router.get("/today")
def today(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return plans.today_items(db, user)


@router.get("/greeting")
def greeting(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """新建对话时的主动问候：有计划报计划，有到期约定提醒，都没有就问今天去哪"""
    return plans.greeting(db, user)


@router.get("/suggest")
def suggest(kind: str, district: str | None = None, lat: float | None = Query(default=None, ge=-90, le=90),
            lng: float | None = Query(default=None, ge=-180, le=180), db: Session = Depends(get_db), user: User = Depends(current_user)):
    """候选门店。kind=nearby 需要带上销售当前位置 lat / lng（只用来算距离，不会保存）"""
    near = (lat, lng) if lat is not None and lng is not None else None
    try:
        return plans.suggest(db, user, kind, district, near)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("")
def add_plan(body: PlanIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.source not in SOURCES:
        raise HTTPException(status_code=400, detail="来源不正确")
    try:
        ids = [int(x) for x in body.storeIds]
        reasons = {int(k): v[:255] for k, v in body.reasons.items()}
    except ValueError:
        raise HTTPException(status_code=400, detail="门店编号不正确")
    return plans.add(db, user, ids, body.source.replace("commitments", "commitment"), reasons)


@router.delete("/{plan_id}")
def remove_plan(plan_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not plans.remove(db, user, plan_id):
        raise HTTPException(status_code=404, detail="计划不存在")
    return True
