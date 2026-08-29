"""Memory CRUD and search API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import database as db
from .. import memory as mem
from .. import vector_memory

router = APIRouter(prefix="/api/memories", tags=["memory"])


class MemoryCreate(BaseModel):
    content: str
    category: str = "general"
    importance: float = 0.5


class MemoryUpdate(BaseModel):
    content: str | None = None
    category: str | None = None
    importance: float | None = None


@router.get("")
async def list_memories(category: str = None, limit: int = 50, offset: int = 0):
    """List memories with optional category filter."""
    memories = await db.get_memories(category=category, limit=limit, offset=offset)
    return {"memories": memories, "count": len(memories)}


@router.get("/search")
async def search_memories(q: str, limit: int = 10):
    """Full-text search memories."""
    results = await db.search_memories(query=q, limit=limit)
    return {"results": results, "count": len(results)}


@router.post("")
async def create_memory(body: MemoryCreate):
    """Manually create a memory."""
    memory = await db.add_memory(
        content=body.content,
        category=body.category,
        importance=body.importance,
    )
    return memory


@router.delete("/{memory_id}")
async def delete_memory(memory_id: int):
    """Delete a memory."""
    deleted = await db.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(404, "Memory not found")
    return {"deleted": True}


@router.get("/context")
async def get_context():
    """Get formatted memories for conversation context injection."""
    context = await mem.get_context_memories(limit=5)
    return {"context": context}


@router.get("/semantic")
async def semantic_search_memories(q: str, limit: int = 5):
    """Semantic search over memories using ChromaDB embeddings."""
    results = await vector_memory.search_memories_semantic(query=q, limit=limit)
    return {"results": results, "count": len(results)}
