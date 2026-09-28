from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Store, Todo, User, utcnow
from ..services.summary import list_todos, serialize_todo

router = APIRouter(prefix="/todos", tags=["待办"])


@router.get("")
def get_todos(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return list_todos(db, user)


def _set_done(todo_id: int, done: bool, db: Session, user: User) -> dict:
    t = db.get(Todo, todo_id)
    if not t or t.user_id != user.id:
        raise HTTPException(status_code=404, detail="待办不存在")
    t.done_at = utcnow() if done else None
    db.commit()
    return serialize_todo(t, db.get(Store, t.store_id).name)


@router.post("/{todo_id}/done")
def done(todo_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _set_done(todo_id, True, db, user)


@router.post("/{todo_id}/undo")
def undo(todo_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _set_done(todo_id, False, db, user)
