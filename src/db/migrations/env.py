"""
Alembic environment configuration.

Reads DATABASE_URL from the environment (or .env file) so no credentials
are ever stored in alembic.ini or committed to the repository.

Usage:
    alembic upgrade head          # apply all migrations
    alembic downgrade -1          # rollback last migration
    alembic revision -m "..."     # create a new migration file
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from dotenv import load_dotenv

# Load .env so DATABASE_URL is available even when running alembic directly
load_dotenv()

# Alembic Config object — provides access to values in alembic.ini
config = context.config

# Configure Python logging from the ini file section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# We use raw-SQL migrations (no SQLAlchemy ORM models), so target_metadata is None.
# This disables autogenerate but keeps migrations fully transparent and portable.
target_metadata = None

# ── Database URL ──────────────────────────────────────────────────────────────
def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Copy .env.example to .env and fill in your credentials."
        )
    return url


# ── Offline mode ──────────────────────────────────────────────────────────────
# Used when generating SQL scripts without a live database connection.
def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode ───────────────────────────────────────────────────────────────
# Used for `alembic upgrade head` against a live database.
def run_migrations_online() -> None:
    engine = create_engine(
        get_database_url(),
        poolclass=pool.NullPool,  # single connection per migration run
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
