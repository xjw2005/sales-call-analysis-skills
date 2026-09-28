from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, own_store
from ..models import Store, StoreCorrection, User
from ..services.summary import list_visits, summarize_store

router = APIRouter(prefix="/stores", tags=["门店"])


class StoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    province: str = ""
    city: str = ""
    address: str = ""


class StorePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    province: str | None = None
    city: str | None = None
    address: str | None = None
    cooperated: bool | None = None


class CorrectionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("")
def list_stores(db: Session = Depends(get_db), user: User = Depends(current_user)):
    visits = list_visits(db, user)
    stores = db.query(Store).filter(Store.owner_id == user.id).all()
    out = [summarize_store(db, s, [v for v in visits if v["storeId"] == str(s.id)]) for s in stores]
    return sorted(out, key=lambda s: s["lastVisitAt"] or s["createdAt"], reverse=True)


@router.post("")
def create_store(body: StoreIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = Store(owner_id=user.id, **body.model_dump())
    store.name = store.name.strip()
    db.add(store)
    db.commit()
    return summarize_store(db, store, [])


@router.get("/{store_id}")
def get_store(store_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = own_store(db, user, store_id)
    visits = list_visits(db, user, store_id=store.id)
    return {**summarize_store(db, store, visits), "visits": visits}


@router.patch("/{store_id}")
def update_store(store_id: int, body: StorePatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = own_store(db, user, store_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(store, k, v)
    db.commit()
    return summarize_store(db, store, list_visits(db, user, store_id=store.id))


@router.post("/{store_id}/corrections")
def add_correction(store_id: int, body: CorrectionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    store = own_store(db, user, store_id)
    db.add(StoreCorrection(store_id=store.id, user_id=user.id, text=body.text.strip()))
    db.commit()
    return True
