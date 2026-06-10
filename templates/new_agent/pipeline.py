"""
TODO: Replace <AgentName> with your agent's name throughout this file.

<AgentName> pipeline.

Pipeline stages (adapt as needed):
  1. Validate input
  2. Fetch context (DB / embeddings)
  3. LLM call (optional — use Anthropic tool_use for structured output)
  4. Write results to DB
  5. Push to downstream queue (if applicable)
  6. Return output

One Anthropic API call should use tool_use for deterministic JSON extraction.
See src/agents/scribe/pipeline.py for a reference implementation.
"""
from __future__ import annotations

import logging
from typing import Any

from src.config import settings
from src.db.client import get_conn, transaction

from .models import <AgentName>Input, <AgentName>Output, <AgentName>PipelineError

logger = logging.getLogger(__name__)


async def run_pipeline(item: dict[str, Any]) -> <AgentName>Output:
    """
    Run the full <AgentName> pipeline on a queue item.

    Args:
        item: Dict popped from the agent's Redis queue.

    Returns:
        <AgentName>Output with counts and audit records.

    Raises:
        <AgentName>PipelineError: on unrecoverable failure (worker routes to human_review_queue).
    """
    # TODO: Parse and validate the item
    try:
        parsed = <AgentName>Input(**item)
    except Exception as exc:
        raise <AgentName>PipelineError(f"Invalid input: {exc}") from exc

    output = <AgentName>Output(source_report_id=parsed.source_report_id)

    # TODO: Implement your pipeline stages here.
    # Each stage should update output counts and append audit records.

    # Example stage skeleton:
    # results = await _stage_one(parsed)
    # output.processed += len(results)

    return output


# TODO: Add private helper functions for each pipeline stage below.
# Keep them small and single-purpose. Name them _stage_name().
