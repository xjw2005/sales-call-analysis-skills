from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai import memory
from ..db import get_db
from ..deps import current_user
from ..models import User

router = APIRouter(prefix="/memories", tags=["助手记忆"])


class MemoryIn(BaseModel):
    kind: str = "alias"
    key: str = Field(default="", max_length=64)
    value: list[str] | str


class MemoryPatch(BaseModel):
    key: str | None = Field(default=None, max_length=64)
    value: list[str] | str | None = None


class TeamIn(BaseModel):
    team: bool = True


def _guard(fn):
    try:
        return fn()
    except LookupError:
        raise HTTPException(status_code=404, detail="记忆不存在")
    except memory.MemoryError_ as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def list_memories(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """助手记住了什么：我自己的（已生效 + 待确认）和上级设为团队通用的"""
    mine = [m for m in memory.visible(db, user, status=None) if m.user_id == user.id]
    team = [m for m in memory.visible(db, user) if m.user_id != user.id]
    return {"items": [memory.serialize(m, user) for m in mine + team]}


@router.post("")
def add_memory(body: MemoryIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """销售自己手动添加：直接生效"""
    def run():
        m, _ = memory.propose(db, user, body.kind, body.key, body.value, "手动添加", user_said=True, question=body.key)
        m.status, m.trust = "active", "user_asserted"
        db.commit()
        return memory.serialize(m, user)
    return _guard(run)


@router.post("/{memory_id}/confirm")
def confirm_memory(memory_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    def run():
        m = memory.confirm(db, user, memory_id)
        db.commit()
        return memory.serialize(m, user)
    return _guard(run)


@router.patch("/{memory_id}")
def patch_memory(memory_id: int, body: MemoryPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    def run():
        m = memory.update(db, user, memory_id, body.value, body.key)
        db.commit()
        return memory.serialize(m, user)
    return _guard(run)


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    def run():
        memory.remove(db, user, memory_id)
        db.commit()
        return True
    return _guard(run)


@router.post("/{memory_id}/team")
def share_memory(memory_id: int, body: TeamIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """经理把自己的别名设为团队通用（下属也能用）或取消"""
    def run():
        m = memory.set_team(db, user, memory_id, body.team)
        db.commit()
        return memory.serialize(m, user)
    return _guard(run)
