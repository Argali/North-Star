"""
Pydantic models for the Scribe pipeline.

These are the internal data structures used during processing.
They are not the same as the database schema (in docs/DATABASE/SCHEMA.md)
— they are the intermediate representations the Scribe produces before
the Archivist validates and stores them.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


# ── Knowledge candidate ───────────────────────────────────────────────────────

class KnowledgeCandidate(BaseModel):
    """
    An atomic fact extracted by the Scribe.
    Awaiting Archivist validation before becoming a Knowledge record.

    The strict extraction template from docs/AGENTS/SCRIBE.md:
    - statement       : one declarative fact
    - confidence      : 0.0–1.0
    - scope_conditions: when/where this applies
    - uncertainties   : what this doesn't account for
    - source_excerpt  : verbatim or near-verbatim source text
    - source_section  : section/paragraph reference in the report
    - topics          : classification tags
    """
    statement: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    scope_conditions: str
    uncertainties: str
    source_excerpt: str
    source_section: str
    topics: list[str] = Field(default_factory=list)

    # ── Contradiction detection fields ─────────────────────────────────────
    # Set by the pipeline's contradiction check step, not by the LLM.
    contradiction_flag: bool = False
    contradicts_id: UUID | None = None
    # "direct" = same claim, conflicting value
    # "temporal" = same claim, different time period (may be an update)
    # "contextual" = similar but different scope/conditions
    contradiction_type: str | None = None


# ── Decision candidate ────────────────────────────────────────────────────────

class DecisionCandidate(BaseModel):
    """
    An explicit organisational choice extracted by the Scribe.

    `linked_knowledge_refs` are 0-based indexes into the
    `knowledge_candidates` list from the same extraction batch.
    The Archivist resolves them to actual UUIDs when validating.
    """
    statement: str
    rationale: str
    # 0-based indexes into knowledge_candidates in the same extraction result
    linked_knowledge_refs: list[int] = Field(default_factory=list)
    owner: str | None = None
    status: str = "planned"   # "planned" | "executed"


# ── Relationship candidate ─────────────────────────────────────────────────────

class RelationshipCandidate(BaseModel):
    """
    A proposed graph edge between two nodes.

    `from_ref` and `to_ref` use a ref system:
    - "report"  → the report generated in this run
    - "K0".."KN" → knowledge_candidates[N] in this batch
    - "D0".."DN" → decision_candidates[N] in this batch
    - "E:<name>" → an entity to be resolved by name (Archivist looks up or creates)
    - "<UUID>"   → an existing node already in the database

    Edge types: supports | informs | contradicts | relates_to
    """
    from_ref: str
    to_ref: str
    type: str   # "supports" | "informs" | "contradicts" | "relates_to"


# ── Report draft ──────────────────────────────────────────────────────────────

class ReportDraft(BaseModel):
    """
    Structured report produced by the first LLM call.
    Matches the `reports` table schema (minus id/created_at which are DB-assigned).
    """
    title: str
    context_summary: str
    analysis: str
    conclusions: str
    tags: list[str] = Field(default_factory=list)


# ── Extraction result ─────────────────────────────────────────────────────────

class ExtractionResult(BaseModel):
    """
    Combined output of the candidate extraction LLM call.
    All three candidate lists are populated in one call to minimise API round-trips.
    """
    knowledge_candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    decision_candidates: list[DecisionCandidate] = Field(default_factory=list)
    relationship_candidates: list[RelationshipCandidate] = Field(default_factory=list)


# ── Scribe output ─────────────────────────────────────────────────────────────

class ScribeOutput(BaseModel):
    """
    Final result returned by the Scribe pipeline.
    Matches the API response schema in docs/AGENTS/SCRIBE.md §9.
    """
    report_id: UUID
    status: str   # "processed" | "needs_review"
    knowledge_candidates: int
    decision_candidates: int
    relationship_candidates: int
    contradiction_flags: int
    reason: str | None = None   # populated when status = "needs_review"
