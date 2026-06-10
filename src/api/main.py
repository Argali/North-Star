"""
North Star REST API — FastAPI application.

Endpoints (from docs/IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md):

    POST  /scribe/process         Submit raw activity → Scribe pipeline   (Phase 3)
    POST  /archivist/process      Manually trigger Archivist               (Phase 3)
    GET   /retrieve               Hybrid retrieval query                    (Phase 4)

    GET   /reports/{id}           Fetch a report
    POST  /reports                Create a report directly
    GET   /knowledge/{id}         Fetch a knowledge item
    POST  /knowledge              Create a knowledge item directly
    GET   /decisions/{id}         Fetch a decision
    POST  /decisions              Create a decision directly
    GET   /entities/{id}          Fetch an entity and related items
    POST  /entities               Create an entity

    GET   /health                 Liveness check
    GET   /ready                  Readiness check (DB + Redis)

Run locally:
    uvicorn src.api.main:app --reload
    # or via pyproject.toml script:
    northstar-api
"""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.config import settings
from src.db.client import close_pool, get_conn, init_pool
from src.pipelines.queues import close_redis, get_redis


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: open DB pool and Redis. Shutdown: close them cleanly."""
    await init_pool()
    await get_redis()   # warm the Redis connection
    yield
    await close_pool()
    await close_redis()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="North Star",
    description=(
        "North Star v1.0 — AI institutional memory architecture. "
        "Stores Reports, Knowledge, Decisions, and Entities. "
        "Retrieves only what agents need."
    ),
    version="1.0.0",
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    lifespan=lifespan,
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health():
    """Liveness probe. Always returns 200 if the process is running."""
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
async def ready():
    """
    Readiness probe. Checks DB and Redis connectivity.
    Returns 200 if both are reachable, 503 otherwise.
    """
    errors: list[str] = []

    # Check Postgres
    try:
        async with get_conn() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        errors.append(f"postgres: {exc}")

    # Check Redis
    try:
        r = await get_redis()
        await r.ping()
    except Exception as exc:
        errors.append(f"redis: {exc}")

    if errors:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "errors": errors},
        )
    return {"status": "ready"}


# ── Pydantic models ───────────────────────────────────────────────────────────

class ReportCreate(BaseModel):
    title: str
    author: str | None = None
    context_summary: str | None = None
    analysis: str | None = None
    conclusions: str | None = None
    raw_source: dict | None = None
    tags: list[str] = Field(default_factory=list)


class KnowledgeCreate(BaseModel):
    statement: str
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    status: str = "proposed"
    source_report_ids: list[UUID] = Field(default_factory=list)
    source_section: str | None = None
    topics: list[str] = Field(default_factory=list)


class DecisionCreate(BaseModel):
    statement: str
    rationale: str
    linked_knowledge_ids: list[UUID] = Field(default_factory=list)
    owner: str | None = None
    status: str = "planned"


class EntityCreate(BaseModel):
    name: str
    type: str | None = None
    metadata: dict | None = None


# ── Reports ───────────────────────────────────────────────────────────────────

@app.post("/reports", tags=["reports"], status_code=201)
async def create_report(body: ReportCreate) -> dict:
    """
    Create a report directly (bypassing the Scribe pipeline).

    Use this for manually ingesting documents or importing existing records.
    For automatic processing of raw activity, use POST /scribe/process (Phase 3).
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reports (title, author, context_summary, analysis, conclusions, raw_source, tags)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            body.title,
            body.author,
            body.context_summary,
            body.analysis,
            body.conclusions,
            json.dumps(body.raw_source) if body.raw_source else None,
            body.tags or [],
        )
    return dict(row)


@app.get("/reports/{report_id}", tags=["reports"])
async def get_report(report_id: UUID) -> dict:
    """Fetch a report by ID."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM reports WHERE id = $1", report_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return dict(row)


@app.get("/reports", tags=["reports"])
async def list_reports(
    tags: list[str] = Query(default=[]),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
) -> dict:
    """List reports, optionally filtered by tags."""
    async with get_conn() as conn:
        if tags:
            rows = await conn.fetch(
                """
                SELECT * FROM reports
                WHERE tags && $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                tags, limit, offset,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM reports ORDER BY created_at DESC LIMIT $1 OFFSET $2",
                limit, offset,
            )
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


# ── Knowledge ─────────────────────────────────────────────────────────────────

@app.post("/knowledge", tags=["knowledge"], status_code=201)
async def create_knowledge(body: KnowledgeCreate) -> dict:
    """
    Create a knowledge item directly.

    Normally produced by the Scribe pipeline. This endpoint is for testing,
    imports, or manual curation.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge
              (statement, confidence, status, source_report_ids, source_section, topics)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            body.statement,
            body.confidence,
            body.status,
            [str(rid) for rid in body.source_report_ids] or [],
            body.source_section,
            body.topics or [],
        )
    return dict(row)


@app.get("/knowledge/{knowledge_id}", tags=["knowledge"])
async def get_knowledge(knowledge_id: UUID) -> dict:
    """Fetch a knowledge item by ID."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM knowledge WHERE id = $1", knowledge_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Knowledge {knowledge_id} not found")
    return dict(row)


@app.get("/knowledge", tags=["knowledge"])
async def list_knowledge(
    status: str = Query(default="validated"),
    topics: list[str] = Query(default=[]),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
) -> dict:
    """List knowledge items filtered by status and/or topics."""
    async with get_conn() as conn:
        if topics:
            rows = await conn.fetch(
                """
                SELECT * FROM knowledge
                WHERE status = $1 AND topics && $2
                ORDER BY valid_from DESC
                LIMIT $3 OFFSET $4
                """,
                status, topics, limit, offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM knowledge
                WHERE status = $1
                ORDER BY valid_from DESC
                LIMIT $2 OFFSET $3
                """,
                status, limit, offset,
            )
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


# ── Decisions ─────────────────────────────────────────────────────────────────

@app.post("/decisions", tags=["decisions"], status_code=201)
async def create_decision(body: DecisionCreate) -> dict:
    """
    Create a decision directly.

    The Archivist enforces that linked_knowledge_ids must reference validated
    knowledge items and that rationale must be non-empty.
    This endpoint bypasses those checks — for manual use only.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO decisions
              (statement, rationale, linked_knowledge_ids, owner, status)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            body.statement,
            body.rationale,
            [str(kid) for kid in body.linked_knowledge_ids] or [],
            body.owner,
            body.status,
        )
    return dict(row)


@app.get("/decisions/{decision_id}", tags=["decisions"])
async def get_decision(decision_id: UUID) -> dict:
    """Fetch a decision by ID."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM decisions WHERE id = $1", decision_id
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")
    return dict(row)


@app.get("/decisions", tags=["decisions"])
async def list_decisions(
    status: str = Query(default="executed"),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
) -> dict:
    """List decisions filtered by status."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM decisions
            WHERE status = $1
            ORDER BY timestamp DESC
            LIMIT $2 OFFSET $3
            """,
            status, limit, offset,
        )
    return {"items": [dict(r) for r in rows], "limit": limit, "offset": offset}


# ── Entities ──────────────────────────────────────────────────────────────────

@app.post("/entities", tags=["entities"], status_code=201)
async def create_entity(body: EntityCreate) -> dict:
    """Create a domain entity (vehicle, system, supplier, project, etc.)."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO entities (name, type, metadata)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            body.name,
            body.type,
            json.dumps(body.metadata) if body.metadata else None,
        )
    return dict(row)


@app.get("/entities/{entity_id}", tags=["entities"])
async def get_entity(
    entity_id: UUID,
    graph_depth: int = Query(default=2, le=3, description="Relationship hops for context enrichment"),
) -> dict:
    """
    Fetch an entity and its related knowledge, decisions, and graph context.

    Uses the hybrid retrieval graph traverser (depth ≤ graph_depth) to
    return all nodes connected to this entity via the relationships table.
    """
    from src.retrieval.graph import traverse

    async with get_conn() as conn:
        entity = await conn.fetchrow(
            "SELECT * FROM entities WHERE id = $1", entity_id
        )
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")

    graph = await traverse(seed_ids=[entity_id], max_depth=graph_depth)

    return {
        "entity": dict(entity),
        "graph": {
            "knowledge": [
                {"id": str(n.id), "label": n.label, "depth": n.depth, **n.raw}
                for n in graph.knowledge
            ],
            "decisions": [
                {"id": str(n.id), "label": n.label, "depth": n.depth, **n.raw}
                for n in graph.decisions
            ],
            "reports": [
                {"id": str(n.id), "label": n.label, "depth": n.depth, **n.raw}
                for n in graph.reports
            ],
            "entities": [
                {"id": str(n.id), "label": n.label, "depth": n.depth, **n.raw}
                for n in graph.entities
                if n.id != entity_id
            ],
            "contradiction_pairs": [
                {
                    "from_id": str(p.from_id), "to_id": str(p.to_id),
                    "from_label": p.from_label, "to_label": p.to_label,
                }
                for p in graph.contradiction_pairs
            ],
            "total_nodes": graph.total_nodes,
        },
    }


# ── Scribe pipeline (Phase 3 stub) ────────────────────────────────────────────

class ScribeInput(BaseModel):
    source_type: str = Field(..., description="'conversation' | 'task' | 'document'")
    payload: dict[str, Any]
    author: str | None = None


@app.post("/scribe/process", tags=["pipeline"], status_code=202)
async def scribe_process(body: ScribeInput) -> dict:
    """
    Submit raw activity for Scribe processing.

    Enqueues the input to `scribe_queue`. The Scribe agent (Phase 3) picks it
    up asynchronously and produces: Report + Knowledge candidates + Decision
    candidates + Relationship proposals.

    Returns 202 Accepted — check the report once processing completes.

    Phase 3 status: queue ingestion is active; Scribe logic is a stub.
    """
    from src.pipelines.queues import scribe_queue

    item = {
        "source_type": body.source_type,
        "payload": body.payload,
        "author": body.author,
    }
    await scribe_queue.push(item)

    return {
        "status": "queued",
        "queue": "scribe_queue",
        "message": "Input queued for Scribe processing. Scribe agent implementation: Phase 3.",
    }


# ── Archivist pipeline (Phase 3 stub) ────────────────────────────────────────

class ArchivistInput(BaseModel):
    knowledge_candidates: list[dict[str, Any]] = Field(default_factory=list)
    decision_candidates: list[dict[str, Any]] = Field(default_factory=list)
    relationship_candidates: list[dict[str, Any]] = Field(default_factory=list)
    source_report_id: UUID | None = None


@app.post("/archivist/process", tags=["pipeline"], status_code=202)
async def archivist_process(body: ArchivistInput) -> dict:
    """
    Manually trigger Archivist processing on a candidate set.

    Normally, the Archivist consumes from `archivist_queue` automatically.
    This endpoint is for testing or manual overrides.

    Phase 3 status: queue ingestion is active; Archivist logic is a stub.
    """
    from src.pipelines.queues import archivist_queue

    item = {
        "knowledge_candidates": body.knowledge_candidates,
        "decision_candidates": body.decision_candidates,
        "relationship_candidates": body.relationship_candidates,
        "source_report_id": str(body.source_report_id) if body.source_report_id else None,
    }
    await archivist_queue.push(item)

    return {
        "status": "queued",
        "queue": "archivist_queue",
        "message": "Candidates queued for Archivist processing. Archivist implementation: Phase 3.",
    }


# ── Retrieval ─────────────────────────────────────────────────────────────────

@app.get("/retrieve", tags=["retrieval"])
async def retrieve(
    q: str = Query(..., description="Natural language query"),
    intent: str = Query(default="knowledge", description="knowledge | report | decision | entity"),
    topics: list[str] = Query(default=[]),
    confidence_floor: float = Query(default=None),
    limit: int = Query(default=10, le=50),
    graph_depth: int = Query(default=2, le=3, description="Relationship hops for context enrichment"),
    alpha: float = Query(default=None, description="Semantic weight override (0–1)"),
    beta: float = Query(default=None, description="Keyword weight override (0–1)"),
    gamma: float = Query(default=None, description="Recency weight override (0–1)"),
) -> dict:
    """
    Hybrid retrieval: α·semantic + β·keyword + γ·recency, then graph traversal.

    Pipeline:
        1. Embed query (OpenAI / local)
        2. Score candidates: pgvector cosine similarity + ts_rank + recency decay
        3. Merge scores with weighted formula
        4. BFS graph traversal from top-k seeds (depth ≤ graph_depth)
        5. Return ranked results + enriched context package

    Results are cached in Redis (TTL from settings.retrieval_cache_ttl).
    """
    import hashlib as _hashlib

    from src.pipelines.queues import cache_retrieval, get_cached_retrieval
    from src.retrieval import retrieve_context

    floor = confidence_floor if confidence_floor is not None else settings.retrieval_confidence_floor

    cache_key = _hashlib.sha256(
        f"{q}|{intent}|{sorted(topics)}|{floor}|{limit}|{graph_depth}|{alpha}|{beta}|{gamma}".encode()
    ).hexdigest()

    cached = await get_cached_retrieval(cache_key)
    if cached:
        cached["_cache"] = "hit"
        return cached

    context = await retrieve_context(
        query=q,
        intent=intent,
        topics=topics or None,
        confidence_floor=floor,
        limit=limit,
        graph_depth=graph_depth,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
    )
    context["_cache"] = "miss"

    await cache_retrieval(cache_key, context)

    return context


# ── Human review ─────────────────────────────────────────────────────────────
#
# The human review queue is backed by Postgres (human_review_queue table),
# not Redis. Items are durable across restarts and carry a full audit trail.
#
# Resolution actions:
#   approve — validate and store any knowledge candidates in the item's context
#   reject  — discard; no knowledge is stored
#   skip    — defer; item stays pending, queued_at reset to NOW() (moves to back)

class ReviewResolveBody(BaseModel):
    action: str = Field(..., description="'approve' | 'reject' | 'skip'")
    note: str | None = Field(None, description="Optional resolution note")
    resolved_by: str | None = Field(None, description="Name/identifier of the resolver")


@app.get("/human-review", tags=["review"])
async def list_human_review(
    reason: str | None = Query(default=None, description="Substring filter on reason"),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
) -> dict:
    """
    List pending items in the human review queue.

    The queue is populated by:
    - Archivist: direct/ambiguous contradictions
    - Archivist: high-stakes domain flags (safety/compliance/finance/legal/medical)
    - Scribe and Archivist workers: pipeline errors
    - Staleness scan: knowledge flagged as stale

    Each item has: id (UUID), source, reason, context, status, queued_at.
    """
    from src.pipelines.queues import human_review_queue

    items, total = await human_review_queue.list_pending(
        reason=reason, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/human-review/{review_id}", tags=["review"])
async def get_human_review_item(review_id: UUID) -> dict:
    """Fetch a single human review item by UUID."""
    from src.pipelines.queues import human_review_queue

    item = await human_review_queue.get(review_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Review item {review_id} not found")
    return item


@app.post("/human-review/{review_id}/resolve", tags=["review"])
async def resolve_human_review(
    review_id: UUID,
    body: ReviewResolveBody,
) -> dict:
    """
    Resolve a human review item.

    Actions:
    - approve: if the item's context contains a knowledge statement, validate
               and store it with confidence 0.7 (human-curated default).
    - reject:  mark as rejected. No knowledge is stored.
    - skip:    defer; item stays pending, moved to back of queue.

    Resolution is idempotent — resolving an already-resolved item returns 404.
    """
    from src.pipelines.queues import human_review_queue

    action = body.action.lower()
    if action not in ("approve", "reject", "skip"):
        raise HTTPException(
            status_code=400, detail="action must be 'approve', 'reject', or 'skip'"
        )

    # Fetch the item first so we can act on its context
    item = await human_review_queue.get(review_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Review item {review_id} not found")
    if item.get("status") != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Item is already {item['status']} — cannot resolve again",
        )

    approved_ids: list[str] = []

    if action == "approve":
        # If the context contains a knowledge statement, validate and store it
        context = item.get("context", {})
        new_statement = context.get("new_statement") or context.get("statement")
        source_report_id = context.get("source_report_id")

        if new_statement:
            from src.db.client import transaction
            async with transaction() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO knowledge (statement, confidence, status, source_report_ids)
                    VALUES ($1, $2, 'validated', $3)
                    RETURNING id
                    """,
                    new_statement,
                    0.7,
                    [source_report_id] if source_report_id else [],
                )
            approved_ids.append(str(row["id"]))

    # Update queue item status
    updated = await human_review_queue.resolve(
        review_id=review_id,
        action=action,
        note=body.note,
        resolved_by=body.resolved_by,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Item was resolved by another request")

    return {
        "id": str(review_id),
        "action": action,
        "reason": item.get("reason"),
        "note": body.note,
        "resolved_by": body.resolved_by,
        "status": action if action != "skip" else "pending",
        "stored_ids": approved_ids,
    }


# ── Staleness scan (Phase 6 Lite) ─────────────────────────────────────────────

@app.post("/scan", tags=["review"], status_code=202)
async def run_staleness_scan(
    staleness_days: int = Query(
        default=None,
        description="Override default staleness threshold (days). Defaults to settings.staleness_days.",
    ),
    dry_run: bool = Query(default=False, description="Report what would be flagged without changing data."),
) -> dict:
    """
    Scan for stale knowledge and decisions.

    Flags knowledge items that:
    - Are validated
    - Have valid_from older than staleness_days
    - Have no source report created in the last staleness_days

    Flags decisions that:
    - Are in 'planned' status
    - Were created more than staleness_days ago with no update

    Stale items are pushed to human_review_queue for triage.
    In dry_run mode, returns what would be flagged without changing anything.
    """
    from src.db.client import transaction
    from src.pipelines.queues import human_review_queue

    threshold_days = staleness_days if staleness_days is not None else settings.staleness_days
    stale_knowledge: list[dict] = []
    stale_decisions: list[dict] = []

    async with get_conn() as conn:

        # Stale knowledge: validated, old, no recent supporting report
        k_rows = await conn.fetch(
            """
            SELECT k.id, k.statement, k.valid_from, k.topics,
                   r.created_at AS latest_report
            FROM knowledge k
            LEFT JOIN LATERAL (
                SELECT r.created_at
                FROM reports r
                WHERE r.id = ANY(k.source_report_ids::uuid[])
                ORDER BY r.created_at DESC
                LIMIT 1
            ) r ON true
            WHERE k.status = 'validated'
              AND k.valid_from < NOW() - ($1 || ' days')::interval
              AND (r.created_at IS NULL OR r.created_at < NOW() - ($1 || ' days')::interval)
            ORDER BY k.valid_from ASC
            """,
            str(threshold_days),
        )

        for row in k_rows:
            stale_knowledge.append({
                "id": str(row["id"]),
                "statement": row["statement"],
                "valid_from": str(row["valid_from"]),
                "topics": list(row["topics"] or []),
                "latest_report": str(row["latest_report"]) if row["latest_report"] else None,
            })

        # Stale decisions: planned and old
        d_rows = await conn.fetch(
            """
            SELECT id, statement, timestamp AS created_at, owner
            FROM decisions
            WHERE status = 'planned'
              AND timestamp < NOW() - ($1 || ' days')::interval
            ORDER BY timestamp ASC
            """,
            str(threshold_days),
        )

        for row in d_rows:
            stale_decisions.append({
                "id": str(row["id"]),
                "statement": row["statement"],
                "created_at": str(row["created_at"]),
                "owner": row["owner"],
            })

    if not dry_run:
        from src.pipelines.queues import human_review_queue
        for item in stale_knowledge:
            await human_review_queue.push({
                "source": "staleness_scan",
                "reason": "stale_knowledge",
                "context": item,
            })

        for item in stale_decisions:
            await human_review_queue.push({
                "source": "staleness_scan",
                "reason": "stale_decision",
                "context": item,
            })

    return {
        "threshold_days": threshold_days,
        "dry_run": dry_run,
        "stale_knowledge": len(stale_knowledge),
        "stale_decisions": len(stale_decisions),
        "items": {
            "knowledge": stale_knowledge,
            "decisions": stale_decisions,
        },
        "queued": not dry_run,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    """Called by `northstar-api` script defined in pyproject.toml."""
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )


if __name__ == "__main__":
    run()
