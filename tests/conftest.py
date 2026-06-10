"""
Shared pytest fixtures for North Star tests.

Unit test fixtures (no DB/Redis required):
    mock_conn           — asyncpg connection mock
    mock_pool           — asyncpg pool mock
    mock_embedding_provider — embedding provider that returns predictable vectors
    mock_anthropic_client   — Anthropic client that returns scripted tool_use responses

Integration test fixtures (require docker compose up):
    pg_pool             — real asyncpg pool against test DB
    redis_client        — real Redis client
    test_app            — FastAPI TestClient with real DB/Redis
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio


# ── Unit test fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mock_conn():
    """
    Mock asyncpg Connection.

    Supports: fetchrow, fetchval, fetch, execute.
    Override return values per-test via mock_conn.fetchrow.return_value = ...
    """
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.fetch.return_value = []
    conn.execute.return_value = "UPDATE 1"
    return conn


@pytest.fixture
def mock_pool(mock_conn):
    """Mock asyncpg Pool that yields mock_conn on acquire()."""
    pool = MagicMock()
    # pool.acquire() returns an async context manager yielding mock_conn
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acquire_cm
    return pool


@pytest.fixture
def patch_db(mock_pool):
    """
    Patch src.db.client._pool with mock_pool for the duration of a test.
    Import this fixture in any test that touches get_conn() or transaction().
    """
    with patch("src.db.client._pool", mock_pool):
        yield mock_pool


@pytest.fixture
def mock_embedding_provider():
    """
    Embedding provider that returns deterministic unit vectors.

    embed(text)       → [0.1] * 1536
    embed_batch(texts) → [[0.1]*1536, ...]
    """
    provider = AsyncMock()
    provider.embed.return_value = [0.1] * 1536
    provider.embed_batch.side_effect = lambda texts: [[0.1] * 1536 for _ in texts]
    return provider


@pytest.fixture
def patch_embeddings(mock_embedding_provider):
    """Patch get_provider() to return mock_embedding_provider."""
    with patch("src.utils.embeddings.get_provider", return_value=mock_embedding_provider):
        yield mock_embedding_provider


def _make_tool_use_response(tool_name: str, tool_input: dict[str, Any]):
    """Build a minimal Anthropic Message mock with one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input

    response = MagicMock()
    response.content = [block]
    response.stop_reason = "tool_use"
    return response


@pytest.fixture
def mock_anthropic_client():
    """
    Mock AsyncAnthropic client.

    Configure per-test:
        mock_anthropic_client.messages.create.return_value = _make_tool_use_response(...)
    """
    client = AsyncMock()
    client.messages = AsyncMock()
    return client


# ── Integration test fixtures ─────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def pg_pool():
    """
    Real asyncpg connection pool for integration tests.

    Requires: docker compose up -d postgres
    Creates an isolated schema (northstar_test) per session, tears it down after.
    """
    import asyncpg
    from src.config import settings

    # Use a separate test database URL if provided, else append _test
    test_url = settings.database_url.replace("/northstar", "/northstar_test")

    pool = await asyncpg.create_pool(test_url, min_size=1, max_size=5)

    # Run migrations
    async with pool.acquire() as conn:
        await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def clean_db(pg_pool):
    """
    Truncate all tables before each integration test.
    Faster than dropping/recreating schema.
    """
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE TABLE
                embeddings, relationships, decisions, knowledge, entities, reports,
                human_review_queue
            RESTART IDENTITY CASCADE
        """)
    yield pg_pool


@pytest.fixture(scope="session")
def test_settings(pg_pool):
    """Override settings.database_url to point at the test database."""
    from src.config import settings
    original = settings.database_url
    settings.database_url = settings.database_url.replace("/northstar", "/northstar_test")
    yield settings
    settings.database_url = original
