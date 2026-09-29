from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import CorrectionEvent, Store, StoreCorrection, StoreDirectory, StoreProfileSection, User, Visit, VisitAnalysis
from ..services.access import is_manager, team_ids, visible_store
from ..services.constants import COOPERATION_STATUSES, PROFILE_LABELS, PROFILE_STATES
from ..services.ingest import upsert_profile_section
from ..services.summary import _effective, list_stores, list_todos, list_visits, summarize_store

router = APIRouter(prefix="/stores", tags=["门店"])


class StoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    province: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    grid: str = ""
    platform: str | None = None
    externalId: str | None = None
    storeType: str = ""
    cooperationStatus: str = "未触达"
    contactName: str = ""
    contactPhone: str = ""
    lat: float | None = None
    lng: float | None = None


class StorePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    province: str | None = None
    city: str | None = None
    district: str | None = None
    address: str | None = None
    grid: str | None = None
    storeType: str | None = None
    cooperationStatus: str | None = None
    contactName: str | None = None
    contactPhone: str | None = None
    installedAt: date | None = None
    wecomAdded: bool | None = None


class CorrectionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProfilePatch(BaseModel):
    content: str = Field(max_length=5000)
    state: str | None = None


def _check_status(value: str) -> None:
    if value not in COOPERATION_STATUSES:
        raise HTTPException(status_code=400, detail=f"合作状态只能是：{'、'.join(COOPERATION_STATUSES)}")


def _can_manage(db: Session, user: User, store: Store) -> bool:
    """能修改门店资料的人：门店负责人或其上级；没有负责人的门店任何能看到的人都能改"""
    return store.primary_sales_id is None or store.primary_sales_id in team_ids(db, user)


def _detail(db: Session, user: User, store: Store) -> dict:
    visits = list_visits(db, user, store_id=store.id)
    out = summarize_store(db, store, visits, detail=True)
    if not (_can_manage(db, user, store) or is_manager(user)):
        out.pop("contactName", None)
        out.pop("contactPhone", None)
    return {**out, "visits": visits, "legacyActions": _legacy_actions(db, visits), "todos": [t for t in list_todos(db, user, scope="team") if t["storeId"] == out["id"]]}


def _legacy_actions(db: Session, visits: list[dict]) -> list[dict]:
    """飞书导入的拜访里「下一步行动 / 上次行动闭环」原文，按拜访时间倒序"""
    legacy = {int(v["id"]): v for v in visits if v.get("legacy")}
    if not legacy:
        return []
    rows = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id.in_(list(legacy)), VisitAnalysis.module.in_(["next-action", "loop"])))
    by_visit: dict[int, list[dict]] = {}
    for r in rows:
        text = (_effective(r) or {}).get("text", "")
        if text.strip():
            by_visit.setdefault(r.visit_id, []).append({"title": "下一步行动" if r.module == "next-action" else "上次行动闭环", "text": text})
    return [{"visitId": str(vid), "createdAt": v["createdAt"], "stage": v["stage"], "sections": by_visit.get(vid, [])}
            for vid, v in sorted(legacy.items(), key=lambda x: -x[1]["createdAt"])]


def _slim(store: dict) -> dict:
    """列表只需要一句话画像等摘要：去掉体积大的档案七维度、行动和闭环，详情接口再带"""
    out = {k: v for k, v in store.items() if k not in ("profile", "loops", "actions")}
    out["hasProfile"] = store["profile"] is not None
    return out


@router.get("")
def get_stores(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [_slim(s) for s in list_stores(db, user)]


@router.post("")
def create_store(body: StoreIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _check_status(body.cooperationStatus)
    data = {
        "name": body.name.strip(), "province": body.province, "city": body.city, "district": body.district,
        "address": body.address, "grid": body.grid, "store_type": body.storeType,
        "cooperation_status": body.cooperationStatus, "contact_name": body.contactName, "contact_phone": body.contactPhone,
        "lat": body.lat, "lng": body.lng, "primary_sales_id": user.id,
    }
    if body.platform and body.externalId:
        exists = db.scalars(select(Store).where(Store.platform == body.platform, Store.external_id == body.externalId)).first()
        if exists:
            raise HTTPException(status_code=409, detail=f"该平台下已有这家门店：{exists.name}")
        data["platform"], data["external_id"] = body.platform, body.externalId
        directory = db.scalars(
            select(StoreDirectory).where(StoreDirectory.platform == body.platform, StoreDirectory.external_id == body.externalId)
        ).first()
        if directory:
            data["directory_id"] = directory.id
            for field in ("province", "city", "district", "address", "grid"):
                if not data[field]:
                    data[field] = getattr(directory, field)
    store = Store(**data)
    db.add(store)
    db.commit()
    return _detail(db, user, store)


@router.get("/{store_id}")
def get_store(store_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _detail(db, user, visible_store(db, user, store_id))


@router.patch("/{store_id}")
def update_store(store_id: int, body: StorePatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = visible_store(db, user, store_id)
    if not _can_manage(db, user, store):
        raise HTTPException(status_code=403, detail="只有门店负责人或其上级能修改门店资料")
    if body.cooperationStatus is not None:
        _check_status(body.cooperationStatus)
    mapping = {
        "name": "name", "province": "province", "city": "city", "district": "district", "address": "address", "grid": "grid",
        "storeType": "store_type", "cooperationStatus": "cooperation_status", "contactName": "contact_name",
        "contactPhone": "contact_phone", "installedAt": "installed_at", "wecomAdded": "wecom_added",
    }
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(store, mapping[field], value)
    db.commit()
    return _detail(db, user, store)


@router.post("/{store_id}/corrections")
def add_correction(store_id: int, body: CorrectionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """DSR 补充的门店事实：下次分析门店档案时作为权威输入"""
    store = visible_store(db, user, store_id)
    db.add(StoreCorrection(store_id=store.id, user_id=user.id, text=body.text.strip(), source="app"))
    db.commit()
    return True


@router.patch("/{store_id}/profile/{key}")
def edit_profile(store_id: int, key: str, body: ProfilePatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """直接修改档案某个维度，并留下纠正记录"""
    store = visible_store(db, user, store_id)
    if not _can_manage(db, user, store):
        raise HTTPException(status_code=403, detail="只有门店负责人或其上级能修改门店档案")
    if key not in PROFILE_LABELS:
        raise HTTPException(status_code=404, detail="没有这个档案维度")
    if body.state is not None and body.state not in PROFILE_STATES:
        raise HTTPException(status_code=400, detail=f"档案状态只能是：{'、'.join(PROFILE_STATES)}")
    row = db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == store.id, StoreProfileSection.key == key)).first()
    before = {"state": row.state, "content": row.content} if row else None
    after = {"state": body.state or (row.state if row else "当前状态"), "content": body.content}
    upsert_profile_section(db, store.id, key, after["state"], after["content"],
                           evidence=row.evidence if row else None, source_visit_id=row.source_visit_id if row else None, updated_by=user.id)
    db.add(CorrectionEvent(target_type="profile", store_id=store.id, target_key=key, before=before, after=after, user_id=user.id))
    db.commit()
    return _detail(db, user, store)
