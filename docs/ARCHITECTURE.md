# System Architecture

*How North Star works end-to-end.*

## 1. Purpose

North Star is an architecture for AI organizations that accumulate institutional knowledge without accumulating context. It defines how:
- **Reports** are created from raw activity
- **Knowledge** is extracted and curated
- **Decisions** are validated and traced
- **Memory** remains small and coherent
- **Agents** collaborate without sharing context
- **Retrieval** returns only what is needed

## 2. High-Level System Overview

North Star is built around four pillars:

| Pillar | Description |
|--------|------------|
| **Reports** | Durable evidence — the historical record |
| **Knowledge** | Curated facts extracted from reports |
| **Decisions** | Traceable organizational choices |
| **Retrieval** | Minimal, targeted context assembly |

These are produced and maintained by two core agents:
- **Scribe** — creates structure from raw activity
- **Archivist** — protects quality and maintains the knowledge graph

And stored in one reference stack:
- **Postgres** — structured memory
- **pgvector** — semantic discovery
- **Redis** — ephemeral state and queues

## 3. Architecture Diagram

```
Raw Activity (conversations, task logs, documents)
        │
        ▼
┌───────────────────────────────┐
│           SCRIBE              │
│  Summarize → Extract → Build  │
│  Report, Knowledge, Decisions │
└───────────────┬───────────────┘
                │
        ┌───────┴────────┐
        ▼                ▼
┌──────────────┐   ┌──────────────────┐
│   Postgres   │◄──│   ARCHIVIST      │
│  Reports     │   │  Validate        │
│  Knowledge   │   │  Deduplicate     │
│  Decisions   │   │  Resolve         │
│  Graph       │   │  Deprecate       │
└──────┬───────┘   └──────────────────┘
       │
       ▼
┌──────────────────────────────┐
│         RETRIEVAL            │
│  Keyword + Semantic + Graph  │
│  → Minimal Context Package   │
└──────────────────────────────┘
        │
        ▼
   Agent receives
   only what it needs
```

## 4. Core Principles

### 4.1 Keep Active Context Small
Agents never load full histories. They retrieve only what is relevant to the current task.

### 4.2 Reports Are First-Class Objects
Conversations are temporary. Reports are durable, immutable, and serve as the evidence base for all knowledge.

### 4.3 Knowledge Is Extracted, Not Stored Raw
Reports → evidence. Knowledge → reusable facts. The extraction step is non-optional.

### 4.4 Decisions Are Separate From Knowledge
Knowledge = "what is true." Decision = "what we chose to do." Separate entities, separate lifecycles.

### 4.5 Memory Must Be Curated
Not everything gets stored. The Archivist is the quality gate.

## 5. Components

### 5.1 Scribe (Creation Layer)

The Scribe transforms raw activity into structured artifacts. See [AGENTS/SCRIBE.md](AGENTS/SCRIBE.md).

**Inputs:** conversations, task logs, agent outputs, documents

**Outputs:**
- Structured Report → Postgres
- Knowledge candidates → Archivist queue (Redis)
- Decision candidates → Archivist queue (Redis)
- Relationship candidates → Archivist queue (Redis)
- Embeddings → pgvector

### 5.2 Archivist (Curation Layer)

The Archivist validates and maintains long-term memory quality. See [AGENTS/ARCHIVIST.md](AGENTS/ARCHIVIST.md).

**Actions:**
- Validates knowledge and decisions
- Merges duplicates
- Resolves or flags contradictions
- Marks stale knowledge
- Maintains the relationship graph
- Routes ambiguous items to human review

### 5.3 Storage Layer

**Postgres** stores:
- `reports` — durable work artifacts
- `knowledge` — curated facts with provenance
- `decisions` — explicit choices with rationale
- `entities` — domain objects (vehicles, systems, etc.)
- `relationships` — lightweight knowledge graph

**pgvector** stores:
- `embeddings` — semantic representations for discovery

**Redis** stores:
- `scribe_queue` / `archivist_queue` — async pipelines
- `agent_state:{id}` — ephemeral agent state
- `retrieval_cache:{hash}` — query result caching

See [DATABASE/SCHEMA.md](DATABASE/SCHEMA.md).

## 6. Data Flow

### Step-by-Step

1. **Raw Activity** — a conversation, task, or document arrives
2. **Scribe ingests** — normalizes, summarizes, extracts knowledge and decisions
3. **Report is stored** — immutable artifact written to Postgres
4. **Embeddings generated** — stored in pgvector for semantic search
5. **Candidates queued** — knowledge + decision candidates pushed to Redis
6. **Archivist processes** — validates, deduplicates, resolves contradictions
7. **Memory updated** — curated knowledge and decisions written to Postgres
8. **Graph updated** — relationships inserted or updated
9. **Retrieval ready** — future agents can query the curated store

### From Conversation to Retrieval

```
Conversation
    → Scribe → Report (stored, immutable)
    → Scribe → Knowledge candidates (proposed)
    → Archivist → Knowledge (validated)
    → Archivist → Relationships (graph)
    → Retrieval → Minimal context (for next agent)
```

## 7. Retrieval Architecture

Retrieval is the mechanism that keeps context small. It is hybrid by design.

**Pipeline:**
1. Keyword search (Postgres full-text)
2. Semantic search (pgvector)
3. Hybrid ranking (semantic + keyword + recency)
4. Graph traversal (expand via relationships)
5. Filtering (confidence, status, topic)
6. Context assembly (minimal package)

**Output format:**
```json
{
  "reports": [...],
  "knowledge": [...],
  "decisions": [...],
  "relationships": [...]
}
```

See [RETRIEVAL.md](RETRIEVAL.md).

## 8. Knowledge Graph (Lightweight)

North Star uses a lightweight graph stored in Postgres — no separate graph database required for the reference implementation.

**Nodes:** reports, knowledge, decisions, entities

**Edges:**
- `supports` — report provides evidence for knowledge
- `informs` — knowledge supports a decision
- `contradicts` — conflicting knowledge items
- `relates_to` — links to domain entities

See [DATABASE/KNOWLEDGE_GRAPH.md](DATABASE/KNOWLEDGE_GRAPH.md).

## 9. Agent Collaboration Model

Agents communicate through the shared memory layer, not through shared context.

```
Agent A produces → Report → Knowledge → Decisions
Agent B retrieves → only what it needs from curated store
```

North Star encourages specialization: Architect, QA, Compliance, Maintenance, Cost Analysis, Security — each retrieves only its relevant slice.

## 10. Deployment Model

### Reference Stack
- Postgres + pgvector extension
- Redis
- Scribe service (`POST /scribe/process`)
- Archivist service (`POST /archivist/process`)
- Retrieval API (`GET /retrieve`)

### Minimal Docker Compose
- `postgres` (with pgvector extension)
- `redis`
- `scribe` service
- `archivist` service
- `api` service

### Cloud Options
- Railway, Render, Fly.io (full stack)
- Supabase (Postgres + pgvector managed)
- Upstash (Redis managed)

## 11. Extensibility

North Star defines **interfaces**, not dependencies.

Users may replace:
- Postgres → MySQL, SQLite, Supabase
- pgvector → Qdrant, Weaviate, Milvus
- Redis → Dragonfly, KeyDB
- Agents → any LLM backend

As long as the report, knowledge, decision schemas, and retrieval contract are preserved, the system is compatible.

## 12. Success Criteria

- 10,000 reports remain as searchable as 10
- Every decision traces back to evidence
- Contradictions are visible, not silent
- Agents operate on minimal context
- A human can audit any item without a model
- Growth increases capability, not complexity
