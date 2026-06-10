# Postgres + pgvector + Redis — Reference Implementation

*North Star official minimal stack.*

## 1. Purpose

This document defines the official North Star reference implementation using:
- **Postgres** for structured, durable knowledge
- **pgvector** for semantic discovery
- **Redis** for ephemeral state, queues, and caching

This stack is:
- Simple and reliable
- MIT-friendly
- Easy to deploy (Docker, Railway, Fly.io, Render)
- Easy to extend or replace component by component

It is not the only possible stack. It is the **baseline**.

## 2. Why This Stack Fits North Star

### Postgres (the backbone)
- Strong relational model — perfect for reports, knowledge, decisions
- ACID guarantees — traceability and auditability
- Easy migrations — evolving schemas as North Star grows
- Full-text search built in — no extra infra for keyword search
- Works well with human-readable, structured data

### pgvector (semantic discovery)
- Semantic search across thousands of reports
- Topic clustering and duplicate detection
- Similarity-based retrieval for relevant evidence
- But **not used as memory** — aligned with North Star's philosophy

### Redis (the glue)
- Agent state and ephemeral memory
- Async queues for Scribe and Archivist pipelines
- Caching retrieval results
- Keeps Postgres clean and focused on durable knowledge

## 3. Repository Structure

```
/
├── README.md
├── LICENSE (MIT)
├── docs/
│   ├── NORTH_STAR.md
│   ├── PHILOSOPHY.md
│   ├── ARCHITECTURE.md
│   ├── MEMORY_MODEL.md
│   ├── RETRIEVAL.md
│   ├── ROADMAP.md
│   ├── AGENTS/
│   │   ├── SCRIBE.md
│   │   └── ARCHIVIST.md
│   ├── DATABASE/
│   │   ├── SCHEMA.md
│   │   └── KNOWLEDGE_GRAPH.md
│   └── IMPLEMENTATIONS/
│       └── POSTGRES_PGVECTOR_REDIS.md  ← you are here
├── src/
│   ├── db/           # Migrations, schema, DB client
│   ├── agents/       # Scribe and Archivist service stubs
│   ├── pipelines/    # Queue processors
│   ├── api/          # REST endpoints
│   └── utils/        # Embedding generation, helpers
└── docker-compose.yml
```

## 4. Data Model

See [DATABASE/SCHEMA.md](../DATABASE/SCHEMA.md) for the full DDL.

### Core Tables

| Table | Role |
|-------|------|
| `reports` | Durable, immutable work artifacts |
| `knowledge` | Curated, atomic facts with provenance |
| `decisions` | Explicit choices traceable to knowledge |
| `entities` | Domain objects (vehicles, systems, etc.) |
| `relationships` | Lightweight graph (supports/informs/contradicts/relates_to) |
| `embeddings` | pgvector semantic representations |

### Quick DDL (minimal)

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE reports (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  title TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  author TEXT,
  context_summary TEXT,
  analysis TEXT,
  conclusions TEXT,
  raw_source JSONB,
  tags TEXT[]
);

CREATE TABLE knowledge (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  statement TEXT NOT NULL,
  confidence FLOAT,
  status TEXT CHECK (status IN ('proposed','validated','deprecated','superseded')) DEFAULT 'proposed',
  source_report_ids UUID[],
  source_section TEXT,
  valid_from TIMESTAMPTZ DEFAULT NOW(),
  valid_until TIMESTAMPTZ,
  topics TEXT[]
);

CREATE TABLE decisions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  statement TEXT NOT NULL,
  rationale TEXT,
  linked_knowledge_ids UUID[],
  owner TEXT,
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  status TEXT CHECK (status IN ('planned','executed','reverted','needs_reassessment')) DEFAULT 'planned'
);

CREATE TABLE entities (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name TEXT NOT NULL,
  type TEXT,
  metadata JSONB
);

CREATE TABLE relationships (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  from_id UUID NOT NULL,
  to_id UUID NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('supports','informs','contradicts','relates_to')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE embeddings (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  object_type TEXT NOT NULL,
  object_id UUID NOT NULL,
  embedding VECTOR(1536),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops);
```

## 5. Redis Usage

Redis is used exclusively for **ephemeral, fast, non-durable** operations.

### Queues

| Key | Purpose |
|-----|---------|
| `scribe_queue` | Incoming raw inputs for the Scribe |
| `archivist_queue` | Candidates from Scribe awaiting Archivist validation |
| `report_processing_queue` | Reports pending embedding generation |
| `human_review_queue` | Items routed to human decision |

### Agent State

| Key Pattern | Purpose |
|------------|---------|
| `agent_state:{agent_id}` | Current task state for a running agent |
| `recent_context:{agent_id}` | Last N retrieved items for context assembly |

### Caching

| Key Pattern | Purpose | TTL |
|------------|---------|-----|
| `retrieval_cache:{query_hash}` | Cached retrieval results | 5 min |
| `report_summary_cache:{report_id}` | Cached report summaries | 30 min |

### Why Redis Here?
- Keeps Postgres clean and focused on durable knowledge
- Prevents memory bloat in the persistent layer
- Enables async pipelines (Scribe → Redis → Archivist)
- Supports multi-agent orchestration without shared context

## 6. Scribe Pipeline

### Input

```
POST /scribe/process
{
  "source_type": "conversation | task | document",
  "payload": { ... },
  "author": "agent-name"
}
```

### Steps

1. Normalize and clean input text
2. Generate `context_summary` — what was the situation
3. Extract reasoning and `analysis`
4. Generate `conclusions`
5. Build structured Report → INSERT into `reports`
6. Generate embedding → INSERT into `embeddings`
7. Extract Knowledge candidates using strict template
8. Extract Decision candidates
9. Discover relationship candidates
10. Flag contradiction candidates (compare against existing `validated` knowledge via pgvector)
11. Push all candidates → `archivist_queue` (Redis)

### Output

```json
{
  "report_id": "UUID",
  "status": "processed | needs_review",
  "knowledge_candidates": 4,
  "decision_candidates": 1,
  "relationship_candidates": 3
}
```

## 7. Archivist Pipeline

### Input (from Redis)

```json
{
  "knowledge_candidates": [...],
  "decision_candidates": [...],
  "relationship_candidates": [...],
  "source_report_id": "UUID"
}
```

### Steps

1. Receive candidates from `archivist_queue`
2. Load relevant existing memory (knowledge, decisions, relationships)
3. For each knowledge candidate:
   - Check for duplicates (pgvector similarity)
   - Check for contradictions (semantic + logic)
   - Classify contradiction type (temporal / direct / contextual)
   - Validate or reject
4. For each decision candidate:
   - Verify `linked_knowledge_ids` are validated
   - Verify rationale is present
   - Validate or reject
5. Insert validated knowledge → `knowledge` table
6. Insert validated decisions → `decisions` table
7. Insert validated relationships → `relationships` table
8. Mark stale knowledge (`valid_until = NOW()`, `status = deprecated`)
9. Flag decisions needing reassessment
10. Push review requests → `human_review_queue` if needed

### Output

```json
{
  "validated_knowledge": 3,
  "validated_decisions": 1,
  "merged": 1,
  "deprecated": 0,
  "relationships_inserted": 4,
  "contradictions_flagged": 1,
  "review_requests": 1
}
```

## 8. Retrieval Architecture

See [RETRIEVAL.md](../RETRIEVAL.md) for the full specification.

### Quick Reference

```
Query
  │
  ├─ Keyword search (Postgres full-text)
  ├─ Semantic search (pgvector)
  │
  ▼
Hybrid ranking (α·semantic + β·keyword + γ·recency)
  │
  ▼
Graph traversal (expand via relationships)
  │
  ▼
Filtering (confidence ≥ threshold, status = validated)
  │
  ▼
Context assembly
```

### Output

```json
{
  "reports": [...],
  "knowledge": [...],
  "decisions": [...],
  "relationships": [...]
}
```

## 9. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/scribe/process` | Submit raw activity for Scribe processing |
| POST | `/archivist/process` | Manually trigger Archivist on a candidate set |
| GET | `/retrieve` | Hybrid retrieval query |
| GET | `/reports/{id}` | Fetch a report by ID |
| GET | `/knowledge/{id}` | Fetch a knowledge item by ID |
| GET | `/decisions/{id}` | Fetch a decision by ID |
| GET | `/entities/{id}` | Fetch an entity and its related knowledge/decisions |

## 10. Deployment

### Minimal Docker Compose

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: northstar
      POSTGRES_USER: northstar
      POSTGRES_PASSWORD: northstar
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine

  scribe:
    build: ./src/agents/scribe
    environment:
      DATABASE_URL: postgresql://northstar:northstar@postgres/northstar
      REDIS_URL: redis://redis:6379
    depends_on: [postgres, redis]

  archivist:
    build: ./src/agents/archivist
    environment:
      DATABASE_URL: postgresql://northstar:northstar@postgres/northstar
      REDIS_URL: redis://redis:6379
    depends_on: [postgres, redis]

  api:
    build: ./src/api
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://northstar:northstar@postgres/northstar
      REDIS_URL: redis://redis:6379
    depends_on: [postgres, redis]

volumes:
  postgres_data:
```

### Cloud Options

| Component | Option |
|-----------|--------|
| Postgres + pgvector | Supabase (managed, free tier available) |
| Postgres + pgvector | Railway, Render, Neon |
| Redis | Upstash (managed, serverless) |
| Full stack | Railway, Render, Fly.io |

## 11. Extensibility

North Star defines interfaces, not dependencies. Users can replace:

| Component | Alternatives |
|-----------|-------------|
| Postgres | MySQL, SQLite, Supabase |
| pgvector | Qdrant, Weaviate, Milvus |
| Redis | Dragonfly, KeyDB, Valkey |
| Agents | Any LLM backend (OpenAI, Anthropic, local) |

### Stable Interfaces (do not change when swapping components)

- Report schema
- Knowledge schema (including status lifecycle)
- Decision schema (including `linked_knowledge_ids` constraint)
- Embedding interface (`object_type` + `object_id`)
- Retrieval API contract (`/retrieve` response format)

## 12. MIT License Compatibility

All components in this stack are open-source and permissively licensed:
- Postgres — PostgreSQL License
- pgvector — MIT
- Redis 7 — BSD 3-Clause
- All North Star code — MIT

No proprietary dependencies in the reference implementation.

## 13. Summary

This stack provides:
- **Durability** (Postgres)
- **Discoverability** (pgvector)
- **Performance** (Redis)
- **Traceability** (schema + relationships)
- **Curation** (Scribe + Archivist pipelines)

It is the simplest stack that fully supports the North Star principle:

> *Keep active context small. Store knowledge externally. Retrieve only what is needed.*
