"""002_knowledge_fts_index

Adds a GIN full-text search index on the knowledge table so hybrid
retrieval can run efficient keyword queries against statements and topics.

Revision ID: 002
Revises: 001
Create Date: 2026-06-10
"""

from alembic import op

# Alembic metadata
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Full-text index on statement + topics combined.
    # array_to_string converts the topics text[] to a space-separated string
    # so topic slugs are included in the tsvector for keyword matching.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_fts
        ON knowledge
        USING GIN (
            to_tsvector(
                'english',
                statement || ' ' || COALESCE(array_to_string(topics, ' '), '')
            )
        )
        """
    )

    # Separate index on valid_from for the recency component of hybrid scoring.
    # knowledge already has a PK index, but a dedicated index on valid_from
    # speeds up ORDER BY valid_from DESC in the recency sub-query.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_valid_from ON knowledge (valid_from DESC)"
    )

    # Status + confidence composite — used by the confidence_floor filter
    # that runs on every retrieval query.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_status_confidence "
        "ON knowledge (status, confidence DESC NULLS LAST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_knowledge_fts")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_valid_from")
    op.execute("DROP INDEX IF EXISTS idx_knowledge_status_confidence")
