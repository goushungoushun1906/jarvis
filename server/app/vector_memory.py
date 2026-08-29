"""ChromaDB-backed semantic memory for JARVIS.

Provides vector embeddings and similarity search over stored memories while
keeping the existing SQLite/FTS5 layer as the source of truth. The vector
store is treated as an index: add/update/delete are mirrored here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from chromadb import PersistentClient
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

logger = logging.getLogger("jarvis")

# Store ChromaDB data next to the SQLite database (inside server/data/chroma_db).
# In PyInstaller production, resolve from _MEIPASS temp dir.
import sys as _sys
if getattr(_sys, 'frozen', False):
    _meipass = getattr(_sys, '_MEIPASS', None)
    if _meipass:
        _prod_server_dir = str(Path(_meipass))
    else:
        _prod_server_dir = str(Path(_sys.executable).parent)
else:
    _prod_server_dir = None
_default_db_dir = os.path.join(
    _prod_server_dir or os.path.dirname(os.path.dirname(__file__)), "data", "chroma_db"
)
_DB_DIR = os.environ.get("JARVIS_CHROMA_DB_DIR", _default_db_dir)

_COLLECTION_NAME = "memories"

_client: PersistentClient | None = None
_collection: Collection | None = None
_ef = DefaultEmbeddingFunction()


def _get_client() -> PersistentClient:
    """Return the shared ChromaDB persistent client."""
    global _client
    if _client is None:
        os.makedirs(_DB_DIR, exist_ok=True)
        _client = PersistentClient(
            path=_DB_DIR,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("ChromaDB persistent client initialized at %s", _DB_DIR)
    return _client


def _get_collection() -> Collection:
    """Return the memories collection, creating it if necessary."""
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=_ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB collection '%s' ready", _COLLECTION_NAME)
    return _collection


async def init_vector_memory() -> None:
    """Warm up the vector memory store (safe to call multiple times)."""
    try:
        await asyncio.to_thread(_get_collection)
    except Exception as e:
        logger.error("Failed to initialize vector memory: %s", e)
        raise


async def add_memory_vector(
    memory_id: int,
    content: str,
    category: str = "general",
    importance: float = 0.5,
    source_conversation_id: str | None = None,
    created_at: str | None = None,
) -> None:
    """Index a memory in ChromaDB."""
    collection = await asyncio.to_thread(_get_collection)
    metadata = {
        "category": category,
        "importance": importance,
    }
    if source_conversation_id:
        metadata["source_conversation_id"] = source_conversation_id
    if created_at:
        metadata["created_at"] = created_at
    await asyncio.to_thread(
        collection.upsert,
        ids=[str(memory_id)],
        documents=[content],
        metadatas=[metadata],
    )
    logger.debug("Indexed memory %d in vector store", memory_id)


async def delete_memory_vector(memory_id: int) -> None:
    """Remove a memory from ChromaDB."""
    collection = await asyncio.to_thread(_get_collection)
    await asyncio.to_thread(
        collection.delete,
        ids=[str(memory_id)],
    )
    logger.debug("Deleted memory %d from vector store", memory_id)


async def search_memories_semantic(
    query: str,
    limit: int = 5,
    category: str | None = None,
    min_importance: float | None = None,
) -> list[dict]:
    """Search memories by semantic similarity.

    Returns a list of dicts with memory_id, content, distance (cosine),
    category and importance.
    """
    collection = await asyncio.to_thread(_get_collection)
    where = None
    if category is not None or min_importance is not None:
        where = {}
        if category is not None:
            where["category"] = category
        if min_importance is not None:
            # Importance filtering happens post-query below via oversampling.
            pass
    results = await asyncio.to_thread(
        collection.query,
        query_texts=[query],
        n_results=limit * 2 if min_importance is not None else limit,
        where=where or None,
        include=["documents", "metadatas", "distances"],
    )
    ids = results.get("ids", [[]])[0] or []
    documents = results.get("documents", [[]])[0] or []
    metadatas = results.get("metadatas", [[]])[0] or []
    distances = results.get("distances", [[]])[0] or []

    output = []
    for memory_id, content, meta, distance in zip(ids, documents, metadatas, distances):
        importance = float(meta.get("importance", 0.5))
        if min_importance is not None and importance < min_importance:
            continue
        output.append({
            "memory_id": int(memory_id),
            "content": content,
            "category": meta.get("category", "general"),
            "importance": importance,
            "source_conversation_id": meta.get("source_conversation_id"),
            "created_at": meta.get("created_at"),
            "distance": float(distance),
        })
        if len(output) >= limit:
            break
    return output


async def reset_vector_memory() -> None:
    """Drop and recreate the memories collection."""
    client = await asyncio.to_thread(_get_client)
    try:
        await asyncio.to_thread(
            client.delete_collection,
            _COLLECTION_NAME,
        )
    except Exception:
        pass
    global _collection
    _collection = None
    await asyncio.to_thread(_get_collection)
