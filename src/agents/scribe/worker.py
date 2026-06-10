"""
Scribe queue worker.

Runs as a long-lived process that:
  1. Listens on `scribe_queue` (Redis BRPOP)
  2. Calls run_pipeline() on each item
  3. On success: result is already pushed to archivist_queue inside the pipeline
  4. On failure: routes item to human_review_queue with error context

Usage:

    # Run directly:
    python -m src.agents.scribe.worker

    # Or import and run from another process:
    from src.agents.scribe.worker import run_worker
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
    close_redis,
    get_redis,
    human_review_queue,
    scribe_queue,
)

from .pipeline import ScribePipelineError, run_pipeline

logger = logging.getLogger(__name__)

# ── Graceful shutdown ─────────────────────────────────────────────────────────

_shutdown = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    logger.info("Scribe worker: received %s — shutting down after current item.", sig.name)
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
    logger.info("Scribe worker starting.")

    # Register signal handlers for clean shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s))

    consecutive_failures = 0

    while not _shutdown.is_set():

        # ── Pop next item from scribe_queue ───────────────────────────────────
        try:
            item = await scribe_queue.pop(timeout=pop_timeout)
        except Exception as exc:
            logger.error("Scribe worker: failed to pop from queue: %s", exc)
            await asyncio.sleep(2)
            continue

        if item is None:
            # Timeout — no item available, loop back to check shutdown flag
            continue

        # ── Process item ──────────────────────────────────────────────────────
        source_type = item.get("source_type", "unknown")
        author = item.get("author", "unknown")
        logger.info("Scribe worker: processing item (source_type=%s, author=%s)",
                    source_type, author)

        try:
            result = await run_pipeline(item)
            consecutive_failures = 0

            logger.info(
                "Scribe worker: ✓ %s | report=%s status=%s "
                "K=%d D=%d R=%d contradictions=%d",
                result.report_id,
                result.report_id,
                result.status,
                result.knowledge_candidates,
                result.decision_candidates,
                result.relationship_candidates,
                result.contradiction_flags,
            )

        except ScribePipelineError as exc:
            consecutive_failures += 1
            logger.error("Scribe worker: pipeline error — routing to human_review_queue: %s", exc)
            await _route_to_human_review(item, error=str(exc), reason="pipeline_error")

        except Exception as exc:
            consecutive_failures += 1
            logger.exception("Scribe worker: unexpected error — routing to human_review_queue")
            await _route_to_human_review(item, error=str(exc), reason="unexpected_error")

        # ── Circuit breaker ───────────────────────────────────────────────────
        if consecutive_failures >= max_failures:
            logger.critical(
                "Scribe worker: %d consecutive failures — stopping worker. "
                "Check human_review_queue and fix the issue before restarting.",
                consecutive_failures,
            )
            _shutdown.set()

    logger.info("Scribe worker: shutdown complete.")


async def _route_to_human_review(
    item: dict[str, Any],
    error: str,
    reason: str,
) -> None:
    """Push a failed item to human_review_queue with error context."""
    review_item = {
        "source": "scribe_worker",
        "reason": reason,
        "error": error,
        "original_item": item,
    }
    try:
        await human_review_queue.push(review_item)
        logger.info("Scribe worker: item routed to human_review_queue.")
    except Exception as exc:
        logger.error(
            "Scribe worker: CRITICAL — could not route to human_review_queue: %s. "
            "Item lost: %s",
            exc,
            item,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

async def _main() -> None:
    """Bootstrap DB + Redis connections, then start the worker loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info("Scribe worker: connecting to Postgres and Redis...")
    await init_pool()
    await get_redis()

    try:
        await run_worker()
    finally:
        logger.info("Scribe worker: closing connections.")
        await close_pool()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(_main())
