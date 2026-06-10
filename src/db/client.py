"""
Async Postgres connection pool using asyncpg.

Usage:

    # On app startup:
    await init_pool()

    # In a request handler or service:
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM reports WHERE id = $1", report_id)

    # For write operations that must be atomic:
    async with transaction() as conn:
        await conn.execute("INSERT INTO reports ...")
        await conn.execute("INSERT INTO embeddings ...")

    # On app shutdown:
    await close_pool()
"""
from __future__ import annotations

import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from src.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """Create the global connection pool. Call once at application startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        # Register the pgvector codec so VECTOR columns are returned as lists of float
        init=_register_vector_codec,
    )


async def close_pool() -> None:
    """Drain and close the global connection pool. Call at application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the global pool. Raises RuntimeError if init_pool() was not called."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_pool() first.")
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire a connection from the pool for the duration of the context."""
    async with get_pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    """Acquire a connection and wrap it in an explicit transaction."""
    async with get_conn() as conn:
        async with conn.transaction():
            yield conn


# ── pgvector codec ────────────────────────────────────────────────────────────

async def _register_vector_codec(conn: asyncpg.Connection) -> None:
    """
    Register a lightweight codec so asyncpg can encode/decode VECTOR columns.

    pgvector stores vectors as a custom type. asyncpg needs to know how to
    serialise Python lists to the wire format and deserialise them back.
    """
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    def _encode_vector(value: list[float]) -> str:
        """Python list → pgvector text literal: [0.1,0.2,...]"""
        return "[" + ",".join(str(v) for v in value) + "]"

    def _decode_vector(value: str) -> list[float]:
        """pgvector text literal → Python list of float."""
        return [float(v) for v in value.strip("[]").split(",")]

    await conn.set_type_codec(
        "vector",
        encoder=_encode_vector,
        decoder=_decode_vector,
        schema="public",
        format="text",
    )
