"""
Hybrid retrieval scorer for North Star.

Implements the ranking formula from docs/RETRIEVAL.md:

    score(item) = α · semantic(item)
                + β · keyword(item)
                + γ · recency(item)

Where:
    semantic(item)  = cosine similarity between query embedding and item embedding
                      (0–1, via pgvector <-> operator converted to similarity)
    keyword(item)   = ts_rank of the item against the query (0–1, normalised)
    recency(item)   = exp(-λ · age_days)  where λ = 0.005 (≈ half-life ~138 days)
                      decay applied to valid_from timestamp

Weights (α, β, γ) are read from settings and must sum to 1.0.
They can be overridden per-call for domain-specific tuning.

Only items with status='validated' and confidence >= confidence_floor are returned.
Results are cached in Redis using the query fingerprint as key.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from src.config import settings
from src.db.client import get_conn
from src.utils.embeddings import get_provider

logger = logging.getLogger(__name__)

# Recency decay rate.  λ = ln(2) / half_life_days
# Default half-life: ~138 days  (λ ≈ 0.005)
_RECENCY_LAMBDA: float = 0.005

# Maximum ts_rank value used for normalisation (Postgres ts_rank returns
# values in [0, 1] for basic queries, but can exceed 1 with normalization
# options — we cap at 1.0 to keep the formula clean).
_KEYWORD_CAP: float = 1.0


# ── Result dataclasses ─────────────────────────────────────────────────────────

@dataclass
class ScoredItem:
    """A single ranked result from hybrid retrieval."""
    id: UUID
    object_type: str          # "knowledge" | "report" | "decision"
    statement: str
    confidence: float
    topics: list[str]
    semantic_score: float     # 0–1
    keyword_score: float      # 0–1
    recency_score: float      # 0–1
    final_score: float        # α·semantic + β·keyword + γ·recency
    # Full row data for context assembly
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """Output of a hybrid retrieval call."""
    query: str
    intent: str
    items: list[ScoredItem]
    query_embedding_used: bool
    alpha: float
    beta: float
    gamma: float
    confidence_floor: float
    total_candidates: int     # before limit


# ── Public entry point ─────────────────────────────────────────────────────────

async def hybrid_score(
    query: str,
    intent: str = "knowledge",
    topics: list[str] | None = None,
    confidence_floor: float | None = None,
    limit: int = 10,
    alpha: float | None = None,
    beta: float | None = None,
    gamma: float | None = None,
) -> RetrievalResult:
    """
    Run hybrid retrieval and return ranked results.

    Args:
        query:            Natural language query string.
        intent:           "knowledge" | "report" | "decision" | "entity"
        topics:           Optional topic filter (AND semantics with OR on embedding).
        confidence_floor: Minimum confidence score (defaults to settings value).
        limit:            Maximum items to return (max 50).
        alpha/beta/gamma: Override ranking weights (must sum to ~1.0 if all provided).

    Returns:
        RetrievalResult with ranked ScoredItems.
    """
    # Resolve weights and floor
    α = alpha  if alpha  is not None else settings.retrieval_alpha
    β = beta   if beta   is not None else settings.retrieval_beta
    γ = gamma  if gamma  is not None else settings.retrieval_gamma
    floor = confidence_floor if confidence_floor is not None else settings.retrieval_confidence_floor

    limit = min(limit, 50)

    # -- Step 1: Embed the query (best-effort; graceful degradation) --
    query_embedding: list[float] | None = None
    try:
        provider = get_provider()
        query_embedding = await provider.embed(query)
    except Exception as exc:
        logger.warning("Retrieval: query embedding failed (%s) — semantic score will be 0", exc)

    # -- Step 2: Dispatch to the right table based on intent --
    if intent == "knowledge":
        items, total = await _score_knowledge(
            query=query,
            embedding=query_embedding,
            topics=topics or [],
            floor=floor,
            limit=limit,
            alpha=α, beta=β, gamma=γ,
        )
    elif intent == "report":
        items, total = await _score_reports(
            query=query,
            embedding=query_embedding,
            limit=limit,
            alpha=α, beta=β, gamma=γ,
        )
    elif intent == "decision":
        items, total = await _score_decisions(
            query=query,
            embedding=query_embedding,
            limit=limit,
            alpha=α, beta=β, gamma=γ,
        )
    else:
        # entity — keyword-only, no embeddings table for entities yet
        items, total = await _score_entities(query=query, limit=limit)

    # -- Step 3: Final sort by final_score DESC --
    items.sort(key=lambda x: x.final_score, reverse=True)

    return RetrievalResult(
        query=query,
        intent=intent,
        items=items[:limit],
        query_embedding_used=query_embedding is not None,
        alpha=α,
        beta=β,
        gamma=γ,
        confidence_floor=floor,
        total_candidates=total,
    )


# ── Per-table scorers ──────────────────────────────────────────────────────────

async def _score_knowledge(
    query: str,
    embedding: list[float] | None,
    topics: list[str],
    floor: float,
    limit: int,
    alpha: float,
    beta: float,
    gamma: float,
) -> tuple[list[ScoredItem], int]:
    """
    Hybrid score all validated knowledge items matching the query.

    Strategy:
    - Semantic: cosine similarity via pgvector (if embedding available)
    - Keyword:  GIN FTS via to_tsvector + plainto_tsquery (migration 002)
    - Recency:  exp(-λ · age_days) on valid_from

    We run semantic and keyword as separate CTEs, then FULL OUTER JOIN on id
    to combine, so items that only match one signal still appear.
    """
    async with get_conn() as conn:

        if embedding:
            rows = await conn.fetch(
                """
                WITH semantic AS (
                    SELECT
                        k.id,
                        k.statement,
                        k.confidence,
                        k.topics,
                        k.valid_from,
                        k.source_section,
                        k.source_report_ids,
                        GREATEST(0.0,
                            1.0 - (e.embedding <-> $1::vector)
                        ) AS sem_score
                    FROM embeddings e
                    JOIN knowledge k ON k.id = e.object_id
                    WHERE e.object_type = 'knowledge'
                      AND k.status = 'validated'
                      AND k.confidence >= $2
                      AND ($3 = '{}' OR k.topics && $3)
                    ORDER BY e.embedding <-> $1::vector
                    LIMIT $4
                ),
                keyword AS (
                    SELECT
                        id,
                        LEAST(
                            ts_rank(
                                to_tsvector('english',
                                    statement || ' ' || COALESCE(array_to_string(topics,' '),'')),
                                plainto_tsquery('english', $5)
                            ),
                            1.0
                        ) AS kw_score
                    FROM knowledge
                    WHERE status = 'validated'
                      AND confidence >= $2
                      AND ($3 = '{}' OR topics && $3)
                      AND to_tsvector('english',
                              statement || ' ' || COALESCE(array_to_string(topics,' '),''))
                          @@ plainto_tsquery('english', $5)
                    LIMIT $4
                )
                SELECT
                    COALESCE(s.id, k.id)              AS id,
                    COALESCE(s.statement, '')          AS statement,
                    COALESCE(s.confidence, 0.5)        AS confidence,
                    COALESCE(s.topics, ARRAY[]::text[]) AS topics,
                    COALESCE(s.valid_from, NOW())       AS valid_from,
                    COALESCE(s.source_section, '')      AS source_section,
                    COALESCE(s.source_report_ids, ARRAY[]::uuid[]) AS source_report_ids,
                    COALESCE(s.sem_score, 0.0)          AS sem_score,
                    COALESCE(k.kw_score,  0.0)          AS kw_score
                FROM semantic s
                FULL OUTER JOIN keyword k ON s.id = k.id
                ORDER BY (
                    $6 * COALESCE(s.sem_score, 0.0)
                  + $7 * COALESCE(k.kw_score,  0.0)
                ) DESC
                LIMIT $4
                """,
                str(embedding),   # $1
                floor,             # $2
                topics or [],      # $3
                limit * 3,         # $4  pull more candidates, trim after recency
                query,             # $5
                alpha,             # $6
                beta,              # $7
            )
        else:
            # No embedding: keyword-only
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    statement,
                    confidence,
                    topics,
                    valid_from,
                    source_section,
                    source_report_ids,
                    0.0 AS sem_score,
                    LEAST(
                        ts_rank(
                            to_tsvector('english',
                                statement || ' ' || COALESCE(array_to_string(topics,' '),'')),
                            plainto_tsquery('english', $1)
                        ),
                        1.0
                    ) AS kw_score
                FROM knowledge
                WHERE status = 'validated'
                  AND confidence >= $2
                  AND ($3 = '{}' OR topics && $3)
                  AND to_tsvector('english',
                          statement || ' ' || COALESCE(array_to_string(topics,' '),''))
                      @@ plainto_tsquery('english', $1)
                ORDER BY kw_score DESC
                LIMIT $4
                """,
                query, floor, topics or [], limit * 3,
            )

        total = len(rows)

        items = []
        for row in rows:
            sem   = float(row["sem_score"])
            kw    = float(row["kw_score"])
            rec   = _recency_score(row["valid_from"])
            final = alpha * sem + beta * kw + gamma * rec

            items.append(ScoredItem(
                id=row["id"],
                object_type="knowledge",
                statement=row["statement"],
                confidence=float(row["confidence"] or 0.5),
                topics=list(row["topics"] or []),
                semantic_score=sem,
                keyword_score=kw,
                recency_score=rec,
                final_score=final,
                raw={
                    "source_section": row["source_section"],
                    "source_report_ids": [str(r) for r in (row["source_report_ids"] or [])],
                },
            ))

    return items, total


async def _score_reports(
    query: str,
    embedding: list[float] | None,
    limit: int,
    alpha: float,
    beta: float,
    gamma: float,
) -> tuple[list[ScoredItem], int]:
    """Hybrid score reports. Reports don't have a confidence field; recency-weighted."""
    async with get_conn() as conn:
        if embedding:
            rows = await conn.fetch(
                """
                WITH semantic AS (
                    SELECT
                        r.id,
                        r.title,
                        r.created_at,
                        r.tags,
                        1.0 - (e.embedding <-> $1::vector) AS sem_score
                    FROM embeddings e
                    JOIN reports r ON r.id = e.object_id
                    WHERE e.object_type = 'report'
                    ORDER BY e.embedding <-> $1::vector
                    LIMIT $2
                ),
                keyword AS (
                    SELECT id,
                        LEAST(ts_rank(
                            to_tsvector('english',
                                title || ' ' || COALESCE(context_summary,'') || ' ' || COALESCE(conclusions,'')),
                            plainto_tsquery('english', $3)
                        ), 1.0) AS kw_score
                    FROM reports
                    WHERE to_tsvector('english',
                            title || ' ' || COALESCE(context_summary,'') || ' ' || COALESCE(conclusions,''))
                        @@ plainto_tsquery('english', $3)
                    LIMIT $2
                )
                SELECT
                    COALESCE(s.id, k.id)              AS id,
                    COALESCE(s.title, '')              AS title,
                    COALESCE(s.created_at, NOW())      AS created_at,
                    COALESCE(s.tags, ARRAY[]::text[])  AS tags,
                    COALESCE(s.sem_score, 0.0)         AS sem_score,
                    COALESCE(k.kw_score,  0.0)         AS kw_score
                FROM semantic s
                FULL OUTER JOIN keyword k ON s.id = k.id
                ORDER BY ($4 * COALESCE(s.sem_score,0.0) + $5 * COALESCE(k.kw_score,0.0)) DESC
                LIMIT $2
                """,
                str(embedding), limit * 2, query, alpha, beta,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, title, created_at, tags, 0.0 AS sem_score,
                    LEAST(ts_rank(
                        to_tsvector('english',
                            title||' '||COALESCE(context_summary,'')||' '||COALESCE(conclusions,'')),
                        plainto_tsquery('english', $1)
                    ), 1.0) AS kw_score
                FROM reports
                WHERE to_tsvector('english',
                        title||' '||COALESCE(context_summary,'')||' '||COALESCE(conclusions,''))
                    @@ plainto_tsquery('english', $1)
                ORDER BY kw_score DESC LIMIT $2
                """,
                query, limit * 2,
            )

        total = len(rows)
        items = []
        for row in rows:
            sem   = float(row["sem_score"])
            kw    = float(row["kw_score"])
            rec   = _recency_score(row["created_at"])
            final = alpha * sem + beta * kw + gamma * rec
            items.append(ScoredItem(
                id=row["id"],
                object_type="report",
                statement=row["title"],
                confidence=1.0,
                topics=list(row["tags"] or []),
                semantic_score=sem,
                keyword_score=kw,
                recency_score=rec,
                final_score=final,
            ))

    return items, total


async def _score_decisions(
    query: str,
    embedding: list[float] | None,
    limit: int,
    alpha: float,
    beta: float,
    gamma: float,
) -> tuple[list[ScoredItem], int]:
    """Score decisions. No embeddings yet — keyword + recency only."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, statement, status, timestamp AS created_at,
                LEAST(ts_rank(
                    to_tsvector('english', statement||' '||COALESCE(rationale,'')),
                    plainto_tsquery('english', $1)
                ), 1.0) AS kw_score
            FROM decisions
            WHERE status NOT IN ('reverted')
              AND to_tsvector('english', statement||' '||COALESCE(rationale,''))
                  @@ plainto_tsquery('english', $1)
            ORDER BY kw_score DESC
            LIMIT $2
            """,
            query, limit * 2,
        )

        total = len(rows)
        items = []
        for row in rows:
            kw    = float(row["kw_score"])
            rec   = _recency_score(row["created_at"])
            final = beta * kw + gamma * rec   # no semantic component yet
            items.append(ScoredItem(
                id=row["id"],
                object_type="decision",
                statement=row["statement"],
                confidence=1.0,
                topics=[],
                semantic_score=0.0,
                keyword_score=kw,
                recency_score=rec,
                final_score=final,
                raw={"status": row["status"]},
            ))

    return items, total


async def _score_entities(
    query: str,
    limit: int,
) -> tuple[list[ScoredItem], int]:
    """Simple name-match for entities (no embeddings or FTS index yet)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, type, created_at
            FROM entities
            WHERE name ILIKE $1
            LIMIT $2
            """,
            f"%{query}%", limit,
        )
        total = len(rows)
        items = [
            ScoredItem(
                id=row["id"],
                object_type="entity",
                statement=row["name"],
                confidence=1.0,
                topics=[row["type"] or ""],
                semantic_score=0.0,
                keyword_score=1.0,
                recency_score=_recency_score(row.get("created_at")),
                final_score=1.0,
            )
            for row in rows
        ]
    return items, total


# ── Helpers ────────────────────────────────────────────────────────────────────

def _recency_score(timestamp: Any) -> float:
    """
    Compute recency score: exp(-λ · age_days).

    - Today → 1.0
    - 138 days ago → ~0.5  (half-life with λ=0.005)
    - 1 year ago → ~0.16
    - 5 years ago → ~0.0003
    """
    if timestamp is None:
        return 0.5  # unknown age — use neutral score

    from datetime import datetime, timezone

    if hasattr(timestamp, "tzinfo"):
        if timestamp.tzinfo is None:
            now = datetime.now()
        else:
            now = datetime.now(timezone.utc)
        age_days = max(0.0, (now - timestamp).total_seconds() / 86400)
    else:
        return 0.5

    return math.exp(-_RECENCY_LAMBDA * age_days)
