"""
Settings loaded from environment variables (or .env file).

Usage:
    from src.config import settings
    print(settings.database_url)
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # -- Database --
    database_url: str = "postgresql://northstar:northstar@localhost:5432/northstar"

    # -- Cache / Queues --
    redis_url: str = "redis://localhost:6379"

    # -- Embeddings --
    # "openai" | "local"
    embedding_provider: str = "openai"
    openai_api_key: str = ""

    # OpenAI: "text-embedding-3-small" (1536) | "text-embedding-3-large" (3072)
    # Local:  "all-MiniLM-L6-v2" (384) -- requires pip install north-star[local]
    embedding_model: str = "text-embedding-3-small"

    # Must match the model output dimension and the VECTOR(N) column size.
    embedding_dim: int = 1536

    # -- Retrieval --
    # Hybrid ranking weights: alpha*semantic + beta*keyword + gamma*recency
    retrieval_alpha: float = 0.5
    retrieval_beta: float = 0.3
    retrieval_gamma: float = 0.2

    retrieval_confidence_floor: float = 0.5
    retrieval_cache_ttl: int = 300

    # -- Scribe / Archivist agents --
    # Anthropic API key for all LLM calls (Scribe and Archivist pipelines)
    anthropic_api_key: str = ""

    # Claude model for the Scribe pipeline
    # Options: claude-sonnet-4-6 | claude-opus-4-8 | claude-haiku-4-5-20251001
    scribe_model: str = "claude-sonnet-4-6"

    # Claude model for the Archivist pipeline (can differ from Scribe)
    archivist_model: str = "claude-sonnet-4-6"

    # Cosine distance threshold for contradiction detection (0-1, lower = stricter)
    contradiction_threshold: float = 0.15

    # Maximum LLM call retries on malformed output before routing to human queue
    scribe_max_retries: int = 2

    # -- Phase 6 Lite --
    # Knowledge items older than this many days with no recent supporting report
    # are flagged as stale during the staleness scan.
    staleness_days: int = 90

    # -- API --
    api_host: str = "0.0.0.0"
    api_port: int = 8000


settings = Settings()
