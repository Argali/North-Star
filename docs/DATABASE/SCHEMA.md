# Database Schema

*North Star reference schema — Postgres + pgvector.*

## 1. Purpose

This schema defines the durable storage layer for North Star using:
- **Postgres** for structured data
- **pgvector** for semantic search
- **Redis** for ephemeral queues and agent state (not defined here — see [IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md](../IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md))

The schema supports six canonical entity types:
- Reports
- Knowledge
- Decisions
- Entities
- Relationships
- Embeddings

## 2. Design Philosophy

This schema is built to be:
- **Traceable** — every fact links back to its source report
- **Auditable** — nothing is hard-deleted; status fields preserve history
- **Simple** — lightweight graph-in-Postgres instead of a separate graph DB
- **Human-readable** — no opaque blobs; every field has a clear purpose
- **Extensible** — stable interfaces, swappable backends

## 3. Schema Definition

### 3.1 `reports`

Durable, immutable work artifacts produced by the Scribe.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique identifier |
| `title` | text | NOT NULL | Human-readable title |
| `created_at` | timestamptz | DEFAULT NOW() | Creation timestamp |
| `author` | text | | Agent or human who produced it |
| `context_summary` | text | | What was the situation |
| `analysis` | text | | What was examined and how |
| `conclusions` | text | | What was found |
| `raw_source` | jsonb | | Optional: original logs/transcript |
| `tags` | text[] | | Topic classification |

**Notes:**
- Reports are **immutable** after creation — they are evidence, not drafts
- `raw_source` is optional and may be omitted for storage efficiency
- `tags` should use consistent, lowercase slugs (e.g., `fleet-maintenance`)

### 3.2 `knowledge`

Atomic, validated facts extracted from reports.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique identifier |
| `statement` | text | NOT NULL | A single declarative fact |
| `confidence` | float | CHECK 0–1 | Confidence score |
| `status` | text | CHECK (proposed, validated, deprecated, superseded) | Lifecycle status |
| `source_report_ids` | UUID[] | | Provenance — source report UUIDs |
| `source_section` | text | | Paragraph-level pointer within source report |
| `valid_from` | timestamptz | DEFAULT NOW() | When this became true |
| `valid_until` | timestamptz | | When this stopped being true |
| `topics` | text[] | | Classification tags |

**Notes:**
- Only `validated` knowledge is used in retrieval by default
- `deprecated` and `superseded` items are retained for audit trail
- `source_section` enables paragraph-level provenance (use section IDs or content hashes)

### 3.3 `decisions`

Explicit organizational choices traceable to validated knowledge.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique identifier |
| `statement` | text | NOT NULL | The decision text |
| `rationale` | text | | Why this decision was made |
| `linked_knowledge_ids` | UUID[] | | Which knowledge items support this |
| `owner` | text | | Human or agent responsible |
| `timestamp` | timestamptz | DEFAULT NOW() | When decided |
| `status` | text | CHECK (planned, executed, reverted, needs_reassessment) | Lifecycle status |

**Notes:**
- Decisions rejected by the Archivist if `linked_knowledge_ids` is empty or rationale is missing
- `needs_reassessment` is set automatically when a linked knowledge item is deprecated or superseded

### 3.4 `entities`

Domain objects referenced across reports, knowledge, and decisions.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique identifier |
| `name` | text | NOT NULL | Entity name |
| `type` | text | | e.g., `vehicle`, `system`, `supplier`, `project` |
| `metadata` | jsonb | | Arbitrary domain data |

**Notes:**
- Entities enable cross-report linking ("Vehicle 259" in one report linked to another)
- `metadata` is flexible — can store any domain-specific attributes

### 3.5 `relationships`

Lightweight graph structure stored in Postgres.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique identifier |
| `from_id` | UUID | NOT NULL | Source node |
| `to_id` | UUID | NOT NULL | Target node |
| `type` | text | NOT NULL, CHECK (supports, informs, contradicts, relates_to) | Relationship type |
| `created_at` | timestamptz | DEFAULT NOW() | Timestamp |

**Edge type semantics:**

| Type | From | To | Meaning |
|------|------|----|---------|
| `supports` | Report | Knowledge | Report provides evidence for knowledge |
| `informs` | Knowledge | Decision | Knowledge supports a decision |
| `contradicts` | Knowledge | Knowledge | Conflicting facts (bidirectional) |
| `relates_to` | Knowledge or Decision | Entity | References a domain object |

**Notes:**
- This structure enables multi-hop traversal without a dedicated graph database
- No edge is created without both nodes already existing
- Contradictions are explicitly modeled — never silently overwritten

### 3.6 `embeddings` (pgvector)

Semantic vector representations for discovery.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, DEFAULT uuid_generate_v4() | Unique identifier |
| `object_type` | text | NOT NULL, CHECK (report, knowledge, decision) | Type of the source object |
| `object_id` | UUID | NOT NULL | FK to the source object |
| `embedding` | vector(1536) | | pgvector column (1536 = OpenAI ada-002 / text-embedding-3-small) |
| `created_at` | timestamptz | DEFAULT NOW() | Timestamp |

**Notes:**
- Used for semantic search, duplicate detection, and topic clustering
- Embeddings are **not** curated memory — they are a discovery tool
- Dimension 1536 is default; adjust to match your embedding model

## 4. Full DDL

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- Reports
CREATE TABLE reports (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  title           TEXT NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  author          TEXT,
  context_summary TEXT,
  analysis        TEXT,
  conclusions     TEXT,
  raw_source      JSONB,
  tags            TEXT[]
);

-- Knowledge
CREATE TABLE knowledge (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  statement         TEXT NOT NULL,
  confidence        FLOAT CHECK (confidence >= 0 AND confidence <= 1),
  status            TEXT CHECK (status IN ('proposed','validated','deprecated','superseded'))
                    DEFAULT 'proposed',
  source_report_ids UUID[],
  source_section    TEXT,
  valid_from        TIMESTAMPTZ DEFAULT NOW(),
  valid_until       TIMESTAMPTZ,
  topics            TEXT[]
);

-- Decisions
CREATE TABLE decisions (
  id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  statement             TEXT NOT NULL,
  rationale             TEXT,
  linked_knowledge_ids  UUID[],
  owner                 TEXT,
  timestamp             TIMESTAMPTZ DEFAULT NOW(),
  status                TEXT CHECK (status IN ('planned','executed','reverted','needs_reassessment'))
                        DEFAULT 'planned'
);

-- Entities
CREATE TABLE entities (
  id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name     TEXT NOT NULL,
  type     TEXT,
  metadata JSONB
);

-- Relationships
CREATE TABLE relationships (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  from_id    UUID NOT NULL,
  to_id      UUID NOT NULL,
  type       TEXT NOT NULL CHECK (type IN ('supports','informs','contradicts','relates_to')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Embeddings (pgvector)
CREATE TABLE embeddings (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  object_type TEXT NOT NULL CHECK (object_type IN ('report','knowledge','decision')),
  object_id   UUID NOT NULL,
  embedding   VECTOR(1536),
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_knowledge_status   ON knowledge (status);
CREATE INDEX idx_knowledge_topics   ON knowledge USING GIN (topics);
CREATE INDEX idx_reports_tags       ON reports USING GIN (tags);
CREATE INDEX idx_relationships_from ON relationships (from_id);
CREATE INDEX idx_relationships_to   ON relationships (to_id);
CREATE INDEX idx_relationships_type ON relationships (type);
CREATE INDEX idx_embeddings_object  ON embeddings (object_id, object_type);
CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops);

-- Full-text search index on reports
CREATE INDEX idx_reports_fts ON reports
  USING GIN (to_tsvector('english', title || ' ' || COALESCE(context_summary, '') || ' ' || COALESCE(conclusions, '')));
```

## 5. Key Retrieval Patterns

### Find all knowledge supporting a decision

```sql
SELECT k.*
FROM knowledge k
WHERE k.id = ANY(
  SELECT UNNEST(linked_knowledge_ids)
  FROM decisions
  WHERE id = $1
);
```

### Trace a decision back to source reports

```sql
SELECT DISTINCT r.*
FROM decisions d
JOIN knowledge k ON k.id = ANY(d.linked_knowledge_ids)
JOIN reports r ON r.id = ANY(k.source_report_ids)
WHERE d.id = $1;
```

### Find similar reports (semantic)

```sql
SELECT r.id, r.title, e.embedding <-> $1 AS distance
FROM embeddings e
JOIN reports r ON r.id = e.object_id
WHERE e.object_type = 'report'
ORDER BY e.embedding <-> $1
LIMIT 10;
```

### Detect contradictions for a knowledge item

```sql
SELECT *
FROM relationships
WHERE type = 'contradicts'
  AND (from_id = $1 OR to_id = $1);
```

### Full-text search on reports

```sql
SELECT *
FROM reports
WHERE to_tsvector('english', title || ' ' || COALESCE(context_summary, '') || ' ' || COALESCE(conclusions, ''))
  @@ plainto_tsquery($1)
LIMIT 20;
```

### All knowledge related to an entity

```sql
SELECT k.*
FROM relationships rel
JOIN knowledge k ON k.id = rel.from_id
WHERE rel.to_id = $1
  AND rel.type = 'relates_to'
  AND k.status = 'validated';
```

## 6. Schema Integrity Rules

- **No orphan knowledge** — every knowledge item must have at least one `source_report_ids` entry
- **No decision without linked knowledge** — Archivist rejects empty `linked_knowledge_ids`
- **No edge without existing nodes** — enforced at application level
- **No silent deletes** — use `status` transitions, never DELETE
- **Embeddings stay in sync** — every new report, knowledge, and decision entry triggers embedding generation

## 7. Extensibility

**Swappable storage:**
- Postgres → MySQL, SQLite, Supabase (preserve schema)
- pgvector → Qdrant, Weaviate, Milvus (implement same embedding interface)

**Adding graph power:**
- For advanced graph reasoning, sync the `relationships` table to Neo4j via CDC
- The Postgres layer remains the source of truth

**Stable interfaces:**
These schemas define North Star's memory contract. Downstream agents, retrieval systems, and implementations must respect:
- Report schema
- Knowledge schema (including status lifecycle)
- Decision schema (including linked_knowledge_ids constraint)
- Embedding interface (object_type + object_id)
