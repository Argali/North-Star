"""
North Star CLI — Phase 6 Lite
==============================

Commands
--------
    northstar review   — list and resolve human review items
    northstar scan     — trigger a staleness scan
    northstar stats    — show knowledge base statistics

Usage
-----
    python -m src.cli review
    python -m src.cli review --resolve 0 approve --note "Verified manually"
    python -m src.cli scan
    python -m src.cli scan --dry-run --days 60
    python -m src.cli stats

Environment
-----------
    NORTHSTAR_URL  — base URL of the running API (default http://localhost:8000)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# HTTP helpers (zero extra deps — stdlib only)
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("NORTHSTAR_URL", "http://localhost:8000").rstrip("/")


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    params: dict | None = None,
) -> Any:
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print(f"[error] HTTP {exc.code}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"[error] Could not connect to {BASE_URL}: {exc.reason}", file=sys.stderr)
        print("  Is the server running? Set NORTHSTAR_URL if needed.", file=sys.stderr)
        sys.exit(1)


def _get(path: str, params: dict | None = None) -> Any:
    return _request("GET", path, params=params)


def _post(path: str, body: dict | None = None, params: dict | None = None) -> Any:
    return _request("POST", path, body=body, params=params)


def _fmt(obj: Any, indent: int = 2) -> str:
    return json.dumps(obj, indent=indent, default=str)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

def cmd_review(args: argparse.Namespace) -> None:
    """List or resolve human review items."""

    if args.resolve is not None:
        # Resolve a specific item
        index = args.resolve
        action = args.action
        note = args.note

        body: dict[str, Any] = {"action": action}
        if note:
            body["note"] = note

        result = _post(f"/human-review/{index}/resolve", body=body)
        print(f"[{result.get('action', action).upper()}] index={index}")
        if result.get("stored_ids"):
            print(f"  stored knowledge: {result['stored_ids']}")
        if result.get("note"):
            print(f"  note: {result['note']}")
        print(f"  status: {result.get('status', '?')}")
        return

    # List mode
    params: dict[str, Any] = {
        "limit": args.limit,
        "offset": args.offset,
    }
    if args.reason:
        params["reason"] = args.reason

    result = _get("/human-review", params=params)
    items: list[dict] = result.get("items", [])
    total: int = result.get("total", 0)

    if not items:
        print("No items in the review queue.")
        return

    print(f"Human review queue — {total} total (showing {len(items)})\n")
    for item in items:
        idx = item.get("_queue_index", "?")
        reason = item.get("reason", "unknown")
        source = item.get("source", "?")
        context = item.get("context", {})
        statement = (
            context.get("statement")
            or context.get("new_statement")
            or context.get("message")
            or ""
        )
        queued_at = item.get("queued_at", "")
        print(f"  [{idx}] {reason}  ({source})  {queued_at}")
        if statement:
            print(f"       {statement[:120]}")
    print()
    print("  To resolve:  northstar review --resolve <index> <approve|reject|skip>")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace) -> None:
    """Trigger a staleness scan."""

    params: dict[str, Any] = {"dry_run": str(args.dry_run).lower()}
    if args.days is not None:
        params["staleness_days"] = args.days

    result = _post("/scan", params=params)

    dry = result.get("dry_run", False)
    threshold = result.get("threshold_days", "?")
    n_k = result.get("stale_knowledge", 0)
    n_d = result.get("stale_decisions", 0)

    label = "[DRY RUN] " if dry else ""
    print(f"{label}Staleness scan — threshold {threshold} days\n")
    print(f"  stale knowledge : {n_k}")
    print(f"  stale decisions : {n_d}")

    if not dry and (n_k + n_d) > 0:
        print(f"\n  {n_k + n_d} items pushed to human review queue.")
        print("  Run 'northstar review' to triage them.")

    if args.verbose:
        items = result.get("items", {})
        if items.get("knowledge"):
            print("\nStale knowledge:")
            for k in items["knowledge"]:
                print(f"  {k['id']}  {k['statement'][:80]}  (from {k['valid_from']})")
        if items.get("decisions"):
            print("\nStale decisions:")
            for d in items["decisions"]:
                print(f"  {d['id']}  {d['statement'][:80]}  (from {d['created_at']})")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def cmd_stats(args: argparse.Namespace) -> None:
    """Show knowledge base statistics."""

    health = _get("/health")
    ready = _get("/readyz")

    print("North Star — knowledge base stats\n")
    print(f"  api status   : {health.get('status', '?')}")
    print(f"  ready        : {ready.get('ready', '?')}")

    db_ok = health.get("db") == "ok"
    redis_ok = health.get("redis") == "ok"
    print(f"  postgres     : {'✓' if db_ok else '✗'}")
    print(f"  redis        : {'✓' if redis_ok else '✗'}")

    # Pull a retrieve sample to show counts
    if db_ok:
        # Quick stats via a retrieval probe (shows meta.total_candidates)
        try:
            probe = _post(
                "/retrieve",
                body={"query": "status", "intent": "knowledge", "limit": 1},
            )
            meta = probe.get("meta", {})
            print(f"  total candidates (knowledge): {meta.get('total_candidates', 'n/a')}")
            print(f"  embedding used   : {meta.get('embedding_used', 'n/a')}")
        except SystemExit:
            pass

    if args.verbose:
        print("\nFull health response:")
        print(_fmt(health))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="northstar",
        description="North Star CLI — manage your AI memory store.",
    )
    p.add_argument(
        "--url",
        default=None,
        help="API base URL (overrides NORTHSTAR_URL env var).",
    )

    sub = p.add_subparsers(dest="command", required=True)

    # -- review --
    rv = sub.add_parser("review", help="List or resolve human review items.")
    rv.add_argument(
        "--resolve",
        type=int,
        default=None,
        metavar="INDEX",
        help="Resolve item at this queue index.",
    )
    rv.add_argument(
        "action",
        nargs="?",
        choices=["approve", "reject", "skip"],
        default=None,
        help="Resolution action (required with --resolve).",
    )
    rv.add_argument(
        "--note",
        default=None,
        help="Optional resolution note.",
    )
    rv.add_argument("--reason", default=None, help="Filter by reason string.")
    rv.add_argument("--limit", type=int, default=20, help="Max items to show (default 20).")
    rv.add_argument("--offset", type=int, default=0)

    # -- scan --
    sc = sub.add_parser("scan", help="Trigger a staleness scan.")
    sc.add_argument("--days", type=int, default=None, help="Staleness threshold in days.")
    sc.add_argument("--dry-run", action="store_true", default=False)
    sc.add_argument("--verbose", "-v", action="store_true", default=False)

    # -- stats --
    st = sub.add_parser("stats", help="Show knowledge base statistics.")
    st.add_argument("--verbose", "-v", action="store_true", default=False)

    return p


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Allow --url flag to override env
    if args.url:
        global BASE_URL
        BASE_URL = args.url.rstrip("/")

    if args.command == "review":
        if args.resolve is not None and args.action is None:
            parser.error("--resolve requires an action: approve, reject, or skip")
        cmd_review(args)

    elif args.command == "scan":
        cmd_scan(args)

    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
