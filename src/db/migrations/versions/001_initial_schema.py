"""Initial schema — North Star reference stack.

Creates all 6 tables, indexes, and extensions required by the North Star
memory architecture. Matches the DDL defined in docs/DATABASE/SCHEMA.md.

Revision ID: 001
Revises:     (none — this is the first migration)
Create Date: 2026-06-10
"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ── reports ───────────────────────────────────────────────────────────────
    # Durable, immutable work artifacts produced by the Scribe.
    # Records are never deleted — they are the source of truth for all knowledge
    # and decisions.
    op.execute("""
        CREATE TABLE reports (
            id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            title           TEXT        NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            author          TEXT,
            context_summary TEXT,
            analysis        TEXT,
            conclusions     TEXT,
            raw_source      JSONB,
            tags            TEXT[]
        );
    """)

    # ── knowledge ─────────────────────────────────────────────────────────────
    # Atomic, validated facts extracted from reports.
    # Lifecycle: proposed → validated → deprecated | superseded
    op.execute("""
        CREATE TABLE knowledge (
            id                UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            statement         TEXT        NOT NULL,
            confidence        FLOAT       CHECK (confidence >= 0 AND confidence <= 1),
            status            TEXT        CHECK (status IN ('proposed','validated','deprecated','superseded'))
                                          DEFAULT 'proposed',
            source_report_ids UUID[],
            source_section    TEXT,
            valid_from        TIMESTAMPTZ DEFAULT NOW(),
            valid_until       TIMESTAMPTZ,
            topics            TEXT[]
        );
    """)

    # ── decisions ─────────────────────────────────────────────────────────────
    # Explicit organisational choices traceable to validated knowledge.
    # Lifecycle: planned → executed | reverted | needs_reassessment
    # Archivist rejects decisions with empty linked_knowledge_ids or missing rationale.
    op.execute("""
        CREATE TABLE decisions (
            id                   UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            statement            TEXT        NOT NULL,
            rationale            TEXT,
            linked_knowledge_ids UUID[],
            owner                TEXT,
            timestamp            TIMESTAMPTZ DEFAULT NOW(),
            status               TEXT        CHECK (status IN ('planned','executed','reverted','needs_reassessment'))
                                             DEFAULT 'planned'
        );
    """)

    # ── entities ──────────────────────────────────────────────────────────────
    # Domain objects (vehicles, systems, suppliers, projects, customers).
    # Enables cross-report linking: "Vehicle 259" in one report is the same
    # entity in every other report and decision.
    op.execute("""
        CREATE TABLE entities (
            id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name     TEXT NOT NULL,
            type     TEXT,
            metadata JSONB
        );
    """)

    # ── relationships ─────────────────────────────────────────────────────────
    # Lightweight graph layer stored in Postgres.
    # Edge vocabulary is strict — no ad-hoc edge types.
    #
    # supports   : Report    → Knowledge  (evidence)
    # informs    : Knowledge → Decision   (justification)
    # contradicts: Knowledge ↔ Knowledge  (conflict, bidirectional)
    # relates_to : Knowledge/Decision → Entity  (domain link)
    op.execute("""
        CREATE TABLE relationships (
            id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            from_id    UUID        NOT NULL,
            to_id      UUID        NOT NULL,
            type       TEXT        NOT NULL
                       CHECK (type IN ('supports','informs','contradicts','relates_to')),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # ── embeddings ────────────────────────────────────────────────────────────
    # pgvector semantic representations.
    # Used for: semantic search, duplicate detection, topic clustering.
    # Embeddings are a discovery tool — NOT curated memory.
    # Dimension 1536 matches OpenAI text-embedding-3-small and text-embedding-3-large (1536).
    # Adjust by changing the EMBEDDING_DIM env var and this column if you swap models.
    op.execute("""
        CREATE TABLE embeddings (
            id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            object_type TEXT        NOT NULL
                        CHECK (object_type IN ('report','knowledge','decision')),
            object_id   UUID        NOT NULL,
            embedding   VECTOR(1536),
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # ── Indexes ───────────────────────────────────────────────────────────────

    # Knowledge — fast lookups by lifecycle status and topic tags
    op.execute("CREATE INDEX idx_knowledge_status ON knowledge (status);")
    op.execute("CREATE INDEX idx_knowledge_topics ON knowledge USING GIN (topics);")

    # Reports — fast lookups by topic tags
    op.execute("CREATE INDEX idx_reports_tags ON reports USING GIN (tags);")

    # Reports — full-text search index (title + context_summary + conclusions)
    op.execute("""
        CREATE INDEX idx_reports_fts ON reports
        USING GIN (
            to_tsvector('english',
                title || ' ' ||
                COALESCE(context_summary, '') || ' ' ||
                COALESCE(conclusions, '')
            )
        );
    """)

    # Relationships — fast traversal in both directions, and by type
    op.execute("CREATE INDEX idx_relationships_from ON relationships (from_id);")
    op.execute("CREATE INDEX idx_relationships_to   ON relationships (to_id);")
    op.execute("CREATE INDEX idx_relationships_type ON relationships (type);")

    # Embeddings — fast object lookup and ivfflat vector search
    op.execute("CREATE INDEX idx_embeddings_object ON embeddings (object_id, object_type);")
    op.execute("""
        CREATE INDEX idx_embeddings_vector
        ON embeddings
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100);
    """)
    # Note: ivfflat requires data to be present before the index is useful.
    # For empty tables, the index is created but will do sequential scans
    # until enough rows exist. lists=100 is appropriate for up to ~1M rows.


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS embeddings   CASCADE;")
    op.execute("DROP TABLE IF EXISTS relationships CASCADE;")
    op.execute("DROP TABLE IF EXISTS entities     CASCADE;")
    op.execute("DROP TABLE IF EXISTS decisions    CASCADE;")
    op.execute("DROP TABLE IF EXISTS knowledge    CASCADE;")
    op.execute("DROP TABLE IF EXISTS reports      CASCADE;")
    # Note: we do NOT drop the vector or uuid-ossp extensions
    # because other schemas in the database may depend on them.
