from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Store, Todo, User, utcnow
from ..services.access import team_ids, visible_store
from ..services.summary import list_todos, serialize_todo

router = APIRouter(prefix="/todos", tags=["待办"])


class TodoIn(BaseModel):
    topic: str = Field(min_length=1, max_length=128)
    action: str = ""
    storeId: str | None = None
    assigneeId: str | None = None  # 不填就是自己；指派给下属需要是其上级
    dueDate: date | None = None
    timeframe: str = ""
    targetQty: float | None = None
    unit: str = ""
    period: str | None = None


class TodoPatch(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=128)
    action: str | None = None
    dueDate: date | None = None
    status: str | None = None  # open | done | cancelled
    targetQty: float | None = None
    achievedQty: float | None = None
    progressNote: str | None = None


def _accessible(db: Session, user: User, todo_id: int) -> Todo:
    t = db.get(Todo, todo_id)
    if t is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    ids = team_ids(db, user)
    store = db.get(Store, t.store_id) if t.store_id else None
    owner_match = t.assignee_id is None and store is not None and store.primary_sales_id in ids
    if not (t.assignee_id in ids or t.created_by == user.id or owner_match):
        raise HTTPException(status_code=404, detail="待办不存在")
    return t


def _set_status(t: Todo, status: str) -> None:
    if status not in ("open", "done", "cancelled"):
        raise HTTPException(status_code=400, detail="状态只能是 open、done 或 cancelled")
    t.status = status
    t.done_at = utcnow() if status == "done" else None


@router.get("")
def get_todos(scope: str = "mine", db: Session = Depends(get_db), user: User = Depends(current_user)):
    return list_todos(db, user, scope=scope)


@router.post("")
def create_todo(body: TodoIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    assignee = int(body.assigneeId) if body.assigneeId else user.id
    if assignee != user.id and assignee not in team_ids(db, user):
        raise HTTPException(status_code=403, detail="只能指派给自己或下属")
    store_id = visible_store(db, user, int(body.storeId)).id if body.storeId else None
    t = Todo(
        source="self" if assignee == user.id else "manager", store_id=store_id, assignee_id=assignee, created_by=user.id,
        topic=body.topic.strip(), action=body.action, timeframe=body.timeframe, due_date=body.dueDate,
        target_qty=body.targetQty, unit=body.unit, period=body.period, owner="销售",
    )
    db.add(t)
    db.commit()
    return serialize_todo(db, t)


@router.patch("/{todo_id}")
def patch_todo(todo_id: int, body: TodoPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = _accessible(db, user, todo_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        _set_status(t, data.pop("status"))
    mapping = {"topic": "topic", "action": "action", "dueDate": "due_date", "targetQty": "target_qty",
               "achievedQty": "achieved_qty", "progressNote": "progress_note"}
    for field, value in data.items():
        setattr(t, mapping[field], value)
    db.commit()
    return serialize_todo(db, t)


@router.post("/{todo_id}/done")
def done(todo_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = _accessible(db, user, todo_id)
    _set_status(t, "done")
    db.commit()
    return serialize_todo(db, t)


@router.post("/{todo_id}/undo")
def undo(todo_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = _accessible(db, user, todo_id)
    _set_status(t, "open")
    db.commit()
    return serialize_todo(db, t)
