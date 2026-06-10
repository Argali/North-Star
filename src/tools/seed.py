"""
seed.py — Knowledge Seed Pack loader for North Star.

Reads JSON seed packs from seeds/ and inserts items into the knowledge table.
Skips items whose statement already exists (idempotent).
After loading, triggers embedding backfill for un-embedded rows.

Usage (CLI):
    python -m src.tools.seed --pack architecture
    python -m src.tools.seed --all
    python -m src.tools.seed --list
    python -m src.tools.seed --pack architecture --dry-run

Requires the same environment variables as the main application:
    DATABASE_URL, REDIS_URL, EMBEDDING_MODEL, EMBEDDING_DIM
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── locate the repo root (two levels up from this file) ──────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SEEDS_DIR = REPO_ROOT / "seeds"
MANIFEST_PATH = SEEDS_DIR / "manifest.json"

# ── source tag written into every seeded knowledge row ───────────────────────
SEED_SOURCE_PREFIX = "seed:"


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        print(f"[seed] ERROR: manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _load_pack_file(pack_name: str, manifest: dict) -> tuple[str, list[dict]]:
    """Return (pack_name, items_list) or exit on error."""
    packs_by_name = {p["name"]: p for p in manifest.get("packs", [])}
    if pack_name not in packs_by_name:
        available = ", ".join(packs_by_name.keys())
        print(
            f"[seed] ERROR: unknown pack '{pack_name}'. Available: {available}",
            file=sys.stderr,
        )
        sys.exit(1)

    rel_path = packs_by_name[pack_name]["file"]
    abs_path = SEEDS_DIR / rel_path
    if not abs_path.exists():
        print(f"[seed] ERROR: pack file not found: {abs_path}", file=sys.stderr)
        sys.exit(1)

    with open(abs_path) as f:
        data = json.load(f)

    items = data.get("knowledge", [])
    return pack_name, items


def _validate_item(item: dict, index: int, pack_name: str) -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errors = []
    if not isinstance(item.get("statement"), str) or not item["statement"].strip():
        errors.append(f"item[{index}]: missing or empty 'statement'")
    if not isinstance(item.get("topics"), list) or not item["topics"]:
        errors.append(f"item[{index}]: 'topics' must be a non-empty list")
    confidence = item.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        errors.append(f"item[{index}]: 'confidence' must be a float 0.0–1.0")
    return errors


async def _run_seed(
    pack_names: list[str],
    dry_run: bool,
    verbose: bool,
    batch_size: int = 50,
) -> dict[str, Any]:
    """Core async loader. Returns a summary dict."""

    # Late import so the module can be listed/validated without DB available.
    import asyncpg  # type: ignore

    manifest = _load_manifest()

    # Gather all items to load
    all_items: list[tuple[str, dict]] = []
    for pack_name in pack_names:
        _, items = _load_pack_file(pack_name, manifest)
        all_items.extend((pack_name, item) for item in items)

    if verbose:
        print(f"[seed] loaded {len(all_items)} items across {len(pack_names)} pack(s)")

    if dry_run:
        # Validate and report only
        errors: list[str] = []
        for pack_name, item in all_items:
            idx = all_items.index((pack_name, item))
            errors.extend(_validate_item(item, idx, pack_name))
        if errors:
            for e in errors:
                print(f"[seed] VALIDATION ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[seed] dry-run: {len(all_items)} items validated OK (nothing written)")
        return {"inserted": 0, "skipped": 0, "errors": 0, "dry_run": True}

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("[seed] ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(database_url)
    try:
        inserted = 0
        skipped = 0
        errors_count = 0

        # Process in batches
        for batch_start in range(0, len(all_items), batch_size):
            batch = all_items[batch_start : batch_start + batch_size]

            for pack_name, item in batch:
                statement = item["statement"].strip()
                topics = item.get("topics", [])
                confidence = float(item.get("confidence", 0.9))
                tags = item.get("tags", [])
                source_tag = f"{SEED_SOURCE_PREFIX}{pack_name}"

                # Validate
                item_idx = all_items.index((pack_name, item))
                item_errors = _validate_item(item, item_idx, pack_name)
                if item_errors:
                    for e in item_errors:
                        print(f"[seed] SKIP (invalid): {e}", file=sys.stderr)
                    errors_count += 1
                    continue

                # Idempotency check: skip if statement already stored
                existing = await conn.fetchval(
                    "SELECT id FROM knowledge WHERE statement = $1 LIMIT 1",
                    statement,
                )
                if existing:
                    if verbose:
                        print(f"[seed] skip (exists): {statement[:70]}…")
                    skipped += 1
                    continue

                # Insert
                try:
                    await conn.execute(
                        """
                        INSERT INTO knowledge (
                            statement, topics, confidence, status,
                            tags, source_report_ids
                        ) VALUES (
                            $1, $2, $3, 'validated', $4, ARRAY[]::text[]
                        )
                        """,
                        statement,
                        topics,
                        confidence,
                        tags,
                    )
                    inserted += 1
                    if verbose:
                        print(f"[seed] inserted [{pack_name}]: {statement[:70]}…")
                except Exception as exc:
                    print(f"[seed] ERROR inserting item: {exc}", file=sys.stderr)
                    errors_count += 1

    finally:
        await conn.close()

    summary = {
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors_count,
        "dry_run": False,
    }
    return summary


def _list_packs(manifest: dict) -> None:
    print(f"Seed manifest v{manifest.get('version', '?')}")
    print(f"Description: {manifest.get('description', '')}")
    print()
    print(f"{'Name':<20}  {'File':<50}  Items")
    print("-" * 80)
    for pack in manifest.get("packs", []):
        abs_path = SEEDS_DIR / pack["file"]
        count = "?"
        if abs_path.exists():
            try:
                with open(abs_path) as f:
                    data = json.load(f)
                count = str(len(data.get("knowledge", [])))
            except Exception:
                count = "ERR"
        print(f"{pack['name']:<20}  {pack['file']:<50}  {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.seed",
        description="Load knowledge seed packs into North Star",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pack",
        metavar="NAME",
        help="Load a single named seed pack (e.g. architecture)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Load all seed packs listed in the manifest",
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="List available packs and their item counts (no DB required)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate items without writing to the database",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-item progress",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        metavar="N",
        help="Number of items to process per batch (default: 50)",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        default=True,
        help="Run embedding backfill after loading (default: true)",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_false",
        dest="backfill",
        help="Skip embedding backfill after loading",
    )

    args = parser.parse_args()
    manifest = _load_manifest()

    if args.list:
        _list_packs(manifest)
        return

    if args.pack:
        pack_names = [args.pack]
    else:  # --all
        pack_names = [p["name"] for p in manifest.get("packs", [])]

    t0 = time.time()
    summary = asyncio.run(
        _run_seed(
            pack_names=pack_names,
            dry_run=args.dry_run,
            verbose=args.verbose,
            batch_size=args.batch_size,
        )
    )
    elapsed = time.time() - t0

    if not summary["dry_run"]:
        print(
            f"[seed] done in {elapsed:.1f}s  "
            f"inserted={summary['inserted']}  "
            f"skipped={summary['skipped']}  "
            f"errors={summary['errors']}"
        )

        if args.backfill and summary["inserted"] > 0:
            print("[seed] running embedding backfill for new knowledge rows…")
            from src.tools.backfill import run_backfill  # type: ignore

            result = asyncio.run(
                run_backfill(object_type="knowledge", batch_size=100, dry_run=False)
            )
            print(
                f"[seed] backfill done  "
                f"processed={result['processed']}  "
                f"failed={result['failed']}"
            )

        if summary["errors"] > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
