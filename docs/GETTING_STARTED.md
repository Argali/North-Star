# Getting Started with North Star

This guide takes you from zero to a running North Star instance in about 10 minutes.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker + Docker Compose | any recent | runs Postgres and Redis |
| Python | 3.11 or 3.12 | for the API and agents |
| Anthropic API key | — | Scribe and Archivist use Claude |
| OpenAI API key | — | embeddings (or swap to local, see below) |

You do **not** need to install Postgres or Redis locally. Docker handles both.

---

## 1. Clone and configure

```bash
git clone https://github.com/your-org/north-star.git
cd north-star

cp .env.example .env
```

Open `.env` and fill in the two required secrets:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

Everything else has safe defaults for local development.

---

## 2. Start the infrastructure

```bash
docker compose up -d postgres redis
```

Wait a few seconds, then confirm both are healthy:

```bash
docker compose ps
```

Both services should show `healthy` or `running`.

---

## 3. Run migrations

This creates all tables, indexes, and the pgvector extension:

```bash
# Install Python dependencies first
pip install -e ".[dev]"

# Run Alembic migrations
alembic upgrade head
```

You should see two migrations applied: `001_initial_schema` and `002_knowledge_fts_index`.

---

## 4. Start the API

```bash
uvicorn src.api.main:app --reload
```

Verify it's up:

```bash
curl http://localhost:8000/health
# {"status":"ok","db":"ok","redis":"ok"}

curl http://localhost:8000/readyz
# {"ready":true}
```

The interactive API docs are at **http://localhost:8000/docs**.

---

## 5. Start the agents

Open two more terminal windows:

```bash
# Terminal 2 — Scribe (extracts knowledge from raw input)
python -m src.agents.scribe.worker

# Terminal 3 — Archivist (validates, deduplicates, resolves contradictions)
python -m src.agents.archivist.worker
```

Both workers will log `Listening on queue ...` when ready.

---

## 6. Ingest your first document

### Using the Python SDK

```python
import asyncio
from northstar import NorthStarClient

async def main():
    async with NorthStarClient() as client:
        result = await client.ingest(
            source_type="conversation",
            payload={
                "text": "We decided to use Postgres for the primary database. "
                        "SQLite was considered but ruled out due to concurrency limits. "
                        "The decision was made on 2025-01-15 by the backend team."
            },
            author="erwan",
            tags=["architecture", "database"],
        )
        print("Report ID:", result["report_id"])

asyncio.run(main())
```

Install the SDK:

```bash
pip install -e sdk/python
```

### Using curl

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "conversation",
    "payload": {
      "text": "We decided to use Postgres as our primary database."
    },
    "author": "erwan"
  }'
```

The response includes a `report_id`. The Scribe worker processes the report asynchronously and pushes knowledge candidates to the Archivist queue. Within a few seconds both agents will have logged their processing steps.

---

## 7. Retrieve context

```python
import asyncio
from northstar import NorthStarClient

async def main():
    async with NorthStarClient() as client:
        ctx = await client.retrieve(
            query="What database are we using and why?",
            intent="knowledge",
            limit=5,
        )
        for item in ctx["ranked"]:
            print(f"[{item['score']:.2f}] {item['statement']}")

asyncio.run(main())
```

Or with curl:

```bash
curl -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "What database are we using and why?", "intent": "knowledge"}'
```

The response contains `ranked` items (hybrid-scored knowledge), plus a `graph` block with related entities, decisions, and contradiction pairs discovered by the graph traverser.

---

## 8. Use the CLI

```bash
# Check status
python -m src.cli stats

# Trigger a staleness scan (flags knowledge older than 90 days with no recent source)
python -m src.cli scan
python -m src.cli scan --dry-run         # preview without queuing

# Review items flagged for human attention
python -m src.cli review
python -m src.cli review --resolve 0 approve --note "Verified manually"
python -m src.cli review --resolve 1 reject
```

Set `NORTHSTAR_URL` if your API is not on `localhost:8000`:

```bash
export NORTHSTAR_URL=http://my-server:8000
python -m src.cli stats
```

---

## 9. Backfill embeddings (optional)

If you import existing data directly into the database (bypassing the API), run the backfill script to generate embeddings for any objects that are missing them:

```bash
python -m src.tools.backfill --object-type knowledge
python -m src.tools.backfill --object-type reports --dry-run    # preview first
python -m src.tools.backfill --object-type decisions --verbose
```

---

## Key configuration options

All settings come from environment variables (or `.env`). The most useful ones:

| Variable | Default | What it does |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required. Claude API key for Scribe + Archivist. |
| `OPENAI_API_KEY` | — | Required when `EMBEDDING_PROVIDER=openai`. |
| `EMBEDDING_PROVIDER` | `openai` | Set to `local` to use sentence-transformers (no API key needed). |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI model. Switch to `text-embedding-3-large` for higher accuracy. |
| `EMBEDDING_DIM` | `1536` | Must match the model's output dimension. |
| `RETRIEVAL_ALPHA` | `0.5` | Semantic score weight in hybrid ranking. |
| `RETRIEVAL_BETA` | `0.3` | Keyword score weight. |
| `RETRIEVAL_GAMMA` | `0.2` | Recency score weight. |
| `RETRIEVAL_CONFIDENCE_FLOOR` | `0.5` | Exclude knowledge below this confidence. |
| `STALENESS_DAYS` | `90` | Days before validated knowledge is considered stale. |
| `CONTRADICTION_THRESHOLD` | `0.15` | Cosine distance below which two items are flagged as contradictory. |
| `SCRIBE_MODEL` | `claude-sonnet-4-6` | Claude model for the Scribe agent. |
| `ARCHIVIST_MODEL` | `claude-sonnet-4-6` | Claude model for the Archivist agent. |

### Using local embeddings (no OpenAI key)

Set in `.env`:

```env
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=all-MiniLM-L6-v2
EMBEDDING_DIM=384
```

Then install the extra dependency:

```bash
pip install sentence-transformers
```

Semantic quality is lower than OpenAI's models but the system runs fully offline.

---

## Running with Docker Compose (full stack)

To run the API inside Docker alongside Postgres and Redis:

```bash
docker compose --profile app up -d
```

The API is exposed at `http://localhost:8000`. For the agents, either run them locally (step 5 above) or add their service definitions to `docker-compose.yml` (see the commented-out `scribe` and `archivist` blocks in that file).

---

## What happens to ingested data

```
Your text
   │
   ▼
POST /ingest  →  creates a Report  →  pushes to ns:queue:scribe
                                              │
                                              ▼
                                    Scribe worker
                                    (extracts knowledge candidates,
                                     decision candidates, entities)
                                              │
                                              ▼
                                    Archivist queue (ns:queue:archivist)
                                              │
                                              ▼
                                    Archivist worker
                                    (validates, deduplicates,
                                     resolves contradictions)
                                              │
                                    ┌─────────┴──────────┐
                                    ▼                    ▼
                             knowledge table      human_review_queue
                             status=validated     (contradictions,
                                                   high-stakes items)
```

Knowledge that passes validation is immediately available for retrieval via `/retrieve`.

---

## Next steps

- **Extend the system** — see [CONTRIBUTING.md](../CONTRIBUTING.md) to add agents, entity types, or decision workflows.
- **Understand the architecture** — [docs/ARCHITECTURE.md](ARCHITECTURE.md) explains the full data flow.
- **Tune retrieval** — [docs/RETRIEVAL.md](RETRIEVAL.md) covers the hybrid scoring formula and graph traversal.
- **See the roadmap** — [docs/ROADMAP.md](ROADMAP.md) shows what's planned for North Star Enterprise.
