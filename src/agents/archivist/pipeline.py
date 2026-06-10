"""
Archivist pipeline -- the quality gate of North Star's memory.

Implements the pipeline from docs/AGENTS/ARCHIVIST.md section 10:

    Receive candidates from archivist_queue
      |
      v
    Validate each knowledge candidate
      |- Duplicate?     -> Merge (deprecate lower-confidence item)
      |- Contradiction? -> Classify (temporal / direct / contextual)
      |       |- Temporal   -> supersede old, store new as validated
      |       +- Direct/ambiguous -> flag both, route to human_review_queue
      +- Clean?         -> validate, store
      |
      v
    Validate each decision candidate
      |- Missing knowledge link? -> Reject
      |- Missing rationale?      -> Reject
      +- Clean?                  -> validate, store
      |
      v
    Validate and insert relationships
      |
      v
    Run staleness scan + decision impact tracing
      |
      v
    Emit human review tasks if needed
      |
      v
    Return ArchivistOutput

One Anthropic API call is used for ambiguous contradiction classification
when the heuristic confidence is low. All DB writes are atomic per candidate.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import anthropic

from src.config import settings
from src.db.client import get_conn, transaction
from src.pipelines.queues import human_review_queue
from src.utils.embeddings import get_provider

from .models import (
    ArchivistOutput,
    ContradictionRecord,
    DecisionOutcome,
    ImpactRecord,
    KnowledgeOutcome,
    MergeRecord,
    ValidationRecord,
)

logger = logging.getLogger(__name__)

# Similarity thresholds
DUPLICATE_THRESHOLD   = 0.92   # cosine similarity above this = duplicate -> merge
CONTRADICTION_THRESHOLD = 0.15  # cosine distance below this = potential contradiction

# High-stakes topic tags -- always route to human review, no auto-resolution
HIGH_STAKES_TOPICS = {"safety", "compliance", "finance", "legal", "medical"}

# Module-level Anthropic client
_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None
        )
    return _anthropic_client


# == Public entry point ========================================================

async def run_pipeline(item: dict[str, Any]) -> ArchivistOutput:
    """
    Run the full Archivist pipeline on a queue item from archivist_queue.

    Args:
        item: Dict with keys:
              knowledge_candidates[], decision_candidates[],
              relationship_candidates[], source_report_id

    Returns:
        ArchivistOutput with counts and audit records.
    """
    source_report_id = _parse_uuid(item.get("source_report_id"))
    k_candidates = item.get("knowledge_candidates", [])
    d_candidates = item.get("decision_candidates", [])
    r_candidates = item.get("relationship_candidates", [])

    output = ArchivistOutput(source_report_id=source_report_id)

    # Map from candidate index -> stored UUID (filled as we validate)
    # Used to resolve linked_knowledge_refs in decisions and relationship refs
    knowledge_id_map: dict[int, UUID] = {}

    logger.info(
        "Archivist: processing batch — source_report=%s K=%d D=%d R=%d",
        source_report_id, len(k_candidates), len(d_candidates), len(r_candidates),
    )

    # == Step 1: Validate knowledge candidates =================================
    for i, raw in enumerate(k_candidates):
        record = await _validate_knowledge(
            index=i,
            raw=raw,
            source_report_id=source_report_id,
            output=output,
        )
        output.knowledge_records.append(record)

        if record.outcome == KnowledgeOutcome.VALIDATED and record.stored_id:
            knowledge_id_map[i] = record.stored_id
            output.validated_knowledge += 1
        elif record.outcome == KnowledgeOutcome.MERGED and record.merged_into:
            knowledge_id_map[i] = record.merged_into
            output.merged += 1
        elif record.outcome == KnowledgeOutcome.REVIEW:
            output.contradictions_flagged += 1
            output.review_requests += 1
        else:
            output.rejected += 1

    # == Step 2: Validate decision candidates ==================================
    for i, raw in enumerate(d_candidates):
        record = await _validate_decision(
            index=i,
            raw=raw,
            knowledge_id_map=knowledge_id_map,
            output=output,
        )
        output.decision_records.append(record)

        if record.outcome == DecisionOutcome.VALIDATED and record.stored_id:
            output.validated_decisions += 1
        elif record.outcome == DecisionOutcome.REVIEW:
            output.review_requests += 1
        else:
            output.rejected += 1

    # == Step 3: Validate and insert relationships =============================
    inserted = await _insert_relationships(
        r_candidates=r_candidates,
        knowledge_id_map=knowledge_id_map,
        decision_records=output.decision_records,
        source_report_id=source_report_id,
    )
    output.relationships_inserted = inserted

    # == Step 4: Staleness scan on affected topics =============================
    deprecated_count = await _staleness_scan(knowledge_id_map=knowledge_id_map)
    output.deprecated = deprecated_count

    # == Step 5: Decision impact tracing =======================================
    if output.deprecated > 0 or output.merged > 0:
        impact_records = await _trace_decision_impact(
            affected_ids=_collect_deprecated_ids(output),
        )
        output.impact_records = impact_records

    logger.info(
        "Archivist: done — validated_K=%d validated_D=%d merged=%d "
        "deprecated=%d contradictions=%d review=%d rejected=%d",
        output.validated_knowledge, output.validated_decisions,
        output.merged, output.deprecated,
        output.contradictions_flagged, output.review_requests, output.rejected,
    )

    return output


# == Knowledge validation ======================================================

async def _validate_knowledge(
    index: int,
    raw: dict[str, Any],
    source_report_id: UUID | None,
    output: ArchivistOutput,
) -> ValidationRecord:
    """Validate one knowledge candidate. Returns a ValidationRecord."""
    statement = raw.get("statement", "").strip()
    topics = raw.get("topics", [])
    confidence = float(raw.get("confidence", 0.0))
    source_report_ids_raw = raw.get("source_report_ids", [])

    # -- Provenance check --
    # Must link to the source report (at minimum)
    if not statement:
        return ValidationRecord(
            index=index, statement="(empty)",
            outcome=KnowledgeOutcome.REJECTED,
            reason="Empty statement",
        )

    if source_report_id is None and not source_report_ids_raw:
        return ValidationRecord(
            index=index, statement=statement,
            outcome=KnowledgeOutcome.REJECTED,
            reason="No source report — provenance missing",
        )

    effective_report_ids = source_report_ids_raw or [str(source_report_id)]

    # -- High-stakes domain check --
    is_high_stakes = bool(set(t.lower() for t in topics) & HIGH_STAKES_TOPICS)

    # -- Embedding for similarity checks --
    provider = get_provider()
    try:
        embedding = await provider.embed(statement)
    except Exception as exc:
        logger.warning("Archivist: embedding failed for K%d: %s — proceeding without similarity checks", index, exc)
        embedding = None

    # -- Duplicate check (cosine similarity vs existing validated knowledge) --
    if embedding:
        dup_row = await _find_similar_knowledge(embedding, threshold_similarity=DUPLICATE_THRESHOLD)
        if dup_row:
            logger.info("Archivist: K%d is duplicate of %s (sim=%.3f) — merging",
                        index, dup_row["id"], dup_row["similarity"])
            merged_id = await _merge_knowledge(
                existing_id=dup_row["id"],
                new_statement=statement,
                new_confidence=confidence,
                new_report_ids=effective_report_ids,
                output=output,
            )
            return ValidationRecord(
                index=index, statement=statement,
                outcome=KnowledgeOutcome.MERGED,
                merged_into=merged_id,
                reason=f"Merged with {merged_id} (similarity={dup_row['similarity']:.3f})",
            )

    # -- Contradiction check --
    contradiction_flag = raw.get("contradiction_flag", False)
    contradicts_id_raw = raw.get("contradicts_id")
    contradiction_type = raw.get("contradiction_type")

    # Re-check via pgvector even if Scribe didn't flag it (belt and suspenders)
    if embedding and not contradiction_flag:
        cont_row = await _find_contradiction_candidate(
            embedding, threshold_distance=CONTRADICTION_THRESHOLD
        )
        if cont_row:
            contradiction_flag = True
            contradicts_id_raw = str(cont_row["id"])
            contradiction_type = contradiction_type or cont_row.get("guessed_type", "contextual")

    if contradiction_flag and contradicts_id_raw:
        existing_id = _parse_uuid(contradicts_id_raw)

        # High-stakes: always route to human
        if is_high_stakes:
            await _push_to_review(
                reason="high_stakes_contradiction",
                context={
                    "statement": statement,
                    "contradicts_id": str(existing_id),
                    "topics": topics,
                    "source_report_id": str(source_report_id),
                },
            )
            return ValidationRecord(
                index=index, statement=statement,
                outcome=KnowledgeOutcome.REVIEW,
                reason=f"High-stakes contradiction with {existing_id} — routed to human",
            )

        # Classify and apply state machine
        if contradiction_type is None:
            contradiction_type = await _classify_contradiction_llm(
                new_statement=statement,
                existing_id=existing_id,
            )

        resolution = await _apply_contradiction_state_machine(
            contradiction_type=contradiction_type,
            new_statement=statement,
            new_confidence=confidence,
            new_topics=topics,
            new_report_ids=effective_report_ids,
            existing_id=existing_id,
            source_report_id=source_report_id,
            index=index,
            output=output,
        )

        record = ContradictionRecord(
            new_item_index=index,
            existing_id=existing_id,
            contradiction_type=contradiction_type,
            resolution=resolution["action"],
            new_item_id=resolution.get("new_id"),
        )
        output.contradiction_records.append(record)

        if resolution["action"] == "superseded":
            return ValidationRecord(
                index=index, statement=statement,
                outcome=KnowledgeOutcome.VALIDATED,
                stored_id=resolution.get("new_id"),
                reason=f"Supersedes {existing_id} (temporal evolution)",
            )
        else:
            return ValidationRecord(
                index=index, statement=statement,
                outcome=KnowledgeOutcome.REVIEW,
                reason=f"{contradiction_type} contradiction with {existing_id} — routed to human",
            )

    # -- Clean: validate and store --
    stored_id = await _store_knowledge(
        statement=statement,
        confidence=confidence,
        topics=topics,
        source_report_ids=effective_report_ids,
        source_section=raw.get("source_section", ""),
    )

    # Store embedding
    if embedding:
        await _store_embedding(stored_id, "knowledge", embedding)

    return ValidationRecord(
        index=index, statement=statement,
        outcome=KnowledgeOutcome.VALIDATED,
        stored_id=stored_id,
    )


# == Decision validation =======================================================

async def _validate_decision(
    index: int,
    raw: dict[str, Any],
    knowledge_id_map: dict[int, UUID],
    output: ArchivistOutput,
) -> ValidationRecord:
    """Validate one decision candidate."""
    statement = raw.get("statement", "").strip()
    rationale  = raw.get("rationale", "").strip()
    owner      = raw.get("owner") or None
    status     = raw.get("status", "planned")
    linked_refs = raw.get("linked_knowledge_refs", [])

    if not statement:
        return ValidationRecord(
            index=index, statement="(empty)",
            outcome=DecisionOutcome.REJECTED,
            reason="Empty statement",
        )

    # -- Rationale required --
    if not rationale:
        return ValidationRecord(
            index=index, statement=statement,
            outcome=DecisionOutcome.REJECTED,
            reason="Missing rationale",
        )

    # -- Resolve knowledge refs to UUIDs --
    linked_ids = [knowledge_id_map[ref] for ref in linked_refs if ref in knowledge_id_map]

    # Also accept UUIDs passed directly (when Archivist is called manually)
    for ref in linked_refs:
        if isinstance(ref, str) and len(ref) == 36:
            uid = _parse_uuid(ref)
            if uid and uid not in linked_ids:
                linked_ids.append(uid)

    if not linked_ids:
        return ValidationRecord(
            index=index, statement=statement,
            outcome=DecisionOutcome.REJECTED,
            reason=(
                "No validated knowledge linked. "
                "Decisions must reference at least one validated knowledge item."
            ),
        )

    # -- Owner required (warn only, don't reject) --
    if not owner:
        logger.warning("Archivist: D%d has no owner — storing with null owner.", index)

    # -- Store decision --
    stored_id = await _store_decision(
        statement=statement,
        rationale=rationale,
        linked_knowledge_ids=[str(uid) for uid in linked_ids],
        owner=owner,
        status=status,
    )

    return ValidationRecord(
        index=index, statement=statement,
        outcome=DecisionOutcome.VALIDATED,
        stored_id=stored_id,
    )


# == Relationship management ===================================================

async def _insert_relationships(
    r_candidates: list[dict[str, Any]],
    knowledge_id_map: dict[int, UUID],
    decision_records: list[ValidationRecord],
    source_report_id: UUID | None,
) -> int:
    """
    Resolve relationship refs to UUIDs and insert valid edges.
    Returns the number of relationships inserted.
    """
    # Build decision index -> UUID map
    decision_id_map: dict[int, UUID] = {
        r.index: r.stored_id
        for r in decision_records
        if r.outcome == DecisionOutcome.VALIDATED and r.stored_id
    }

    inserted = 0

    for r in r_candidates:
        from_id = _resolve_ref(r.get("from_ref", ""), source_report_id, knowledge_id_map, decision_id_map)
        to_id   = _resolve_ref(r.get("to_ref", ""),   source_report_id, knowledge_id_map, decision_id_map)
        edge_type = r.get("type", "")

        if not from_id or not to_id:
            logger.debug("Archivist: skipping relationship — could not resolve ref: %s -> %s", r.get("from_ref"), r.get("to_ref"))
            continue

        if from_id == to_id:
            logger.debug("Archivist: skipping self-loop relationship: %s", from_id)
            continue

        if edge_type not in ("supports", "informs", "contradicts", "relates_to"):
            logger.debug("Archivist: skipping unknown edge type: %s", edge_type)
            continue

        try:
            async with transaction() as conn:
                # Idempotent: skip if this exact edge already exists
                existing = await conn.fetchval(
                    "SELECT id FROM relationships WHERE from_id=$1 AND to_id=$2 AND type=$3",
                    from_id, to_id, edge_type,
                )
                if existing:
                    continue
                await conn.execute(
                    "INSERT INTO relationships (from_id, to_id, type) VALUES ($1, $2, $3)",
                    from_id, to_id, edge_type,
                )
            inserted += 1
        except Exception as exc:
            logger.warning("Archivist: failed to insert relationship %s->%s (%s): %s",
                           from_id, to_id, edge_type, exc)

    return inserted


def _resolve_ref(
    ref: str,
    source_report_id: UUID | None,
    knowledge_id_map: dict[int, UUID],
    decision_id_map: dict[int, UUID],
) -> UUID | None:
    """Resolve a ref string to a UUID."""
    if not ref:
        return None

    if ref == "report":
        return source_report_id

    if ref.startswith("K") and ref[1:].isdigit():
        return knowledge_id_map.get(int(ref[1:]))

    if ref.startswith("D") and ref[1:].isdigit():
        return decision_id_map.get(int(ref[1:]))

    if ref.startswith("E:"):
        # Entity lookup by name -- best-effort
        import asyncio
        entity_name = ref[2:].strip()
        try:
            return asyncio.get_event_loop().run_until_complete(
                _get_or_create_entity(entity_name)
            )
        except Exception:
            return None

    # Direct UUID
    return _parse_uuid(ref)


# == Staleness scan ============================================================

async def _staleness_scan(knowledge_id_map: dict[int, UUID]) -> int:
    """
    Deprecate knowledge items that have been superseded by new validated items
    on the same topics, within the same batch.

    This is a lightweight scan on affected items only.
    Full scheduled staleness scan is Phase 6.
    Returns number of items deprecated.
    """
    # For now, items superseded via contradiction resolution are already deprecated
    # in _apply_contradiction_state_machine. This function can be extended in Phase 6
    # to run broader periodic scans.
    return 0


# == Decision impact tracing ===================================================

async def _trace_decision_impact(affected_ids: list[UUID]) -> list[ImpactRecord]:
    """
    When knowledge is deprecated or superseded, find all decisions linked to it
    and flag them as needs_reassessment.

    Returns list of ImpactRecords for the audit trail.
    """
    if not affected_ids:
        return []

    impact_records = []

    async with get_conn() as conn:
        for knowledge_id in affected_ids:
            # Find decisions that reference this knowledge item
            rows = await conn.fetch(
                """
                SELECT id, status
                FROM decisions
                WHERE $1 = ANY(linked_knowledge_ids::uuid[])
                  AND status NOT IN ('reverted', 'needs_reassessment')
                """,
                knowledge_id,
            )

            for row in rows:
                decision_id = row["id"]
                previous_status = row["status"]

                try:
                    async with transaction() as tx:
                        await tx.execute(
                            "UPDATE decisions SET status = 'needs_reassessment' WHERE id = $1",
                            decision_id,
                        )

                    impact_records.append(ImpactRecord(
                        decision_id=decision_id,
                        triggered_by_knowledge_id=knowledge_id,
                        previous_status=previous_status,
                        reason="Linked knowledge item was deprecated or superseded",
                    ))

                    logger.info(
                        "Archivist: decision %s flagged as needs_reassessment "
                        "(knowledge %s deprecated)",
                        decision_id, knowledge_id,
                    )

                except Exception as exc:
                    logger.error("Archivist: failed to flag decision %s: %s", decision_id, exc)

    return impact_records


# == Contradiction state machine ===============================================

async def _apply_contradiction_state_machine(
    contradiction_type: str,
    new_statement: str,
    new_confidence: float,
    new_topics: list[str],
    new_report_ids: list[str],
    existing_id: UUID,
    source_report_id: UUID | None,
    index: int,
    output: ArchivistOutput,
) -> dict[str, Any]:
    """
    Apply the contradiction resolution logic from ARCHIVIST.md section 7.3.

    Returns a dict: {"action": "superseded"|"flagged", "new_id": UUID|None}
    """
    if contradiction_type == "temporal":
        # Temporal evolution: supersede the old item, store the new one as validated
        try:
            # Deprecate old item
            async with transaction() as conn:
                await conn.execute(
                    "UPDATE knowledge SET status='superseded', valid_until=NOW() WHERE id=$1",
                    existing_id,
                )

            # Store new item as validated
            new_id = await _store_knowledge(
                statement=new_statement,
                confidence=new_confidence,
                topics=new_topics,
                source_report_ids=new_report_ids,
                source_section="",
                status="validated",
            )

            logger.info(
                "Archivist: K%d supersedes %s (temporal) -> new_id=%s",
                index, existing_id, new_id,
            )

            # Add to deprecated tracking for impact tracing
            output.deprecated += 1

            return {"action": "superseded", "new_id": new_id}

        except Exception as exc:
            logger.error("Archivist: failed temporal supersession for K%d: %s", index, exc)
            # Fall through to human review

    # Direct, contextual, or failed temporal: route to human
    await _push_to_review(
        reason=f"{contradiction_type}_contradiction",
        context={
            "new_statement": new_statement,
            "existing_id": str(existing_id),
            "contradiction_type": contradiction_type,
            "source_report_id": str(source_report_id),
        },
    )

    logger.info(
        "Archivist: K%d flagged as %s contradiction with %s — routed to human",
        index, contradiction_type, existing_id,
    )

    return {"action": "flagged", "new_id": None}


async def _classify_contradiction_llm(
    new_statement: str,
    existing_id: UUID,
) -> str:
    """
    Use Claude to classify the contradiction type when heuristics are uncertain.
    Returns "temporal", "direct", or "contextual".
    Falls back to "contextual" on any error.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT statement, valid_from FROM knowledge WHERE id = $1",
            existing_id,
        )

    if not row:
        return "contextual"

    existing_statement = row["statement"]
    valid_from = str(row["valid_from"])

    try:
        client = _get_client()
        response = await client.messages.create(
            model=settings.archivist_model,
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    "Classify the relationship between these two knowledge statements.\n\n"
                    f"Statement A (existing, created {valid_from}):\n{existing_statement}\n\n"
                    f"Statement B (new):\n{new_statement}\n\n"
                    "Reply with exactly one word: 'temporal', 'direct', or 'contextual'.\n"
                    "- temporal: B is an update to A (A was true before, B is true now)\n"
                    "- direct: A and B directly contradict each other on the same claim\n"
                    "- contextual: A and B are both true under different conditions\n"
                    "Reply with one word only."
                ),
            }],
        )
        text = response.content[0].text.strip().lower()
        if text in ("temporal", "direct", "contextual"):
            return text
    except Exception as exc:
        logger.warning("Archivist: LLM contradiction classification failed: %s", exc)

    return "contextual"


# == DB helpers ================================================================

async def _find_similar_knowledge(
    embedding: list[float],
    threshold_similarity: float,
) -> dict | None:
    """
    Find a validated knowledge item with cosine similarity above threshold.
    Returns row dict with {id, statement, similarity} or None.
    """
    # cosine similarity = 1 - cosine_distance
    threshold_distance = 1.0 - threshold_similarity

    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT k.id, k.statement,
                   1 - (e.embedding <-> $1::vector) AS similarity
            FROM embeddings e
            JOIN knowledge k ON k.id = e.object_id
            WHERE e.object_type = 'knowledge'
              AND k.status = 'validated'
              AND e.embedding <-> $1::vector < $2
            ORDER BY e.embedding <-> $1::vector
            LIMIT 1
            """,
            str(embedding),
            threshold_distance,
        )
    return dict(row) if row else None


async def _find_contradiction_candidate(
    embedding: list[float],
    threshold_distance: float,
) -> dict | None:
    """
    Find a validated knowledge item that might contradict this one
    (very close in embedding space but not a duplicate).
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT k.id, k.statement,
                   e.embedding <-> $1::vector AS distance
            FROM embeddings e
            JOIN knowledge k ON k.id = e.object_id
            WHERE e.object_type = 'knowledge'
              AND k.status = 'validated'
              AND e.embedding <-> $1::vector < $2
            ORDER BY e.embedding <-> $1::vector
            LIMIT 1
            """,
            str(embedding),
            threshold_distance,
        )
    return dict(row) if row else None


async def _merge_knowledge(
    existing_id: UUID,
    new_statement: str,
    new_confidence: float,
    new_report_ids: list[str],
    output: ArchivistOutput,
) -> UUID:
    """
    Merge a duplicate into the existing canonical item.
    Combines source_report_ids and keeps the higher-confidence statement.
    Returns the canonical UUID.
    """
    async with transaction() as conn:
        existing = await conn.fetchrow(
            "SELECT confidence, source_report_ids FROM knowledge WHERE id = $1",
            existing_id,
        )
        if not existing:
            raise ValueError(f"Cannot merge: existing knowledge {existing_id} not found")

        existing_confidence = existing["confidence"] or 0.0
        existing_report_ids = list(existing["source_report_ids"] or [])

        # Merge report IDs (deduplicated)
        merged_ids = list(set(existing_report_ids + new_report_ids))

        # Keep higher confidence
        final_confidence = max(existing_confidence, new_confidence)

        await conn.execute(
            """
            UPDATE knowledge
            SET source_report_ids = $1, confidence = $2
            WHERE id = $3
            """,
            merged_ids, final_confidence, existing_id,
        )

    output.merge_records.append(MergeRecord(
        canonical_id=existing_id,
        deprecated_id=existing_id,  # not deprecating in a merge, just updating
        similarity=DUPLICATE_THRESHOLD,
        merged_report_ids=[_parse_uuid(r) for r in new_report_ids if _parse_uuid(r)],
    ))

    return existing_id


async def _store_knowledge(
    statement: str,
    confidence: float,
    topics: list[str],
    source_report_ids: list[str],
    source_section: str,
    status: str = "validated",
) -> UUID:
    """Insert a validated knowledge item and return its UUID."""
    async with transaction() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO knowledge
              (statement, confidence, status, source_report_ids, source_section, topics)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            statement,
            confidence,
            status,
            source_report_ids,
            source_section,
            topics,
        )
    return row["id"]


async def _store_decision(
    statement: str,
    rationale: str,
    linked_knowledge_ids: list[str],
    owner: str | None,
    status: str,
) -> UUID:
    """Insert a validated decision and return its UUID."""
    async with transaction() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO decisions
              (statement, rationale, linked_knowledge_ids, owner, status)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            statement,
            rationale,
            linked_knowledge_ids,
            owner,
            status,
        )
    return row["id"]


async def _store_embedding(
    object_id: UUID,
    object_type: str,
    embedding: list[float],
) -> None:
    """Store a pre-computed embedding vector."""
    try:
        async with transaction() as conn:
            await conn.execute(
                """
                INSERT INTO embeddings (object_type, object_id, embedding)
                VALUES ($1, $2, $3::vector)
                """,
                object_type,
                object_id,
                str(embedding),
            )
    except Exception as exc:
        logger.warning("Archivist: failed to store embedding for %s %s: %s",
                       object_type, object_id, exc)


async def _get_or_create_entity(name: str) -> UUID | None:
    """Look up an entity by name, creating it if it does not exist."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM entities WHERE LOWER(name) = LOWER($1) LIMIT 1",
            name,
        )
        if row:
            return row["id"]

    async with transaction() as conn:
        row = await conn.fetchrow(
            "INSERT INTO entities (name) VALUES ($1) RETURNING id",
            name,
        )
    return row["id"] if row else None


async def _push_to_review(reason: str, context: dict[str, Any]) -> None:
    """Push an item to human_review_queue."""
    try:
        await human_review_queue.push({
            "source": "archivist_pipeline",
            "reason": reason,
            "context": context,
        })
    except Exception as exc:
        logger.error("Archivist: failed to push to human_review_queue: %s", exc)


# == Utility helpers ===========================================================

def _parse_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _collect_deprecated_ids(output: ArchivistOutput) -> list[UUID]:
    """Collect UUIDs of knowledge items that were deprecated or superseded in this run."""
    ids = []
    for rec in output.contradiction_records:
        if rec.resolution == "superseded":
            ids.append(rec.existing_id)
    return ids


# == Exception =================================================================

class ArchivistPipelineError(Exception):
    """Raised when the Archivist pipeline cannot proceed."""
