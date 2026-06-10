"""004_human_review_queue

Creates a durable human_review_queue table in Postgres.

Replaces the Redis-list implementation which loses data on restart.
Items written here survive crashes, are queryable, and carry a full
audit trail (status, resolved_at, resolved_by, resolution_note).

Revision ID: 004
Revises: 003
Create Date: 2026-06-10
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE human_review_queue (
            id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
            source          TEXT        NOT NULL,
            reason          TEXT        NOT NULL,
            context         JSONB       NOT NULL    DEFAULT '{}',
            status          TEXT        NOT NULL    DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'skipped')),
            queued_at       TIMESTAMPTZ NOT NULL    DEFAULT NOW(),
            resolved_at     TIMESTAMPTZ,
            resolved_by     TEXT,
            resolution_note TEXT
        );
    """)

    # Primary access pattern: fetch pending items ordered by arrival time
    op.execute(
        "CREATE INDEX idx_hrq_status_queued ON human_review_queue (status, queued_at ASC);"
    )

    # Fast lookup by reason (e.g. "show me all high_stakes_contradiction items")
    op.execute(
        "CREATE INDEX idx_hrq_reason ON human_review_queue (reason);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS human_review_queue CASCADE;")
