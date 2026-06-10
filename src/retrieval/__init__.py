"""
North Star hybrid retrieval module.

Public interface:
    from src.retrieval import retrieve_context

See docs/RETRIEVAL.md for the full specification.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from .graph import ContradictionPair, GraphContext, GraphNode, traverse
from .scorer import RetrievalResult, ScoredItem, hybrid_score


async def retrieve_context(
    query: str,
    intent: str = "knowledge",
    topics: list[str] | None = None,
    confidence_floor: float | None = None,
    limit: int = 10,
    graph_depth: int = 2,
    alpha: float | None = None,
    beta: float | None = None,
    gamma: float | None = None,
) -> dict[str, Any]:
    """
    Full retrieval pipeline: hybrid scoring + graph traversal.

    Returns a context dict ready for the /retrieve API response.
    """
    result = await hybrid_score(
        query=query,
        intent=intent,
        topics=topics,
        confidence_floor=confidence_floor,
        limit=limit,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
    )

    seed_ids = [item.id for item in result.items]
    graph = await traverse(seed_ids=seed_ids, max_depth=graph_depth)

    return _assemble_context(query=query, result=result, graph=graph)


def _assemble_context(
    query: str,
    result: RetrievalResult,
    graph: GraphContext,
) -> dict[str, Any]:
    """Merge scorer results and graph context into a single response dict."""

    def _node_to_dict(n: GraphNode) -> dict[str, Any]:
        return {
            "id": str(n.id),
            "type": n.node_type,
            "label": n.label,
            "depth": n.depth,
            **n.raw,
        }

    def _item_to_dict(item: ScoredItem) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "type": item.object_type,
            "statement": item.statement,
            "confidence": item.confidence,
            "topics": item.topics,
            "scores": {
                "semantic": round(item.semantic_score, 4),
                "keyword":  round(item.keyword_score, 4),
                "recency":  round(item.recency_score, 4),
                "final":    round(item.final_score, 4),
            },
            **item.raw,
        }

    return {
        "query": query,
        "intent": result.intent,
        "ranked": [_item_to_dict(i) for i in result.items],
        "graph": {
            "knowledge":           [_node_to_dict(n) for n in graph.knowledge],
            "reports":             [_node_to_dict(n) for n in graph.reports],
            "decisions":           [_node_to_dict(n) for n in graph.decisions],
            "entities":            [_node_to_dict(n) for n in graph.entities],
            "contradiction_pairs": [
                {
                    "from_id":    str(p.from_id),
                    "to_id":      str(p.to_id),
                    "from_label": p.from_label,
                    "to_label":   p.to_label,
                }
                for p in graph.contradiction_pairs
            ],
            "total_nodes":       graph.total_nodes,
            "max_depth_reached": graph.max_depth_reached,
        },
        "meta": {
            "alpha":            result.alpha,
            "beta":             result.beta,
            "gamma":            result.gamma,
            "confidence_floor": result.confidence_floor,
            "embedding_used":   result.query_embedding_used,
            "total_candidates": result.total_candidates,
        },
    }


__all__ = [
    "retrieve_context",
    "hybrid_score",
    "traverse",
    "RetrievalResult",
    "ScoredItem",
    "GraphContext",
    "GraphNode",
    "ContradictionPair",
]
