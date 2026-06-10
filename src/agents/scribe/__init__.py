"""
Scribe agent -- transforms raw activity into structured institutional memory.

Public interface:
    from src.agents.scribe import run_pipeline, run_worker, ScribeOutput

See docs/AGENTS/SCRIBE.md for the full specification.
"""
from .models import DecisionCandidate, ExtractionResult, KnowledgeCandidate
from .models import RelationshipCandidate, ReportDraft, ScribeOutput
from .pipeline import ScribePipelineError, run_pipeline
from .worker import run_worker

__all__ = [
    "run_pipeline",
    "run_worker",
    "ScribeOutput",
    "ScribePipelineError",
    "KnowledgeCandidate",
    "DecisionCandidate",
    "RelationshipCandidate",
    "ReportDraft",
    "ExtractionResult",
]
