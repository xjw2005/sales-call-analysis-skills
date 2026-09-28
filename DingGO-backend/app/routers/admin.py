from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import Store, Visit
from ..services.ingest import apply_analysis
from ..services.summary import serialize_visit

router = APIRouter(prefix="/admin", tags=["管理"], dependencies=[Depends(require_admin)])


class AnalysisIn(BaseModel):
    analysis: dict
    transcript: list | None = None


@router.post("/visits/{visit_id}/analysis")
def import_analysis(visit_id: int, body: AnalysisIn, db: Session = Depends(get_db)):
    """写入一条拜访的分析结果（结构同小程序 model/analysis.js），需请求头 X-Admin-Token"""
    v = db.get(Visit, visit_id)
    if not v:
        raise HTTPException(status_code=404, detail="拜访记录不存在")
    apply_analysis(db, v, body.analysis, body.transcript)
    return serialize_visit(db, v, db.get(Store, v.store_id).name, detail=True)
