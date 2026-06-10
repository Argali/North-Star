"""
Embedding backfill script — Phase 6 Lite
=========================================

Re-embeds objects that have no embedding or whose embedding dimension
does not match the current model's output dimension.

Usage
-----
    python -m src.tools.backfill --object-type knowledge
    python -m src.tools.backfill --object-type reports
    python -m src.tools.backfill --object-type decisions --batch-size 50
    python -m src.tools.backfill --object-type knowledge --dry-run

Object types: knowledge, reports, decisions, entities
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Any
from uuid import UUID

logger = logging.getLogger("northstar.backfill")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OBJECT_TYPES = ("knowledge", "reports", "decisions", "entities")

# SQL to fetch objects missing embeddings (or with wrong dimension)
# The embedding dimension check is done in Python after fetching.
FETCH_SQL: dict[str, str] = {
    "knowledge": """
        SELECT k.id, k.statement AS text
        FROM knowledge k
        LEFT JOIN embeddings e
          ON e.object_type = 'knowledge' AND e.object_id = k.id
        WHERE e.id IS NULL
           OR array_length(e.vector, 1) IS DISTINCT FROM $1
        ORDER BY k.valid_from ASC
        LIMIT $2 OFFSET $3
    """,
    "reports": """
        SELECT r.id, r.raw_content AS text
        FROM reports r
        LEFT JOIN embeddings e
          ON e.object_type = 'report' AND e.object_id = r.id
        WHERE e.id IS NULL
           OR array_length(e.vector, 1) IS DISTINCT FROM $1
        ORDER BY r.created_at ASC
        LIMIT $2 OFFSET $3
    """,
    "decisions": """
        SELECT d.id, d.statement AS text
        FROM decisions d
        LEFT JOIN embeddings e
          ON e.object_type = 'decision' AND e.object_id = d.id
        WHERE e.id IS NULL
           OR array_length(e.vector, 1) IS DISTINCT FROM $1
        ORDER BY d.created_at ASC
        LIMIT $2 OFFSET $3
    """,
    "entities": """
        SELECT en.id, (en.name || ' ' || COALESCE(en.description, '')) AS text
        FROM entities en
        LEFT JOIN embeddings e
          ON e.object_type = 'entity' AND e.object_id = en.id
        WHERE e.id IS NULL
           OR array_length(e.vector, 1) IS DISTINCT FROM $1
        ORDER BY en.name ASC
        LIMIT $2 OFFSET $3
    """,
}

# Map CLI object_type → embedding object_type tag used in the embeddings table
EMBED_TYPE_TAG: dict[str, str] = {
    "knowledge": "knowledge",
    "reports": "report",
    "decisions": "decision",
    "entities": "entity",
}

COUNT_SQL: dict[str, str] = {
    "knowledge": """
        SELECT COUNT(*) FROM knowledge k
        LEFT JOIN embeddings e ON e.object_type = 'knowledge' AND e.object_id = k.id
        WHERE e.id IS NULL OR array_length(e.vector, 1) IS DISTINCT FROM $1
    """,
    "reports": """
        SELECT COUNT(*) FROM reports r
        LEFT JOIN embeddings e ON e.object_type = 'report' AND e.object_id = r.id
        WHERE e.id IS NULL OR array_length(e.vector, 1) IS DISTINCT FROM $1
    """,
    "decisions": """
        SELECT COUNT(*) FROM decisions d
        LEFT JOIN embeddings e ON e.object_type = 'decision' AND e.object_id = d.id
        WHERE e.id IS NULL OR array_length(e.vector, 1) IS DISTINCT FROM $1
    """,
    "entities": """
        SELECT COUNT(*) FROM entities en
        LEFT JOIN embeddings e ON e.object_type = 'entity' AND e.object_id = en.id
        WHERE e.id IS NULL OR array_length(e.vector, 1) IS DISTINCT FROM $1
    """,
}


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

async def _embed_text(text: str) -> list[float] | None:
    """Embed a single text string using the configured embedding model."""
    from src.services.embeddings import embed  # local import to allow DB init first
    try:
        return await embed(text)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return None


async def _upsert_embedding(
    conn: Any,
    object_id: UUID,
    object_type: str,
    vector: list[float],
) -> None:
    await conn.execute(
        """
        INSERT INTO embeddings (object_type, object_id, vector, model)
        VALUES ($1, $2, $3::vector, $4)
        ON CONFLICT (object_type, object_id)
        DO UPDATE SET vector = EXCLUDED.vector,
                      model  = EXCLUDED.model,
                      updated_at = NOW()
        """,
        object_type,
        object_id,
        vector,
        "text-embedding-3-small",  # updated if settings changes
    )


async def run_backfill(
    object_type: str,
    batch_size: int = 100,
    dry_run: bool = False,
    delay_ms: int = 50,
) -> dict[str, int]:
    """
    Main backfill loop.

    Returns a stats dict:
        total_missing: int  — objects needing backfill (at start)
        processed:     int  — objects attempted
        succeeded:     int  — objects successfully embedded + stored
        failed:        int  — objects that errored
        skipped:       int  — objects skipped in dry-run mode
    """
    from src.db.client import get_conn
    from src.services.embeddings import EMBEDDING_DIM  # expected dimension

    stats = {
        "total_missing": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
    }

    embed_tag = EMBED_TYPE_TAG[object_type]

    async with get_conn() as conn:
        total = await conn.fetchval(COUNT_SQL[object_type], EMBEDDING_DIM)
        stats["total_missing"] = total
        logger.info(
            "Backfill %s: %d objects need embedding (dim=%d). dry_run=%s",
            object_type, total, EMBEDDING_DIM, dry_run,
        )

        if total == 0:
            logger.info("Nothing to do.")
            return stats

        if dry_run:
            stats["skipped"] = total
            logger.info("[DRY RUN] Would process %d %s objects.", total, object_type)
            return stats

        offset = 0
        while True:
            rows = await conn.fetch(
                FETCH_SQL[object_type],
                EMBEDDING_DIM,
                batch_size,
                offset,
            )
            if not rows:
                break

            logger.info(
                "Batch offset=%d size=%d/%d ...",
                offset, len(rows), total,
            )

            for row in rows:
                obj_id: UUID = row["id"]
                text: str = (row["text"] or "").strip()
                stats["processed"] += 1

                if not text:
                    logger.warning("  [%s] empty text — skipping", obj_id)
                    stats["failed"] += 1
                    continue

                vector = await _embed_text(text)
                if vector is None:
                    logger.error("  [%s] embedding failed", obj_id)
                    stats["failed"] += 1
                    continue

                try:
                    await _upsert_embedding(conn, obj_id, embed_tag, vector)
                    stats["succeeded"] += 1
                    logger.debug("  [%s] ✓", obj_id)
                except Exception as exc:
                    logger.error("  [%s] DB write failed: %s", obj_id, exc)
                    stats["failed"] += 1

                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)

            offset += batch_size
            if offset >= total:
                break

    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.tools.backfill",
        description="Re-embed North Star objects that are missing or stale embeddings.",
    )
    p.add_argument(
        "--object-type",
        choices=OBJECT_TYPES,
        required=True,
        help="Which table to backfill.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Objects per DB fetch (default 100).",
    )
    p.add_argument(
        "--delay-ms",
        type=int,
        default=50,
        help="Delay between embedding calls in ms (rate-limit safety, default 50).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report what would be backfilled without making changes.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable DEBUG logging.",
    )
    return p


async def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    start = time.monotonic()
    stats = await run_backfill(
        object_type=args.object_type,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        delay_ms=args.delay_ms,
    )
    elapsed = time.monotonic() - start

    print("\n── Backfill complete ─────────────────────────────")
    print(f"  object type   : {args.object_type}")
    print(f"  dry run       : {args.dry_run}")
    print(f"  total missing : {stats['total_missing']}")
    print(f"  processed     : {stats['processed']}")
    print(f"  succeeded     : {stats['succeeded']}")
    print(f"  failed        : {stats['failed']}")
    print(f"  skipped       : {stats['skipped']}")
    print(f"  elapsed       : {elapsed:.1f}s")
    print("──────────────────────────────────────────────────\n")

    if stats["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_main())
