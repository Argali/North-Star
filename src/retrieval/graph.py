"""
Graph traversal for North Star retrieval.

Given a set of seed node UUIDs (from the hybrid scorer), follow the
relationships edges up to a configurable depth and return the enriched
context package.

Design choices:
- Depth-limited BFS (default depth=2) to avoid runaway traversal.
- Edge filter: only "supports", "informs", and "relates_to" are followed
  for context enrichment. "contradicts" edges are included in the output
  as metadata (so the caller can surface conflicts) but are NOT followed
  for further traversal.
- Visited set prevents cycles and revisiting nodes.
- Each unique node type is fetched in a single batch query at the end
  (not one query per node) to minimise round-trips.
- Returns a GraphContext with knowledge, decisions, entities, and
  contradiction_pairs enriched from the traversal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from src.db.client import get_conn

logger = logging.getLogger(__name__)

# Edge types to FOLLOW during traversal (context enrichment)
_FOLLOW_TYPES = {"supports", "informs", "relates_to"}

# Edge types to INCLUDE in output but NOT follow
_SURFACE_TYPES = {"contradicts"}


# ── Output dataclasses ─────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    id: UUID
    node_type: str    # "knowledge" | "report" | "decision" | "entity"
    label: str        # statement / title / name
    depth: int        # 0 = seed, 1 = first hop, 2 = second hop
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContradictionPair:
    """A pair of nodes connected by a 'contradicts' relationship."""
    from_id: UUID
    to_id: UUID
    from_label: str
    to_label: str


@dataclass
class GraphContext:
    """
    Enriched context package returned by the graph traverser.

    knowledge, decisions, entities are deduplicated across all depths.
    reports are seed nodes only (not traversed further to avoid pulling
    in all knowledge via supports edges at scale).
    """
    knowledge:           list[GraphNode] = field(default_factory=list)
    reports:             list[GraphNode] = field(default_factory=list)
    decisions:           list[GraphNode] = field(default_factory=list)
    entities:            list[GraphNode] = field(default_factory=list)
    contradiction_pairs: list[ContradictionPair] = field(default_factory=list)
    total_nodes:         int = 0
    max_depth_reached:   int = 0


# ── Public entry point ─────────────────────────────────────────────────────────

async def traverse(
    seed_ids: list[UUID],
    max_depth: int = 2,
    max_nodes: int = 50,
) -> GraphContext:
    """
    BFS traversal from seed_ids across the relationships graph.

    Args:
        seed_ids:  UUIDs of the seed nodes (from hybrid scorer).
        max_depth: Maximum edge hops from any seed (default 2).
        max_nodes: Hard cap on total nodes collected (prevents runaway).

    Returns:
        GraphContext with deduplicated nodes and contradiction pairs.
    """
    if not seed_ids:
        return GraphContext()

    visited: set[UUID] = set()
    # frontier: list of (id, depth)
    frontier: list[tuple[UUID, int]] = [(uid, 0) for uid in seed_ids]
    collected: dict[UUID, int] = {}   # id -> depth at which first seen

    contradiction_edges: list[tuple[UUID, UUID]] = []

    # ── BFS ──────────────────────────────────────────────────────────────────
    while frontier and len(collected) < max_nodes:
        current_id, depth = frontier.pop(0)

        if current_id in visited:
            continue
        visited.add(current_id)
        collected[current_id] = depth

        if depth >= max_depth:
            continue

        # Fetch outgoing and incoming edges for this node
        async with get_conn() as conn:
            edge_rows = await conn.fetch(
                """
                SELECT from_id, to_id, type
                FROM relationships
                WHERE from_id = $1 OR to_id = $1
                """,
                current_id,
            )

        for row in edge_rows:
            edge_type = row["type"]
            other_id  = row["to_id"] if row["from_id"] == current_id else row["from_id"]

            if edge_type in _SURFACE_TYPES:
                contradiction_edges.append((row["from_id"], row["to_id"]))
                # Don't follow, but ensure both sides are in visited scope
                if other_id not in visited and len(collected) < max_nodes:
                    collected[other_id] = depth + 1
                continue

            if edge_type in _FOLLOW_TYPES and other_id not in visited:
                frontier.append((other_id, depth + 1))

    max_depth_reached = max(collected.values(), default=0)

    # ── Batch fetch all collected node rows ───────────────────────────────────
    all_ids = list(collected.keys())

    knowledge_nodes  = await _fetch_knowledge(all_ids, collected)
    report_nodes     = await _fetch_reports(all_ids, collected)
    decision_nodes   = await _fetch_decisions(all_ids, collected)
    entity_nodes     = await _fetch_entities(all_ids, collected)

    # Build a label index for contradiction pair labelling
    label_index: dict[UUID, str] = {}
    for n in knowledge_nodes + report_nodes + decision_nodes + entity_nodes:
        label_index[n.id] = n.label

    # Deduplicate contradiction edges and look up labels
    seen_pairs: set[frozenset] = set()
    contradiction_pairs: list[ContradictionPair] = []
    for from_id, to_id in contradiction_edges:
        key = frozenset([from_id, to_id])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        contradiction_pairs.append(ContradictionPair(
            from_id=from_id,
            to_id=to_id,
            from_label=label_index.get(from_id, str(from_id)),
            to_label=label_index.get(to_id,   str(to_id)),
        ))

    return GraphContext(
        knowledge=knowledge_nodes,
        reports=report_nodes,
        decisions=decision_nodes,
        entities=entity_nodes,
        contradiction_pairs=contradiction_pairs,
        total_nodes=len(all_ids),
        max_depth_reached=max_depth_reached,
    )


# ── Batch node fetchers ────────────────────────────────────────────────────────

async def _fetch_knowledge(
    all_ids: list[UUID],
    depth_map: dict[UUID, int],
) -> list[GraphNode]:
    if not all_ids:
        return []
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, statement, confidence, topics, source_section
            FROM knowledge
            WHERE id = ANY($1) AND status = 'validated'
            """,
            all_ids,
        )
    return [
        GraphNode(
            id=row["id"],
            node_type="knowledge",
            label=row["statement"],
            depth=depth_map.get(row["id"], 0),
            raw={
                "confidence":     float(row["confidence"] or 0.5),
                "topics":         list(row["topics"] or []),
                "source_section": row["source_section"],
            },
        )
        for row in rows
    ]


async def _fetch_reports(
    all_ids: list[UUID],
    depth_map: dict[UUID, int],
) -> list[GraphNode]:
    if not all_ids:
        return []
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, title, tags FROM reports WHERE id = ANY($1)",
            all_ids,
        )
    return [
        GraphNode(
            id=row["id"],
            node_type="report",
            label=row["title"],
            depth=depth_map.get(row["id"], 0),
            raw={"tags": list(row["tags"] or [])},
        )
        for row in rows
    ]


async def _fetch_decisions(
    all_ids: list[UUID],
    depth_map: dict[UUID, int],
) -> list[GraphNode]:
    if not all_ids:
        return []
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, statement, status, owner FROM decisions WHERE id = ANY($1)",
            all_ids,
        )
    return [
        GraphNode(
            id=row["id"],
            node_type="decision",
            label=row["statement"],
            depth=depth_map.get(row["id"], 0),
            raw={"status": row["status"], "owner": row["owner"]},
        )
        for row in rows
    ]


async def _fetch_entities(
    all_ids: list[UUID],
    depth_map: dict[UUID, int],
) -> list[GraphNode]:
    if not all_ids:
        return []
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, name, type FROM entities WHERE id = ANY($1)",
            all_ids,
        )
    return [
        GraphNode(
            id=row["id"],
            node_type="entity",
            label=row["name"],
            depth=depth_map.get(row["id"], 0),
            raw={"type": row["type"]},
        )
        for row in rows
    ]
