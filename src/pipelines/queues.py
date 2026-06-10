"""
Queue clients for the North Star async pipeline.

Queue architecture (from docs/IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md):

    scribe_queue            ← raw inputs waiting to be processed by Scribe       [Redis]
    archivist_queue         ← Scribe output (candidates) waiting for Archivist   [Redis]
    report_processing_queue ← reports waiting for embedding generation            [Redis]
    human_review_queue      ← items requiring human decision                     [Postgres]

Scribe → archivist_queue → Archivist → (validated knowledge / human_review_queue)

The Scribe, Archivist, and report processing queues use Redis for low-latency
agent-to-agent handoff. Items are ephemeral — agents consume and discard them.

The human review queue uses Postgres for durability. Review items must survive
restarts (compliance/safety decisions cannot be lost), must be queryable by
reason and status, and must carry a full audit trail.

Redis queue pattern: LPUSH to enqueue, BRPOP to dequeue.
Postgres queue pattern: INSERT to enqueue, UPDATE status to resolve.

Usage:

    from src.pipelines.queues import Queue, QueueName, human_review_queue

    # Redis queue (agents)
    q = Queue(QueueName.SCRIBE)
    await q.push({"source_type": "conversation", "payload": {...}})
    item = await q.pop(timeout=5)

    # Postgres-backed human review queue
    review_id = await human_review_queue.push({"source": "archivist", "reason": "...", "context": {...}})
    items = await human_review_queue.list_pending(limit=20)
    await human_review_queue.resolve(review_id, action="approve", note="Looks correct")
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)


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


# ── Postgres-backed human review queue ───────────────────────────────────────

class PgHumanReviewQueue:
    """
    Durable human review queue backed by the human_review_queue Postgres table.

    Unlike the Redis-backed Queue, items written here survive restarts, are
    queryable by reason/status, and carry a full audit trail.

    The .push() interface is identical to Queue.push() so callers (Archivist,
    Scribe workers) require no changes.
    """

    async def push(self, item: dict[str, Any]) -> UUID:
        """
        Insert a review item and return its UUID.

        Expects item to have: source (str), reason (str), context (dict).
        Falls back gracefully if keys are missing.
        """
        from src.db.client import transaction

        source  = item.get("source", "unknown")
        reason  = item.get("reason", "unspecified")
        context = item.get("context", item)  # use whole item as context if no explicit key

        async with transaction() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO human_review_queue (source, reason, context)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                source,
                reason,
                json.dumps(context),
            )

        review_id = row["id"]
        logger.info("HumanReview: queued item %s (reason=%s, source=%s)", review_id, reason, source)
        return review_id

    async def list_pending(
        self,
        reason: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Return pending review items and total count.

        Args:
            reason: Optional filter string (substring match on reason column).
            limit:  Page size.
            offset: Page offset.

        Returns:
            (items, total) where items is a list of row dicts.
        """
        from src.db.client import get_conn

        async with get_conn() as conn:
            if reason:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM human_review_queue WHERE status='pending' AND reason ILIKE $1",
                    f"%{reason}%",
                )
                rows = await conn.fetch(
                    """
                    SELECT id, source, reason, context, status, queued_at
                    FROM human_review_queue
                    WHERE status = 'pending' AND reason ILIKE $1
                    ORDER BY queued_at ASC
                    LIMIT $2 OFFSET $3
                    """,
                    f"%{reason}%", limit, offset,
                )
            else:
                total = await conn.fetchval(
                    "SELECT COUNT(*) FROM human_review_queue WHERE status = 'pending'"
                )
                rows = await conn.fetch(
                    """
                    SELECT id, source, reason, context, status, queued_at
                    FROM human_review_queue
                    WHERE status = 'pending'
                    ORDER BY queued_at ASC
                    LIMIT $1 OFFSET $2
                    """,
                    limit, offset,
                )

        items = []
        for row in rows:
            d = dict(row)
            # Parse JSONB context back to dict
            if isinstance(d.get("context"), str):
                try:
                    d["context"] = json.loads(d["context"])
                except Exception:
                    pass
            d["id"] = str(d["id"])
            d["queued_at"] = str(d["queued_at"])
            items.append(d)

        return items, int(total or 0)

    async def resolve(
        self,
        review_id: UUID | str,
        action: str,
        note: str | None = None,
        resolved_by: str | None = None,
    ) -> bool:
        """
        Resolve a review item.

        Args:
            review_id:   UUID of the item to resolve.
            action:      "approve", "reject", or "skip".
            note:        Optional resolution note.
            resolved_by: Name/identifier of the resolver (human or system).

        Returns:
            True if the item was found and updated, False if not found.
        """
        from src.db.client import transaction

        if action not in ("approve", "reject", "skip"):
            raise ValueError(f"action must be approve/reject/skip, got: {action!r}")

        # "skip" re-queues by updating queued_at to now (moves to back of line)
        if action == "skip":
            async with transaction() as conn:
                result = await conn.execute(
                    """
                    UPDATE human_review_queue
                    SET queued_at = NOW(), resolution_note = $2
                    WHERE id = $1 AND status = 'pending'
                    """,
                    UUID(str(review_id)), note,
                )
            return result == "UPDATE 1"

        async with transaction() as conn:
            result = await conn.execute(
                """
                UPDATE human_review_queue
                SET status       = $2,
                    resolved_at  = NOW(),
                    resolved_by  = $3,
                    resolution_note = $4
                WHERE id = $1 AND status = 'pending'
                """,
                UUID(str(review_id)), action, resolved_by, note,
            )
        return result == "UPDATE 1"

    async def get(self, review_id: UUID | str) -> dict[str, Any] | None:
        """Fetch a single review item by ID."""
        from src.db.client import get_conn

        async with get_conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM human_review_queue WHERE id = $1",
                UUID(str(review_id)),
            )
        if row is None:
            return None
        d = dict(row)
        if isinstance(d.get("context"), str):
            try:
                d["context"] = json.loads(d["context"])
            except Exception:
                pass
        d["id"] = str(d["id"])
        d["queued_at"] = str(d["queued_at"])
        if d.get("resolved_at"):
            d["resolved_at"] = str(d["resolved_at"])
        return d

    async def depth(self) -> int:
        """Return the number of pending review items."""
        from src.db.client import get_conn
        async with get_conn() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM human_review_queue WHERE status = 'pending'"
            ) or 0


# ── Convenience singletons ────────────────────────────────────────────────────

# Pre-built Queue instances for the standard pipelines.
# Import these directly instead of constructing Queue() manually.

scribe_queue            = Queue(QueueName.SCRIBE)
archivist_queue         = Queue(QueueName.ARCHIVIST)
report_processing_queue = Queue(QueueName.REPORT_PROCESSING)

# Human review queue uses Postgres for durability (not Redis).
# Interface is compatible with Queue.push() so callers need no changes.
human_review_queue = PgHumanReviewQueue()
