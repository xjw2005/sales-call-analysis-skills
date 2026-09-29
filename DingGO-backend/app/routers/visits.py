import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user
from ..models import CorrectionEvent, User, Visit, VisitAnalysis, VisitSegment, VisitTranscript, utcnow
from ..ai import worker
from ..services import storage
from ..services.access import actionable_visit, visible_store, visible_visit
from ..services.audio import duration_ms
from ..services.constants import ANALYZED, BUSINESS_LINES, FIRST_STAGE, STAGES, STORE_CONDITIONS
from ..services.summary import list_visits, serialize_visit

router = APIRouter(prefix="/visits", tags=["拜访"])

MAX_SEGMENT_BYTES = 50 * 1024 * 1024
ALLOWED_EXT = {".mp3", ".m4a", ".aac", ".wav", ".amr", ".ogg", ".opus", ".flac"}
ANALYSIS_MODULES = {"explicit-needs", "implicit-needs", "concerns", "effectiveness", "quotes", "store-profile", "next-action"}


class CheckinIn(BaseModel):
    address: str = ""
    lat: float | None = None
    lng: float | None = None


class VisitIn(BaseModel):
    storeId: str
    stage: str
    cooperated: str | None = None
    note: str = Field(default="", max_length=5000)
    durationSec: int = 0
    enteredAt: int | None = None  # 进店时间（毫秒时间戳），不传就用现在
    purposes: list[str] = []
    businessLine: str | None = None
    storeCondition: str | None = None
    checkin: CheckinIn | None = None
    survey: dict | None = None
    recordingMode: str = "uploaded"  # uploaded | none
    noRecordingReason: str = ""


class VisitPatch(BaseModel):
    note: str | None = Field(default=None, max_length=5000)
    purposes: list[str] | None = None
    businessLine: str | None = None
    storeCondition: str | None = None
    cooperated: str | None = None
    survey: dict | None = None
    leftAt: int | None = None


class CompleteIn(BaseModel):
    durationSec: int = 0


class AnalysisPatch(BaseModel):
    result: dict | list


def _validate(stage: str, cooperated, business_line, store_condition) -> None:
    if stage not in STAGES:
        raise HTTPException(status_code=400, detail=f"未知的拜访阶段：{stage}")
    if cooperated not in (None, "是", "否"):
        raise HTTPException(status_code=400, detail="是否达成合作只能是「是」或「否」")
    if business_line not in (None, "") and business_line not in BUSINESS_LINES:
        raise HTTPException(status_code=400, detail=f"业务线只能是：{'、'.join(BUSINESS_LINES)}")
    if store_condition not in (None, "") and store_condition not in STORE_CONDITIONS:
        raise HTTPException(status_code=400, detail=f"门店状况只能是：{'、'.join(STORE_CONDITIONS)}")


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)


@router.get("")
def get_visits(storeId: str | None = None, scope: str = "mine", db: Session = Depends(get_db), user: User = Depends(current_user)):
    return list_visits(db, user, store_id=int(storeId) if storeId else None, scope=scope)


@router.post("")
def create_visit(body: VisitIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = visible_store(db, user, int(body.storeId))
    cooperated = body.cooperated if body.stage == FIRST_STAGE else None
    _validate(body.stage, cooperated, body.businessLine, body.storeCondition)
    if body.recordingMode not in ("uploaded", "none"):
        raise HTTPException(status_code=400, detail="录音方式只能是 uploaded 或 none")
    if body.recordingMode == "none" and not body.noRecordingReason.strip():
        raise HTTPException(status_code=400, detail="没有录音时请填写原因")
    entered = _from_ms(body.enteredAt) if body.enteredAt else utcnow()
    if entered > utcnow() + timedelta(days=1):
        raise HTTPException(status_code=400, detail="进店时间不能是未来")
    v = Visit(
        store_id=store.id, visitor_id=user.id, manager_id=user.manager_id, stage=body.stage,
        business_line=body.businessLine or None, cooperated=cooperated, purposes=body.purposes or None,
        note=body.note, store_condition=body.storeCondition or None, entered_at=entered,
        survey=body.survey, duration_sec=max(0, body.durationSec), recording_mode=body.recordingMode,
        no_recording_reason=body.noRecordingReason.strip(),
        status="uploading" if body.recordingMode == "uploaded" else "no_recording",
    )
    if body.checkin:
        v.checkin_address, v.checkin_lat, v.checkin_lng = body.checkin.address, body.checkin.lat, body.checkin.lng
    db.add(v)
    db.commit()
    out = serialize_visit(db, v)
    if v.status == "uploading":
        # 小程序用 wx.uploadFile 逐段上传到 uploadUrl（字段名 file，formData.index 为段号）
        out["upload"] = {"uploadUrl": f"{get_settings().public_base_url}/visits/{v.id}/segments", "formData": {}}
    return out


@router.get("/{visit_id}")
def get_visit(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    v = visible_visit(db, user, visit_id)
    if v.status in ANALYZED and v.reviewed_at is None and v.visitor_id == user.id:
        v.reviewed_at = utcnow()
        db.commit()
    return serialize_visit(db, v, full=True)


@router.patch("/{visit_id}")
def update_visit(visit_id: int, body: VisitPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    v = actionable_visit(db, user, visit_id)
    _validate(v.stage, body.cooperated, body.businessLine, body.storeCondition)
    if body.note is not None:
        v.note = body.note
    if body.purposes is not None:
        v.purposes = body.purposes
    if body.businessLine is not None:
        v.business_line = body.businessLine or None
    if body.storeCondition is not None:
        v.store_condition = body.storeCondition or None
    if body.cooperated is not None:
        v.cooperated = body.cooperated
    if body.survey is not None:
        v.survey = body.survey
    if body.leftAt is not None:
        v.left_at = _from_ms(body.leftAt)
    db.commit()
    return serialize_visit(db, v, full=True)


@router.post("/{visit_id}/segments")
async def upload_segment(
    visit_id: int,
    file: UploadFile = File(...),
    index: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    v = actionable_visit(db, user, visit_id)
    if v.status != "uploading":
        raise HTTPException(status_code=409, detail="该拜访已结束上传")
    name = file.filename or ""
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".mp3"
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
    seg.object_key, seg.size, seg.format, seg.original_name = key, size, ext.lstrip("."), name[:255]
    seg.duration_ms = duration_ms(storage.path_of(key))
    db.commit()
    return {"key": key, "index": index, "durationMs": seg.duration_ms}


@router.post("/{visit_id}/segments/complete")
def complete_upload(visit_id: int, body: CompleteIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """所有分段传完：计算总时长和预估识别费用，进入「待确认费用」"""
    v = actionable_visit(db, user, visit_id)
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
    return serialize_visit(db, v, full=True)


@router.post("/{visit_id}/confirm-cost")
def confirm_cost(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    v = actionable_visit(db, user, visit_id)
    if v.status != "cost_pending":
        raise HTTPException(status_code=409, detail="该拜访不在待确认费用状态")
    segments = db.scalars(select(VisitSegment).where(VisitSegment.visit_id == v.id)).all()
    if segments and all(seg.file_missing for seg in segments):
        raise HTTPException(status_code=409, detail="这条历史记录的录音文件还没有迁移到服务器，暂时不能识别")
    if not worker.configured():
        raise HTTPException(status_code=503, detail="语音识别与分析服务还没有配置，请联系管理员")
    since = utcnow() - timedelta(days=1)
    used = db.scalar(select(func.count()).select_from(Visit).where(Visit.visitor_id == user.id, Visit.confirmed_at >= since)) or 0
    if used >= get_settings().daily_visit_limit:
        raise HTTPException(status_code=429, detail=f"今天已确认识别 {used} 条录音，达到每日上限，请明天再试或联系管理员")
    v.confirmed_at = utcnow()
    worker.enqueue(db, v)  # 后台线程接手：转写 → 角色 → 有效性 → 分析
    db.commit()
    return serialize_visit(db, v, full=True)


@router.post("/{visit_id}/retry")
def retry_visit(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """处理失败后从失败的那一步继续（已提交的转写任务不会重复提交）"""
    v = actionable_visit(db, user, visit_id)
    try:
        worker.retry(db, v)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    db.commit()
    return serialize_visit(db, v, full=True)


@router.post("/{visit_id}/rejudge")
def rejudge(visit_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """销售认为系统判成「录音过短/内容无效」不对：人工判为有效，直接进入分析（转写已有，不再花转写费）"""
    v = actionable_visit(db, user, visit_id)
    if v.status not in ("invalid_short", "invalid_content"):
        raise HTTPException(status_code=409, detail="只有被判为无效的录音才能重新判定")
    if not worker.configured():
        raise HTTPException(status_code=503, detail="语音识别与分析服务还没有配置，请联系管理员")
    if db.get(VisitTranscript, v.id) is None:
        raise HTTPException(status_code=409, detail="这条拜访没有转写内容，无法分析")
    worker.rejudge(db, v)
    db.commit()
    return serialize_visit(db, v, full=True)


@router.patch("/{visit_id}/analysis/{module}")
def correct_analysis(visit_id: int, module: str, body: AnalysisPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """销售修改某个分析模块的结果：原始结果保留，修改后的另存，并留下纠正记录"""
    v = actionable_visit(db, user, visit_id)
    if v.legacy:
        raise HTTPException(status_code=400, detail="从飞书导入的历史记录暂不支持在线修改")
    if module not in ANALYSIS_MODULES:
        raise HTTPException(status_code=404, detail="没有这个分析模块")
    row = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id == v.id, VisitAnalysis.module == module)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="该模块还没有分析结果")
    before = row.corrected_result if row.corrected_result is not None else row.result
    row.corrected_result, row.corrected_by, row.corrected_at = body.result, user.id, utcnow()
    db.add(CorrectionEvent(target_type="analysis", visit_id=v.id, store_id=v.store_id, target_key=module,
                           before=before, after=body.result, user_id=user.id))
    db.commit()
    return serialize_visit(db, v, full=True)
