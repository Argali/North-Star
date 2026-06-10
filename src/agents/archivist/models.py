"""
Pydantic models for the Archivist pipeline.

These represent the outcomes of the Archivist's decisions:
what was validated, merged, deprecated, flagged, or rejected.
"""
from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


# -- Validation outcome for a single knowledge candidate --

class KnowledgeOutcome(str, Enum):
    VALIDATED = "validated"
    MERGED    = "merged"       # duplicate, merged into existing item
    REJECTED  = "rejected"     # failed validation (no provenance, malformed, etc.)
    REVIEW    = "review"       # genuine contradiction or ambiguous -- routed to human


class DecisionOutcome(str, Enum):
    VALIDATED = "validated"
    REJECTED  = "rejected"     # missing knowledge link or rationale
    REVIEW    = "review"


class ValidationRecord(BaseModel):
    """
    Result of validating one knowledge or decision candidate.
    Stored in the Archivist output for audit trail.
    """
    index: int                         # 0-based index in the incoming candidates list
    statement: str
    outcome: KnowledgeOutcome | DecisionOutcome
    stored_id: UUID | None = None      # UUID assigned by DB on successful insert
    merged_into: UUID | None = None    # UUID of the canonical item (when outcome=MERGED)
    reason: str | None = None          # rejection or review reason


# -- Merge record --

class MergeRecord(BaseModel):
    """Tracks when two knowledge items were merged into one."""
    canonical_id: UUID       # the item that survives
    deprecated_id: UUID      # the item that was deprecated
    similarity: float        # cosine similarity that triggered the merge
    merged_report_ids: list[UUID] = Field(default_factory=list)


# -- Contradiction record --

class ContradictionRecord(BaseModel):
    """Tracks a detected contradiction between two knowledge items."""
    new_item_index: int           # index in incoming candidates
    existing_id: UUID             # UUID of the existing validated knowledge
    contradiction_type: str       # "direct" | "temporal" | "contextual"
    resolution: str               # "superseded" | "flagged" | "routed_to_review"
    new_item_id: UUID | None = None  # set if the new item was stored despite contradiction


# -- Impact trace record --

class ImpactRecord(BaseModel):
    """Records when a knowledge change forces a decision to needs_reassessment."""
    decision_id: UUID
    triggered_by_knowledge_id: UUID
    previous_status: str
    reason: str


# -- Archivist output --

class ArchivistOutput(BaseModel):
    """
    Final result of the Archivist pipeline run.
    Matches the API response schema in docs/AGENTS/ARCHIVIST.md section 11.
    """
    source_report_id: UUID | None = None

    validated_knowledge: int = 0
    validated_decisions: int = 0
    merged: int = 0
    deprecated: int = 0
    relationships_inserted: int = 0
    contradictions_flagged: int = 0
    review_requests: int = 0
    rejected: int = 0

    # Detailed audit records
    knowledge_records: list[ValidationRecord] = Field(default_factory=list)
    decision_records: list[ValidationRecord] = Field(default_factory=list)
    merge_records: list[MergeRecord] = Field(default_factory=list)
    contradiction_records: list[ContradictionRecord] = Field(default_factory=list)
    impact_records: list[ImpactRecord] = Field(default_factory=list)
