"""
Archivist queue worker.

Runs as a long-lived process that:
  1. Listens on `archivist_queue` (Redis BRPOP)
  2. Calls run_pipeline() on each item
  3. On success: logs the ArchivistOutput audit summary
  4. On failure: routes item to human_review_queue with error context

Usage:

    # Run directly:
    python -m src.agents.archivist.worker

    # Or import and run from another process:
    from src.agents.archivist.worker import run_worker
    import asyncio
    asyncio.run(run_worker())
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from src.db.client import close_pool, init_pool
from src.pipelines.queues import (
    archivist_queue,
    close_redis,
    get_redis,
    human_review_queue,
)

from .pipeline import ArchivistPipelineError, run_pipeline

logger = logging.getLogger(__name__)

# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    logger.info("Archivist worker: received %s — shutting down after current item.", sig.name)
    _shutdown.set()


# ── Worker ────────────────────────────────────────────────────────────────────

async def run_worker(
    *,
    pop_timeout: int = 5,
    max_failures: int = 10,
) -> None:
    """
    Main worker loop. Runs until SIGINT/SIGTERM or max_failures consecutive errors.

    Args:
        pop_timeout: Seconds to block on BRPOP before looping (enables shutdown checks).
        max_failures: Stop the worker if this many consecutive items fail (circuit breaker).
    """
    logger.info("Archivist worker starting.")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s))

    consecutive_failures = 0

    while not _shutdown.is_set():

        # ── Pop next item from archivist_queue ────────────────────────────────
        try:
            item = await archivist_queue.pop(timeout=pop_timeout)
        except Exception as exc:
            logger.error("Archivist worker: failed to pop from queue: %s", exc)
            await asyncio.sleep(2)
            continue

        if item is None:
            continue

        source_report_id = item.get("source_report_id", "unknown")
        logger.info(
            "Archivist worker: processing batch (source_report=%s, K=%d, D=%d, R=%d)",
            source_report_id,
            len(item.get("knowledge_candidates", [])),
            len(item.get("decision_candidates", [])),
            len(item.get("relationship_candidates", [])),
        )

        # ── Process item ──────────────────────────────────────────────────────
        try:
            result = await run_pipeline(item)
            consecutive_failures = 0

            logger.info(
                "Archivist worker: ✓ report=%s | "
                "validated_K=%d validated_D=%d merged=%d deprecated=%d "
                "relationships=%d contradictions=%d review=%d rejected=%d",
                source_report_id,
                result.validated_knowledge,
                result.validated_decisions,
                result.merged,
                result.deprecated,
                result.relationships_inserted,
                result.contradictions_flagged,
                result.review_requests,
                result.rejected,
            )

            # Log impact records at WARN level — these need human awareness
            for impact in result.impact_records:
                logger.warning(
                    "Archivist worker: decision %s flagged needs_reassessment "
                    "(triggered by knowledge %s)",
                    impact.decision_id,
                    impact.triggered_by_knowledge_id,
                )

        except ArchivistPipelineError as exc:
            consecutive_failures += 1
            logger.error(
                "Archivist worker: pipeline error — routing to human_review_queue: %s", exc
            )
            await _route_to_human_review(item, error=str(exc), reason="pipeline_error")

        except Exception as exc:
            consecutive_failures += 1
            logger.exception("Archivist worker: unexpected error — routing to human_review_queue")
            await _route_to_human_review(item, error=str(exc), reason="unexpected_error")

        # ── Circuit breaker ───────────────────────────────────────────────────
        if consecutive_failures >= max_failures:
            logger.critical(
                "Archivist worker: %d consecutive failures — stopping worker. "
                "Check human_review_queue and fix the issue before restarting.",
                consecutive_failures,
            )
            _shutdown.set()

    logger.info("Archivist worker: shutdown complete.")


async def _route_to_human_review(
    item: dict[str, Any],
    error: str,
    reason: str,
) -> None:
    """Push a failed item to human_review_queue with error context."""
    review_item = {
        "source": "archivist_worker",
        "reason": reason,
        "error": error,
        "source_report_id": item.get("source_report_id"),
        "original_item_summary": {
            "knowledge_candidates": len(item.get("knowledge_candidates", [])),
            "decision_candidates": len(item.get("decision_candidates", [])),
            "relationship_candidates": len(item.get("relationship_candidates", [])),
        },
    }
    try:
        await human_review_queue.push(review_item)
        logger.info("Archivist worker: item routed to human_review_queue.")
    except Exception as exc:
        logger.error(
            "Archivist worker: CRITICAL — could not route to human_review_queue: %s. "
            "Source report: %s",
            exc,
            item.get("source_report_id"),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

async def _main() -> None:
    """Bootstrap DB + Redis connections, then start the worker loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info("Archivist worker: connecting to Postgres and Redis...")
    await init_pool()
    await get_redis()

    try:
        await run_worker()
    finally:
        logger.info("Archivist worker: closing connections.")
        await close_pool()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(_main())
