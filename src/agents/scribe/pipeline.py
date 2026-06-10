"""
Scribe pipeline — the core processing function.

Implements the pipeline from docs/AGENTS/SCRIBE.md §8:

    Input
      │
      ▼
    Normalize & clean text
      │
      ▼
    LLM Call 1: Generate Report (ReportDraft)
      │
      ▼
    Store report in Postgres → get report_id
      │
      ▼
    Generate embedding → store in embeddings table
      │
      ▼
    LLM Call 2: Extract candidates (knowledge / decisions / relationships)
      │
      ▼
    Contradiction detection (pgvector similarity vs existing validated knowledge)
      │
      ▼
    Push payload → archivist_queue (Redis)
      │
      ▼
    Return ScribeOutput

Two Anthropic API calls per run. Uses tool_use for deterministic JSON output.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import textwrap
from typing import Any
from uuid import UUID

import anthropic

from src.config import settings
from src.db.client import get_conn, transaction
from src.pipelines.queues import archivist_queue, human_review_queue
from src.utils.embeddings import get_provider

from .models import (
    DecisionCandidate,
    ExtractionResult,
    KnowledgeCandidate,
    RelationshipCandidate,
    ReportDraft,
    ScribeOutput,
)
from .prompts import (
    EXTRACT_TOOL,
    REPORT_TOOL,
    SCRIBE_SYSTEM_PROMPT,
    extraction_prompt,
    report_generation_prompt,
)

logger = logging.getLogger(__name__)

# Module-level Anthropic client (created lazily)
_anthropic_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None
        )
    return _anthropic_client


# ── Public entry point ────────────────────────────────────────────────────────

async def run_pipeline(item: dict[str, Any]) -> ScribeOutput:
    """
    Run the full Scribe pipeline on a queue item.

    Args:
        item: Dict with keys: source_type, payload, author (optional), tags (optional)

    Returns:
        ScribeOutput with report_id and candidate counts.

    On unrecoverable failure, raises ScribePipelineError and the caller
    (worker.py) routes the item to human_review_queue.
    """
    source_type = item.get("source_type", "document")
    payload = item.get("payload", {})
    author = item.get("author")
    extra_tags = item.get("tags", [])

    # ── Step 1: Normalize ─────────────────────────────────────────────────────
    source_text = _normalize(source_type, payload)
    if not source_text.strip():
        raise ScribePipelineError("Input text is empty after normalization.")

    logger.info("Scribe: processing %s (source_type=%s, chars=%d)",
                author or "unknown", source_type, len(source_text))

    # ── Step 2: Generate report ───────────────────────────────────────────────
    report_draft = await _generate_report(
        source_type=source_type,
        text=source_text,
        author=author,
        retries=settings.scribe_max_retries,
    )
    report_draft.tags = list(set(report_draft.tags + extra_tags))

    # ── Step 3: Store report ──────────────────────────────────────────────────
    report_id = await _store_report(
        draft=report_draft,
        raw_source=payload,
        author=author,
    )
    logger.info("Scribe: stored report %s — '%s'", report_id, report_draft.title)

    # ── Step 4: Embed report ──────────────────────────────────────────────────
    embed_text = (
        f"{report_draft.title} "
        f"{report_draft.context_summary} "
        f"{report_draft.conclusions}"
    )
    await _store_embedding(report_id, "report", embed_text)

    # ── Step 5: Extract candidates ────────────────────────────────────────────
    extraction = await _extract_candidates(
        report_draft=report_draft,
        source_text=source_text,
        retries=settings.scribe_max_retries,
    )
    logger.info(
        "Scribe: extracted K=%d D=%d R=%d",
        len(extraction.knowledge_candidates),
        len(extraction.decision_candidates),
        len(extraction.relationship_candidates),
    )

    # ── Step 6: Contradiction detection ──────────────────────────────────────
    if extraction.knowledge_candidates:
        extraction.knowledge_candidates = await _check_contradictions(
            extraction.knowledge_candidates
        )

    contradiction_count = sum(
        1 for k in extraction.knowledge_candidates if k.contradiction_flag
    )

    # ── Step 7: Push to archivist_queue ──────────────────────────────────────
    archivist_payload = {
        "knowledge_candidates": [k.model_dump(mode="json") for k in extraction.knowledge_candidates],
        "decision_candidates": [d.model_dump(mode="json") for d in extraction.decision_candidates],
        "relationship_candidates": [r.model_dump(mode="json") for r in extraction.relationship_candidates],
        "source_report_id": str(report_id),
    }
    await archivist_queue.push(archivist_payload)

    # ── Step 8: Return result ─────────────────────────────────────────────────
    status = "needs_review" if contradiction_count > 0 else "processed"
    return ScribeOutput(
        report_id=report_id,
        status=status,
        knowledge_candidates=len(extraction.knowledge_candidates),
        decision_candidates=len(extraction.decision_candidates),
        relationship_candidates=len(extraction.relationship_candidates),
        contradiction_flags=contradiction_count,
        reason=f"{contradiction_count} contradiction(s) flagged" if contradiction_count else None,
    )


# ── Step implementations ──────────────────────────────────────────────────────

def _normalize(source_type: str, payload: dict[str, Any]) -> str:
    """
    Extract a clean text string from the raw payload.

    Supports:
    - conversation: payload["messages"] list of {role, content} dicts
    - task: payload["log"] string
    - document: payload["text"] string
    - fallback: json.dumps the whole payload
    """
    if source_type == "conversation":
        messages = payload.get("messages", [])
        lines = []
        for m in messages:
            role = m.get("role", "unknown").upper()
            content = m.get("content", "")
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    elif source_type == "task":
        return payload.get("log", payload.get("text", json.dumps(payload, indent=2)))

    elif source_type == "document":
        return payload.get("text", payload.get("content", json.dumps(payload, indent=2)))

    else:
        # Generic fallback: stringify payload
        return json.dumps(payload, indent=2, ensure_ascii=False)


async def _generate_report(
    source_type: str,
    text: str,
    author: str | None,
    retries: int = 2,
) -> ReportDraft:
    """
    LLM Call 1: Generate a structured ReportDraft from source text.

    Uses Anthropic tool_use to enforce the output schema.
    Retries up to `retries` times on malformed output.
    """
    client = _get_client()
    user_message = report_generation_prompt(source_type, text, author)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.messages.create(
                model=settings.scribe_model,
                max_tokens=2048,
                system=SCRIBE_SYSTEM_PROMPT,
                tools=[REPORT_TOOL],
                tool_choice={"type": "tool", "name": "generate_report"},
                messages=[{"role": "user", "content": user_message}],
            )

            tool_result = _extract_tool_result(response, "generate_report")
            return ReportDraft(**tool_result)

        except (KeyError, ValueError, TypeError) as exc:
            last_error = exc
            logger.warning("Scribe report generation attempt %d failed: %s", attempt + 1, exc)

    raise ScribePipelineError(
        f"Report generation failed after {retries + 1} attempts: {last_error}"
    )


async def _extract_candidates(
    report_draft: ReportDraft,
    source_text: str,
    retries: int = 2,
) -> ExtractionResult:
    """
    LLM Call 2: Extract knowledge, decision, and relationship candidates.

    Uses Anthropic tool_use for deterministic JSON output.
    """
    client = _get_client()

    # Build the report text block to include in the prompt
    report_text = (
        f"Context: {report_draft.context_summary}\n\n"
        f"Analysis: {report_draft.analysis}\n\n"
        f"Conclusions: {report_draft.conclusions}"
    )
    user_message = extraction_prompt(
        report_title=report_draft.title,
        report_text=report_text,
        source_text=source_text,
    )

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.messages.create(
                model=settings.scribe_model,
                max_tokens=4096,
                system=SCRIBE_SYSTEM_PROMPT,
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": "extract_candidates"},
                messages=[{"role": "user", "content": user_message}],
            )

            raw = _extract_tool_result(response, "extract_candidates")

            knowledge = [KnowledgeCandidate(**k) for k in raw.get("knowledge_candidates", [])]
            decisions = [DecisionCandidate(**d) for d in raw.get("decision_candidates", [])]
            relationships = [RelationshipCandidate(**r) for r in raw.get("relationship_candidates", [])]

            return ExtractionResult(
                knowledge_candidates=knowledge,
                decision_candidates=decisions,
                relationship_candidates=relationships,
            )

        except (KeyError, ValueError, TypeError) as exc:
            last_error = exc
            logger.warning("Scribe extraction attempt %d failed: %s", attempt + 1, exc)

    raise ScribePipelineError(
        f"Candidate extraction failed after {retries + 1} attempts: {last_error}"
    )


async def _check_contradictions(
    candidates: list[KnowledgeCandidate],
) -> list[KnowledgeCandidate]:
    """
    Compare each knowledge candidate against existing validated knowledge
    using pgvector cosine similarity.

    Flags candidates that are suspiciously close to existing items.
    The Archivist will classify and resolve flagged contradictions.

    Threshold: settings.contradiction_threshold (default: 0.15)
    Lower distance = more similar. Distance < threshold = flag.
    """
    provider = get_provider()
    threshold = settings.contradiction_threshold

    # Embed all candidates in a single batch call (efficient)
    statements = [c.statement for c in candidates]
    try:
        embeddings = await provider.embed_batch(statements)
    except Exception as exc:
        logger.warning("Scribe: embedding failed during contradiction check: %s", exc)
        return candidates  # skip contradiction check, proceed without flags

    async with get_conn() as conn:
        for i, (candidate, embedding) in enumerate(zip(candidates, embeddings)):
            try:
                row = await conn.fetchrow(
                    """
                    SELECT k.id, k.statement, e.embedding <-> $1::vector AS distance
                    FROM embeddings e
                    JOIN knowledge k ON k.id = e.object_id
                    WHERE e.object_type = 'knowledge'
                      AND k.status = 'validated'
                    ORDER BY e.embedding <-> $1::vector
                    LIMIT 1
                    """,
                    str(embedding),
                )

                if row and row["distance"] < threshold:
                    candidate.contradiction_flag = True
                    candidate.contradicts_id = row["id"]
                    # Classify contradiction type heuristically.
                    # The Archivist will validate and may reclassify.
                    candidate.contradiction_type = _classify_contradiction(
                        candidate.statement, row["statement"]
                    )
                    logger.info(
                        "Scribe: contradiction flagged for K%d — distance=%.3f — "
                        "contradicts %s (%s)",
                        i, row["distance"], row["id"], candidate.contradiction_type,
                    )

            except Exception as exc:
                # Don't fail the whole pipeline on contradiction check errors
                logger.warning("Scribe: contradiction check failed for K%d: %s", i, exc)

    return candidates


def _classify_contradiction(new_statement: str, existing_statement: str) -> str:
    """
    Heuristic classification of contradiction type.

    The Archivist will validate this classification.

    Rules:
    - "temporal": new statement contains temporal markers (year, quarter, month, date)
                  suggesting it may be an update rather than a true conflict
    - "direct":   statements are very similar (high lexical overlap) with a
                  conflicting value
    - "contextual": otherwise — different scope or partial overlap
    """
    temporal_patterns = re.compile(
        r"\b(20\d\d|q[1-4]|january|february|march|april|may|june|"
        r"july|august|september|october|november|december|"
        r"this year|last year|next year|current|previous|recent)\b",
        re.IGNORECASE,
    )

    if temporal_patterns.search(new_statement) or temporal_patterns.search(existing_statement):
        return "temporal"

    # Lexical overlap check
    new_words = set(new_statement.lower().split())
    existing_words = set(existing_statement.lower().split())
    overlap = len(new_words & existing_words) / max(len(new_words | existing_words), 1)

    if overlap > 0.6:
        return "direct"

    return "contextual"


async def _store_report(
    draft: ReportDraft,
    raw_source: dict[str, Any],
    author: str | None,
) -> UUID:
    """Insert a ReportDraft into the reports table and return its UUID."""
    async with transaction() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO reports
              (title, author, context_summary, analysis, conclusions, raw_source, tags)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            draft.title,
            author,
            draft.context_summary,
            draft.analysis,
            draft.conclusions,
            json.dumps(raw_source),
            draft.tags,
        )
    return row["id"]


async def _store_embedding(
    object_id: UUID,
    object_type: str,
    text: str,
) -> None:
    """Generate an embedding for `text` and store it in the embeddings table."""
    provider = get_provider()
    try:
        vector = await provider.embed(text)
    except Exception as exc:
        logger.warning("Scribe: embedding generation failed for %s %s: %s",
                       object_type, object_id, exc)
        return  # embeddings are non-blocking — proceed without them

    async with transaction() as conn:
        await conn.execute(
            """
            INSERT INTO embeddings (object_type, object_id, embedding)
            VALUES ($1, $2, $3::vector)
            """,
            object_type,
            object_id,
            str(vector),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_tool_result(response: anthropic.types.Message, tool_name: str) -> dict[str, Any]:
    """
    Pull the tool_use input from an Anthropic response.
    Raises ValueError if the expected tool call is not present.
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise ValueError(
        f"Anthropic response did not contain a '{tool_name}' tool call. "
        f"Stop reason: {response.stop_reason}. "
        f"Content types: {[b.type for b in response.content]}"
    )


# ── Exception ─────────────────────────────────────────────────────────────────

class ScribePipelineError(Exception):
    """Raised when the Scribe pipeline cannot proceed and the item must be routed to human review."""
