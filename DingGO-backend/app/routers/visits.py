import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, own_store, own_visit
from ..models import Store, User, VisitSegment, Visit, utcnow
from ..services import storage
from ..services.audio import duration_ms
from ..services.constants import ANALYZED, FIRST_STAGE, STAGES
from ..services.summary import list_visits, serialize_visit

router = APIRouter(prefix="/visits", tags=["拜访"])

MAX_SEGMENT_BYTES = 50 * 1024 * 1024
ALLOWED_EXT = {".mp3", ".m4a", ".aac", ".wav", ".amr"}


class VisitIn(BaseModel):
    storeId: str
    stage: str
    cooperated: str | None = None
    note: str = Field(default="", max_length=2000)
    durationSec: int = 0


class CompleteIn(BaseModel):
    durationSec: int = 0


def _out(db: Session, v: Visit, detail: bool = False) -> dict:
    return serialize_visit(db, v, db.get(Store, v.store_id).name, detail)


@router.get("")
def get_visits(storeId: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return list_visits(db, user, store_id=int(storeId) if storeId else None)


@router.post("")
def create_visit(body: VisitIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = own_store(db, user, int(body.storeId))
    if body.stage not in STAGES:
        raise HTTPException(status_code=400, detail=f"未知的拜访阶段：{body.stage}")
    cooperated = body.cooperated if body.stage == FIRST_STAGE else None
    if cooperated not in (None, "是", "否"):
        raise HTTPException(status_code=400, detail="是否达成合作只能是「是」或「否」")
    v = Visit(store_id=store.id, user_id=user.id, stage=body.stage, cooperated=cooperated,
              note=body.note, duration_sec=max(0, body.durationSec), status="uploading")
    db.add(v)
    db.commit()
    base = get_settings().public_base_url
    # 小程序用 wx.uploadFile 逐段上传到 uploadUrl（字段名 file，formData.index 为段号）
    return {**_out(db, v), "upload": {"uploadUrl": f"{base}/visits/{v.id}/segments", "formData": {}}}


@router.get("/{visit_id}")
def get_visit(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    v = own_visit(db, user, visit_id)
    if v.status in ANALYZED and v.reviewed_at is None:
        v.reviewed_at = utcnow()
        db.commit()
    return _out(db, v, detail=True)


@router.post("/{visit_id}/segments")
async def upload_segment(
    visit_id: int,
    file: UploadFile = File(...),
    index: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    v = own_visit(db, user, visit_id)
    if v.status != "uploading":
        raise HTTPException(status_code=409, detail="该拜访已结束上传")
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ".mp3"
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的音频格式：{ext}")
    data = await file.read()
    if not data or len(data) > MAX_SEGMENT_BYTES:
        raise HTTPException(status_code=400, detail="音频文件为空或超过 50MB")
    key = f"visits/{v.id}/{index:03d}-{uuid.uuid4().hex[:8]}{ext}"
    size = storage.put(key, data)
    seg = db.scalars(select(VisitSegment).where(VisitSegment.visit_id == v.id, VisitSegment.seq == index)).first()
    if seg is None:
        seg = VisitSegment(visit_id=v.id, seq=index)
        db.add(seg)
    seg.object_key, seg.size, seg.duration_ms = key, size, duration_ms(storage.path_of(key))
    db.commit()
    return {"key": key, "index": index, "durationMs": seg.duration_ms}


@router.post("/{visit_id}/segments/complete")
def complete_upload(visit_id: int, body: CompleteIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """所有分段传完：计算总时长和预估识别费用，进入「待确认费用」"""
    v = own_visit(db, user, visit_id)
    if v.status != "uploading":
        raise HTTPException(status_code=409, detail="该拜访已结束上传")
    count, total_ms = db.execute(
        select(func.count(VisitSegment.id), func.coalesce(func.sum(VisitSegment.duration_ms), 0)).where(VisitSegment.visit_id == v.id)
    ).one()
    if not count:
        raise HTTPException(status_code=400, detail="还没有上传录音")
    # 服务器能读出时长就用服务器的，读不出（部分格式）就用小程序上报的
    v.duration_sec = round(total_ms / 1000) if total_ms else max(v.duration_sec, body.durationSec)
    v.est_cost = round(v.duration_sec / 3600 * get_settings().asr_price_per_hour, 2)
    v.status = "cost_pending"
    db.commit()
    return _out(db, v, detail=True)


@router.post("/{visit_id}/confirm-cost")
def confirm_cost(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    v = own_visit(db, user, visit_id)
    if v.status != "cost_pending":
        raise HTTPException(status_code=409, detail="该拜访不在待确认费用状态")
    v.confirmed_at = utcnow()
    # 语音识别与分析（火山引擎）尚未接入：先停在「语音识别中」，由后续流水线或导入脚本推进
    v.status = "asr_running"
    db.commit()
    return _out(db, v, detail=True)


@router.post("/{visit_id}/rejudge")
def rejudge(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    own_visit(db, user, visit_id)
    raise HTTPException(status_code=501, detail="重新判定需要 AI 功能，尚未接入")
