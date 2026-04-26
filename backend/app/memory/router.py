"""FastAPI routes for conversation memory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.memory.models import ConversationMemory
from app.memory.store import get_context_for_next_call, get_recent_memories, save_memory

router = APIRouter()


class MemoryCreate(BaseModel):
    content: str
    memory_type: str
    call_log_id: int | None = None


def serialize_memory(memory: ConversationMemory) -> dict[str, Any]:
    """Serialize a memory row."""

    return {
        "id": memory.id,
        "call_log_id": memory.call_log_id,
        "memory_type": memory.memory_type,
        "content": memory.content,
        "source": memory.source,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
    }


@router.get("/")
def list_memories(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """List recent memories."""

    return [serialize_memory(memory) for memory in get_recent_memories(db, limit)]


@router.post("/")
def add_memory(request: MemoryCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Add a manual memory."""

    return serialize_memory(
        save_memory(
            db,
            content=request.content,
            memory_type=request.memory_type,
            call_log_id=request.call_log_id,
            source="manual",
        )
    )


@router.get("/next-call-context")
def next_call_context(db: Session = Depends(get_db)) -> dict[str, str]:
    """Return context string for the next call."""

    return {"context": get_context_for_next_call(db)}
