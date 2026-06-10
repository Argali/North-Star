"""
TODO: Replace <AgentName> and <queue_name> throughout this file.

<AgentName> queue worker.

Usage:
    python -m src.agents.<agent_name>.worker
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
)

# TODO: import your queue singleton from src/pipelines/queues.py
# from src.pipelines.queues import <queue_name>_queue as agent_queue

from .pipeline import <AgentName>PipelineError, run_pipeline

logger = logging.getLogger(__name__)

_shutdown = asyncio.Event()


def _handle_signal(sig: signal.Signals) -> None:
    logger.info("<AgentName> worker: received %s — shutting down.", sig.name)
    _shutdown.set()


async def run_worker(*, pop_timeout: int = 5, max_failures: int = 10) -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s))

    consecutive_failures = 0

    while not _shutdown.is_set():
        try:
            # TODO: replace agent_queue with your queue singleton
            item = await agent_queue.pop(timeout=pop_timeout)
        except Exception as exc:
            logger.error("<AgentName> worker: queue pop failed: %s", exc)
            await asyncio.sleep(2)
            continue

        if item is None:
            continue

        try:
            result = await run_pipeline(item)
            consecutive_failures = 0
            logger.info("<AgentName> worker: processed item — %s", result)

        except <AgentName>PipelineError as exc:
            consecutive_failures += 1
            logger.error("<AgentName> worker: pipeline error: %s", exc)
            await human_review_queue.push({
                "source": "<agent_name>_worker",
                "reason": "pipeline_error",
                "error": str(exc),
                "original_item": item,
            })

        except Exception as exc:
            consecutive_failures += 1
            logger.exception("<AgentName> worker: unexpected error")
            await human_review_queue.push({
                "source": "<agent_name>_worker",
                "reason": "unexpected_error",
                "error": str(exc),
                "original_item": item,
            })

        if consecutive_failures >= max_failures:
            logger.critical("<AgentName> worker: circuit breaker — stopping.")
            _shutdown.set()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        stream=sys.stdout)
    await init_pool()
    await get_redis()
    try:
        await run_worker()
    finally:
        await close_pool()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(_main())
