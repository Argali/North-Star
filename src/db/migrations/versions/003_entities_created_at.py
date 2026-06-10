"""003_entities_created_at

Adds created_at column to the entities table so the hybrid retrieval scorer
can apply a recency component when ranking entity results.

Revision ID: 003
Revises: 002
Create Date: 2026-06-10
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add created_at with a default of NOW() so existing rows get a sensible
    # timestamp and new rows are timestamped automatically.
    op.execute(
        """
        ALTER TABLE entities
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
        """
    )

    # Backfill existing rows (if any) to NOW() so they are not left as NULL.
    op.execute(
        "UPDATE entities SET created_at = NOW() WHERE created_at IS NULL"
    )

    # Index for recency ordering.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_entities_created_at ON entities (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_entities_created_at")
    op.execute("ALTER TABLE entities DROP COLUMN IF EXISTS created_at")
