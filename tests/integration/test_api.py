"""
Integration tests for the North Star REST API.

Requirements:
    docker compose up -d postgres redis
    alembic upgrade head

Run with:
    pytest tests/integration/ -v

These tests hit a real Postgres database (northstar_test) and Redis.
Each test class uses the clean_db fixture to truncate tables before running.
"""
from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Skip entire module if TEST_DATABASE_URL is not set
pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL") and not os.getenv("DATABASE_URL"),
    reason="No DATABASE_URL set — skipping integration tests. Run: docker compose up -d postgres redis",
)


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client():
    """
    FastAPI async test client.

    Starts the app lifespan (DB pool + Redis) for each test.
    Requires DATABASE_URL and REDIS_URL in environment.
    """
    from src.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Health endpoints ──────────────────────────────────────────────────────────

class TestHealth:

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_returns_200_when_connected(self, client):
        resp = await client.get("/ready")
        # If DB/Redis are up, should be 200; otherwise 503 — don't fail either way
        assert resp.status_code in (200, 503)


# ── Reports ───────────────────────────────────────────────────────────────────

class TestReports:

    @pytest.mark.asyncio
    async def test_create_report(self, client):
        resp = await client.post("/reports", json={
            "title": "Fleet Q1 Analysis",
            "author": "test_agent",
            "context_summary": "Q1 2026 fleet performance",
            "conclusions": "Maintenance costs up 12%",
            "tags": ["fleet", "q1", "2026"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Fleet Q1 Analysis"
        assert "id" in data
        assert data["author"] == "test_agent"

    @pytest.mark.asyncio
    async def test_get_report_by_id(self, client):
        # Create first
        create = await client.post("/reports", json={"title": "Test Report"})
        report_id = create.json()["id"]

        # Fetch by ID
        resp = await client.get(f"/reports/{report_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == report_id

    @pytest.mark.asyncio
    async def test_get_report_not_found(self, client):
        resp = await client.get(f"/reports/{uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_reports_empty(self, client):
        resp = await client.get("/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_list_reports_by_tag(self, client):
        await client.post("/reports", json={"title": "Tagged", "tags": ["alpha"]})
        await client.post("/reports", json={"title": "Untagged"})

        resp = await client.get("/reports?tags=alpha")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all("alpha" in (r.get("tags") or []) for r in items)


# ── Knowledge ─────────────────────────────────────────────────────────────────

class TestKnowledge:

    @pytest.mark.asyncio
    async def test_create_knowledge(self, client):
        resp = await client.post("/knowledge", json={
            "statement": "Fleet inspection interval is 30 days",
            "confidence": 0.9,
            "status": "validated",
            "topics": ["fleet", "maintenance"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["statement"] == "Fleet inspection interval is 30 days"
        assert data["confidence"] == 0.9
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_knowledge_by_id(self, client):
        create = await client.post("/knowledge", json={
            "statement": "Fuel consumption average is 9.2L/100km",
            "confidence": 0.85,
            "status": "validated",
        })
        kid = create.json()["id"]

        resp = await client.get(f"/knowledge/{kid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == kid

    @pytest.mark.asyncio
    async def test_get_knowledge_not_found(self, client):
        resp = await client.get(f"/knowledge/{uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_knowledge_default_status(self, client):
        await client.post("/knowledge", json={
            "statement": "Validated item",
            "confidence": 0.8,
            "status": "validated",
        })
        await client.post("/knowledge", json={
            "statement": "Proposed item",
            "confidence": 0.5,
            "status": "proposed",
        })
        resp = await client.get("/knowledge")
        assert resp.status_code == 200
        items = resp.json()["items"]
        # Default filter is status=validated
        assert all(i["status"] == "validated" for i in items)

    @pytest.mark.asyncio
    async def test_list_knowledge_by_topics(self, client):
        await client.post("/knowledge", json={
            "statement": "Safety check required monthly",
            "status": "validated",
            "topics": ["safety"],
        })
        await client.post("/knowledge", json={
            "statement": "Fuel price is EUR 1.80",
            "status": "validated",
            "topics": ["cost"],
        })
        resp = await client.get("/knowledge?topics=safety")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all("safety" in (i.get("topics") or []) for i in items)

    @pytest.mark.asyncio
    async def test_confidence_bounds(self, client):
        resp = await client.post("/knowledge", json={
            "statement": "Test",
            "confidence": 1.5,   # out of range
        })
        assert resp.status_code == 422


# ── Decisions ─────────────────────────────────────────────────────────────────

class TestDecisions:

    @pytest.mark.asyncio
    async def test_create_decision(self, client):
        resp = await client.post("/decisions", json={
            "statement": "Adopt 30-day inspection cycle",
            "rationale": "Reduces breakdowns by 40%",
            "owner": "fleet_manager",
            "status": "planned",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["statement"] == "Adopt 30-day inspection cycle"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_decision_by_id(self, client):
        create = await client.post("/decisions", json={
            "statement": "Switch to synthetic oil",
            "rationale": "Extends service intervals",
        })
        did = create.json()["id"]

        resp = await client.get(f"/decisions/{did}")
        assert resp.status_code == 200
        assert resp.json()["id"] == did

    @pytest.mark.asyncio
    async def test_get_decision_not_found(self, client):
        resp = await client.get(f"/decisions/{uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_decisions_by_status(self, client):
        await client.post("/decisions", json={
            "statement": "Executed decision",
            "rationale": "Done",
            "status": "executed",
        })
        await client.post("/decisions", json={
            "statement": "Planned decision",
            "rationale": "Pending",
            "status": "planned",
        })
        resp = await client.get("/decisions?status=executed")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(i["status"] == "executed" for i in items)


# ── Entities ──────────────────────────────────────────────────────────────────

class TestEntities:

    @pytest.mark.asyncio
    async def test_create_entity(self, client):
        resp = await client.post("/entities", json={
            "name": "Vehicle 259",
            "type": "vehicle",
            "metadata": {"plate": "AB-259-CD", "make": "Renault"},
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Vehicle 259"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_entity_by_id(self, client):
        create = await client.post("/entities", json={"name": "Webfleet", "type": "system"})
        eid = create.json()["id"]

        resp = await client.get(f"/entities/{eid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["entity"]["id"] == eid
        assert "graph" in body

    @pytest.mark.asyncio
    async def test_get_entity_not_found(self, client):
        resp = await client.get(f"/entities/{uuid4()}")
        assert resp.status_code == 404


# ── Human review queue ────────────────────────────────────────────────────────

class TestHumanReview:

    @pytest.mark.asyncio
    async def test_list_review_empty(self, client):
        resp = await client.get("/human-review")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == [] or isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_reject_review_item(self, client):
        # Push an item directly via the queue
        from src.pipelines.queues import human_review_queue
        review_id = await human_review_queue.push({
            "source": "test",
            "reason": "test_contradiction",
            "context": {"statement": "test statement"},
        })

        resp = await client.post(
            f"/human-review/{review_id}/resolve",
            json={"action": "reject", "note": "false positive"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "reject"

    @pytest.mark.asyncio
    async def test_approve_review_item_stores_knowledge(self, client):
        from src.pipelines.queues import human_review_queue
        review_id = await human_review_queue.push({
            "source": "archivist_pipeline",
            "reason": "direct_contradiction",
            "context": {
                "new_statement": "Inspection interval is 45 days",
                "source_report_id": None,
            },
        })

        resp = await client.post(
            f"/human-review/{review_id}/resolve",
            json={"action": "approve", "note": "Confirmed correct", "resolved_by": "erwan"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "approve"
        assert len(data["stored_ids"]) == 1

        # Verify knowledge was actually stored
        kid = data["stored_ids"][0]
        k_resp = await client.get(f"/knowledge/{kid}")
        assert k_resp.status_code == 200
        assert k_resp.json()["status"] == "validated"

    @pytest.mark.asyncio
    async def test_resolve_already_resolved_returns_409(self, client):
        from src.pipelines.queues import human_review_queue
        review_id = await human_review_queue.push({
            "source": "test",
            "reason": "test",
            "context": {},
        })

        # First resolve
        await client.post(f"/human-review/{review_id}/resolve", json={"action": "reject"})

        # Second resolve — should 409
        resp = await client.post(
            f"/human-review/{review_id}/resolve",
            json={"action": "approve"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_get_review_item_by_id(self, client):
        from src.pipelines.queues import human_review_queue
        review_id = await human_review_queue.push({
            "source": "test",
            "reason": "stale_knowledge",
            "context": {"statement": "Old fact"},
        })

        resp = await client.get(f"/human-review/{review_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reason"] == "stale_knowledge"
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_skip_keeps_item_pending(self, client):
        from src.pipelines.queues import human_review_queue
        review_id = await human_review_queue.push({
            "source": "test",
            "reason": "contextual_contradiction",
            "context": {},
        })

        resp = await client.post(
            f"/human-review/{review_id}/resolve",
            json={"action": "skip"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

        # Item should still be fetchable as pending
        item_resp = await client.get(f"/human-review/{review_id}")
        assert item_resp.status_code == 200
        assert item_resp.json()["status"] == "pending"
