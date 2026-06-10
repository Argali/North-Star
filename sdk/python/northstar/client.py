"""
North Star Python SDK client.

Provides async-first access to the North Star REST API.
A sync wrapper (NorthStarClient.sync) is available for non-async contexts.

Usage (async):

    from northstar import NorthStarClient

    async def main():
        ns = NorthStarClient(base_url="http://localhost:8000")
        await ns.ingest({"source_type": "document", "payload": {"text": "..."}})
        results = await ns.retrieve("What do we know about fleet costs?")

Usage (sync):

    from northstar import NorthStarClient

    ns = NorthStarClient.sync(base_url="http://localhost:8000")
    results = ns.retrieve("What do we know about fleet costs?")

The client can also import the pipeline functions directly when used
inside a North Star deployment (no HTTP overhead):

    ns = NorthStarClient(base_url=None)   # embedded mode
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_TIMEOUT  = 30.0


class NorthStarClient:
    """
    Async HTTP client for the North Star REST API.

    All methods are coroutines. Use NorthStarClient.sync() for a blocking
    wrapper that calls asyncio.run() internally.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Args:
            base_url:    Base URL of the North Star API (no trailing slash).
            api_key:     Optional Bearer token (for future auth support).
            timeout:     Request timeout in seconds.
            http_client: Inject a custom httpx.AsyncClient (useful for testing).
        """
        self._base_url = base_url.rstrip("/")
        self._api_key  = api_key
        self._timeout  = timeout
        self._client   = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers=self._default_headers(),
        )

    def _default_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # ── Context manager support ───────────────────────────────────────────────

    async def __aenter__(self) -> NorthStarClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ── Pipeline ──────────────────────────────────────────────────────────────

    async def ingest(
        self,
        source_type: str,
        payload: dict[str, Any],
        author: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Submit raw activity to the Scribe pipeline.

        The pipeline runs asynchronously (202 Accepted). Use the returned
        queue_id or poll GET /reports to track completion.

        Args:
            source_type: "conversation" | "task" | "document"
            payload:     Source-specific payload.
                         conversation: {"messages": [{"role": ..., "content": ...}]}
                         task:         {"log": "...", "result": "..."}
                         document:     {"text": "..."}
            author:      Optional author name or agent ID.
            tags:        Optional extra tags to attach to the generated report.

        Returns:
            API response dict with status and queue info.
        """
        body: dict[str, Any] = {"source_type": source_type, "payload": payload}
        if author:
            body["author"] = author
        if tags:
            body["tags"] = tags

        return await self._post("/scribe/process", body)

    async def retrieve(
        self,
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
        Hybrid retrieval: semantic + keyword + recency, then graph traversal.

        Args:
            query:            Natural language question.
            intent:           "knowledge" | "report" | "decision" | "entity"
            topics:           Filter to items tagged with any of these topics.
            confidence_floor: Minimum confidence (0–1). Defaults to server setting.
            limit:            Maximum ranked items to return (max 50).
            graph_depth:      Relationship hops for context enrichment (max 3).
            alpha/beta/gamma: Override ranking weights (must sum to ~1.0).

        Returns:
            {
                "ranked": [...],          # top-k items with scores
                "graph": {                # enriched context
                    "knowledge": [...],
                    "decisions": [...],
                    "entities": [...],
                    "contradiction_pairs": [...]
                },
                "meta": {...}
            }
        """
        params: dict[str, Any] = {"q": query, "intent": intent, "limit": limit, "graph_depth": graph_depth}
        if topics:
            params["topics"] = topics
        if confidence_floor is not None:
            params["confidence_floor"] = confidence_floor
        if alpha is not None:
            params["alpha"] = alpha
        if beta is not None:
            params["beta"] = beta
        if gamma is not None:
            params["gamma"] = gamma

        return await self._get("/retrieve", params=params)

    # ── Resource accessors ────────────────────────────────────────────────────

    async def report(self, report_id: str | UUID) -> dict[str, Any]:
        """Fetch a full report by ID."""
        return await self._get(f"/reports/{report_id}")

    async def list_reports(
        self,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List reports, optionally filtered by tags."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tags:
            params["tags"] = tags
        return await self._get("/reports", params=params)

    async def knowledge(self, knowledge_id: str | UUID) -> dict[str, Any]:
        """Fetch a knowledge item by ID."""
        return await self._get(f"/knowledge/{knowledge_id}")

    async def list_knowledge(
        self,
        status: str = "validated",
        topics: list[str] | None = None,
        confidence_floor: float | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List knowledge items."""
        params: dict[str, Any] = {"status": status, "limit": limit, "offset": offset}
        if topics:
            params["topics"] = topics
        if confidence_floor is not None:
            params["confidence_floor"] = confidence_floor
        return await self._get("/knowledge", params=params)

    async def decision(self, decision_id: str | UUID) -> dict[str, Any]:
        """Fetch a decision by ID."""
        return await self._get(f"/decisions/{decision_id}")

    async def list_decisions(
        self,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List decisions."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return await self._get("/decisions", params=params)

    async def entity(
        self,
        entity_id: str | UUID,
        graph_depth: int = 2,
    ) -> dict[str, Any]:
        """Fetch an entity and its related graph context."""
        return await self._get(f"/entities/{entity_id}", params={"graph_depth": graph_depth})

    async def health(self) -> dict[str, Any]:
        """Liveness check."""
        return await self._get("/health")

    async def ready(self) -> dict[str, Any]:
        """Readiness check (DB + Redis)."""
        return await self._get("/ready")

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._client.get(path, params=params)
        return self._handle(resp)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(path, content=json.dumps(body))
        return self._handle(resp)

    @staticmethod
    def _handle(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise NorthStarAPIError(
                status_code=resp.status_code,
                detail=resp.text,
            )
        return resp.json()

    # ── Sync wrapper ──────────────────────────────────────────────────────────

    @classmethod
    def sync(
        cls,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> _SyncClient:
        """
        Return a synchronous wrapper around NorthStarClient.

        All methods block until complete using asyncio.run().
        Do not use this inside an existing async context — use the async
        client directly instead.
        """
        return _SyncClient(base_url=base_url, api_key=api_key, timeout=timeout)


class NorthStarAPIError(Exception):
    """Raised when the North Star API returns an HTTP error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail      = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class _SyncClient:
    """
    Synchronous wrapper around NorthStarClient.

    Created via NorthStarClient.sync(). Not intended for direct instantiation.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def _async_client(self) -> NorthStarClient:
        return NorthStarClient(**self._kwargs)

    def ingest(self, source_type: str, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.ingest(source_type, payload, **kw)
        return self._run(_inner())

    def retrieve(self, query: str, **kw: Any) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.retrieve(query, **kw)
        return self._run(_inner())

    def report(self, report_id: str | UUID) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.report(report_id)
        return self._run(_inner())

    def knowledge(self, knowledge_id: str | UUID) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.knowledge(knowledge_id)
        return self._run(_inner())

    def decision(self, decision_id: str | UUID) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.decision(decision_id)
        return self._run(_inner())

    def entity(self, entity_id: str | UUID, **kw: Any) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.entity(entity_id, **kw)
        return self._run(_inner())

    def health(self) -> dict[str, Any]:
        async def _inner():
            async with NorthStarClient(**self._kwargs) as c:
                return await c.health()
        return self._run(_inner())
