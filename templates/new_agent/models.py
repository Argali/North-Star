"""
TODO: Replace <AgentName> with your agent's name throughout this file.

Models for the <AgentName> agent.

Each agent should define:
  - An input model (what it receives from the queue)
  - An output model (what it returns / pushes downstream)
  - Any intermediate models needed for its pipeline stages

See src/agents/scribe/models.py for a reference implementation.
"""
from __future__ import annotations

from pydantic import BaseModel
from uuid import UUID


class <AgentName>Input(BaseModel):
    """
    TODO: Define the fields this agent expects from its queue item.

    Required: at minimum a source_report_id for provenance tracking.
    """
    source_report_id: UUID | None = None
    # TODO: add your fields here


class <AgentName>Output(BaseModel):
    """
    TODO: Define the fields this agent returns.

    Convention: include counts for each type of item processed
    and a list of audit records for traceability.
    """
    source_report_id: UUID | None = None
    processed: int = 0
    rejected: int = 0
    review_requests: int = 0
    # TODO: add your output fields here


class <AgentName>PipelineError(Exception):
    """Raised when the pipeline cannot proceed and the item must be routed to human review."""
