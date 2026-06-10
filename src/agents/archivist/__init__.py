"""
Archivist agent -- the quality gate of North Star's institutional memory.

Validates, deduplicates, resolves contradictions, and stores knowledge
extracted by the Scribe agent.

Public interface:
    from src.agents.archivist import run_pipeline, run_worker, ArchivistOutput

See docs/AGENTS/ARCHIVIST.md for the full specification.
"""
from .models import (
    ArchivistOutput,
    ContradictionRecord,
    DecisionOutcome,
    ImpactRecord,
    KnowledgeOutcome,
    MergeRecord,
    ValidationRecord,
)
from .pipeline import ArchivistPipelineError, run_pipeline
from .worker import run_worker

__all__ = [
    "run_pipeline",
    "run_worker",
    "ArchivistOutput",
    "ArchivistPipelineError",
    "KnowledgeOutcome",
    "DecisionOutcome",
    "ValidationRecord",
    "MergeRecord",
    "ContradictionRecord",
    "ImpactRecord",
]
