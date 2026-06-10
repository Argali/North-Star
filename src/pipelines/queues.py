"""
Redis queue client for the North Star async pipeline.

Queue architecture (from docs/IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md):

    scribe_queue          ← raw inputs waiting to be processed by Scribe
    archivist_queue       ← Scribe output (candidates) waiting for Archivist
    report_processing_queue ← reports waiting for embedding generation
    human_review_queue    ← items requiring human decision

Scribe → archivist_queue → Archivist → (validated knowledge / human_review_queue)

Pattern: LPUSH to enqueue, BRPOP to dequeue (blocking pop with timeout).
All queue items are JSON-serialised dictionaries.

Usage:

    from src.pipelines.queues import Queue, QueueName

    q = Queue(QueueName.SCRIBE)

    # Enqueue
    await q.push({"source_type": "conversation", "payload": {...}})

    # Dequeue (blocks up to `timeout` seconds, returns None on timeout)
    item = await q.pop(timeout=5)

    # Peek at queue depth
    depth = await q.depth()
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

import redis.asyncio as aioredis

from src.config import settings


# ── Queue names ───────────────────────────────────────────────────────────────

class QueueName(str, Enum):
    """Canonical queue names — never use raw strings."""
    SCRIBE              = "scribe_queue"
    ARCHIVIST           = "archivist_queue"
    REPORT_PROCESSING   = "report_processing_queue"
    HUMAN_REVIEW        = "human_review_queue"


# ── Redis connection ──────────────────────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return a shared async Redis client. Initialised on first call."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def close_redis() -> None:
    """Close the Redis connection. Call at application shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


# ── Queue class ───────────────────────────────────────────────────────────────

class Queue:
    """
    Simple async FIFO queue backed by a Redis list.

    Items are JSON-serialised dicts.
    LPUSH adds to the left (head); BRPOP removes from the right (tail),
    giving standard first-in-first-out order.
    """

    def __init__(self, name: QueueName | str) -> None:
        self.name = str(name)

    async def push(self, item: dict[str, Any]) -> None:
        """
        Add an item to the queue.

        Args:
            item: Any JSON-serialisable dict.
        """
        client = await get_redis()
        await client.lpush(self.name, json.dumps(item))

    async def pop(self, timeout: int = 5) -> dict[str, Any] | None:
        """
        Remove and return the next item from the queue.

        Blocks for up to `timeout` seconds if the queue is empty.
        Returns None if nothing is available within the timeout.

        Args:
            timeout: Seconds to wait before returning None (0 = block forever).
        """
        client = await get_redis()
        result = await client.brpop(self.name, timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    async def push_many(self, items: list[dict[str, Any]]) -> None:
        """Push multiple items atomically using a pipeline."""
        if not items:
            return
        client = await get_redis()
        async with client.pipeline(transaction=True) as pipe:
            for item in items:
                pipe.lpush(self.name, json.dumps(item))
            await pipe.execute()

    async def depth(self) -> int:
        """Return the current number of items in the queue."""
        client = await get_redis()
        return await client.llen(self.name)

    async def peek(self, count: int = 5) -> list[dict[str, Any]]:
        """
        Non-destructive peek at the next `count` items (right end of list).
        Useful for monitoring dashboards.
        """
        client = await get_redis()
        raw_items = await client.lrange(self.name, -count, -1)
        return [json.loads(r) for r in reversed(raw_items)]

    async def flush(self) -> int:
        """Delete all items from the queue. Returns the number of items removed."""
        client = await get_redis()
        depth = await self.depth()
        await client.delete(self.name)
        return depth


# ── Agent state helpers ───────────────────────────────────────────────────────

async def set_agent_state(agent_id: str, state: dict[str, Any], ttl: int = 3600) -> None:
    """
    Store current task state for a running agent.

    Key pattern: agent_state:{agent_id}
    TTL defaults to 1 hour — state is ephemeral.
    """
    client = await get_redis()
    await client.set(
        f"agent_state:{agent_id}",
        json.dumps(state),
        ex=ttl,
    )


async def get_agent_state(agent_id: str) -> dict[str, Any] | None:
    """Retrieve current state for an agent. Returns None if not found or expired."""
    client = await get_redis()
    raw = await client.get(f"agent_state:{agent_id}")
    return json.loads(raw) if raw else None


async def clear_agent_state(agent_id: str) -> None:
    """Clear an agent's state entry."""
    client = await get_redis()
    await client.delete(f"agent_state:{agent_id}")


# ── Retrieval cache helpers ───────────────────────────────────────────────────

async def cache_retrieval(query_hash: str, result: dict[str, Any]) -> None:
    """
    Cache a retrieval result.

    Key pattern: retrieval_cache:{query_hash}
    TTL from settings.retrieval_cache_ttl (default: 300 seconds).
    """
    client = await get_redis()
    await client.set(
        f"retrieval_cache:{query_hash}",
        json.dumps(result),
        ex=settings.retrieval_cache_ttl,
    )


async def get_cached_retrieval(query_hash: str) -> dict[str, Any] | None:
    """Return a cached retrieval result, or None if not cached / expired."""
    client = await get_redis()
    raw = await client.get(f"retrieval_cache:{query_hash}")
    return json.loads(raw) if raw else None


async def invalidate_retrieval_cache(topic: str | None = None) -> int:
    """
    Invalidate retrieval cache entries.

    If `topic` is given, only entries tagged with that topic are invalidated
    (requires keys to carry topic metadata — full implementation in Phase 4).
    For now, flushes all retrieval_cache:* keys.

    Returns the number of keys deleted.
    """
    client = await get_redis()
    keys = await client.keys("retrieval_cache:*")
    if not keys:
        return 0
    return await client.delete(*keys)


# ── Convenience singletons ────────────────────────────────────────────────────

# Pre-built Queue instances for the standard pipelines.
# Import these directly instead of constructing Queue() manually.

scribe_queue            = Queue(QueueName.SCRIBE)
archivist_queue         = Queue(QueueName.ARCHIVIST)
report_processing_queue = Queue(QueueName.REPORT_PROCESSING)
human_review_queue      = Queue(QueueName.HUMAN_REVIEW)
