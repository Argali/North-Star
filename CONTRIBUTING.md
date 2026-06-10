# Contributing to North Star

North Star is MIT-licensed. Contributions are welcome.

---

## Running the stack locally

**Prerequisites:** Docker, Python 3.11+, `uv` or `pip`.

```bash
# 1. Clone and enter the repo
git clone https://github.com/YOUR_ORG/north-star
cd north-star

# 2. Copy environment template
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY

# 3. Start Postgres + Redis
docker compose up -d postgres redis

# 4. Run migrations
docker compose --profile tools run migrate

# 5. Install Python dependencies
pip install -e ".[dev]"

# 6. Start the API
northstar-api
# or: uvicorn src.api.main:app --reload
```

The API is now at `http://localhost:8000`. Swagger docs at `/docs`.

To start the Scribe and Archivist workers:

```bash
python -m src.agents.scribe.worker &
python -m src.agents.archivist.worker &
```

---

## Running tests

```bash
pytest tests/
```

---

## Swapping components

### Postgres → SQLite

North Star uses `asyncpg` and raw SQL (no ORM), so swapping requires:

1. Replace `asyncpg` with `aiosqlite` in `pyproject.toml`.
2. Rewrite `src/db/client.py` — replace the `asyncpg.Pool` with an `aiosqlite` connection.
3. Rewrite the Alembic `env.py` to use `sqlite+aiosqlite://`.
4. Audit the migration SQL — SQLite does not support `uuid-ossp`, `GIN` indexes,
   `ARRAY` columns, or the `vector` type. Replace:
   - UUIDs with `TEXT DEFAULT (hex(randomblob(16)))`
   - `text[]` arrays with JSON columns (`TEXT` + `json_each()` in queries)
   - pgvector with an in-memory FAISS index (`faiss-cpu`) loaded at startup
5. Update hybrid retrieval to query the in-memory FAISS index instead of pgvector.

SQLite is suitable for local development and single-user deployments.
For production use, keep Postgres.

---

### pgvector → Qdrant

pgvector stores embeddings in Postgres. To use Qdrant instead:

1. Add `qdrant-client[async]` to `pyproject.toml`.
2. Add `QDRANT_URL` and `QDRANT_API_KEY` to `.env.example` and `src/config.py`.
3. Create `src/utils/vector_store.py` with an abstract `VectorStore` interface:
   - `upsert(id, embedding, payload)` 
   - `search(embedding, top_k, filter)` → list of `(id, score)`
4. Replace the pgvector queries in `src/retrieval/scorer.py` with calls to `vector_store.search()`.
5. Replace the `INSERT INTO embeddings` calls in Scribe and Archivist pipelines with
   `vector_store.upsert()`.
6. The `embeddings` table in Postgres can be left in place (for audit) or dropped.

The rest of North Star (reports, knowledge, decisions, relationships) stays in Postgres.

---

### OpenAI embeddings → local embeddings

1. In `.env`, set:
   ```
   EMBEDDING_PROVIDER=local
   EMBEDDING_MODEL=all-MiniLM-L6-v2
   EMBEDDING_DIM=384
   ```
2. Install the extra: `pip install "north-star[local]"` (adds `sentence-transformers`).
3. **Important:** update the `VECTOR(1536)` column in the migration to match your model's
   dimension (`VECTOR(384)` for MiniLM). Create a new migration:
   ```sql
   ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector(384);
   DROP INDEX idx_embeddings_vector;
   CREATE INDEX idx_embeddings_vector ON embeddings
     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
   ```
4. Re-embed existing records by running the backfill script (Phase 6).

---

## Adding a new agent

Use the template in `templates/new_agent/`:

1. Copy the directory: `cp -r templates/new_agent src/agents/my_agent`
2. Replace all `<AgentName>` and `<agent_name>` placeholders.
3. Add a queue for your agent in `src/pipelines/queues.py`:
   ```python
   my_agent_queue = Queue(QueueName.MY_AGENT)
   ```
4. Add `MY_AGENT = "my_agent"` to `QueueName`.
5. Implement the pipeline stages in `pipeline.py`.
6. Register the worker in `docker-compose.yml` (copy the `archivist` service block).

---

## Adding a new entity type

See `templates/new_entity_type.md`.

---

## Adding a new decision workflow

See `templates/new_decision_workflow.md`.

---

## Code style

- Python: `ruff` for linting, `black` for formatting (both in `[dev]` extras).
- TypeScript: `tsc --strict` must pass with zero errors.
- All new pipelines must handle failures gracefully and route to `human_review_queue`.
- All DB writes must use the `transaction()` context manager.
- No ORM. Raw SQL only — it keeps the migration story simple and the queries auditable.

---

## Submitting a PR

1. Open an issue first for non-trivial changes.
2. Keep PRs focused — one concern per PR.
3. Add or update tests for any new pipeline behaviour.
4. Update the relevant doc in `/docs` if the architecture changes.
5. Run `ruff check . && black --check . && pytest` before opening the PR.
