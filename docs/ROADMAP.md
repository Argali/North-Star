# Roadmap

*How North Star evolves from philosophy to operational infrastructure.*

## Overview

North Star is built in six phases. Each phase produces usable, shippable output. No phase creates debt that blocks the next.

```
Phase 1 → Foundations          (Month 0–1)
Phase 2 → Reference Stack      (Month 1–3)
Phase 3 → Agent Ecosystem      (Month 3–6)
Phase 4 → Retrieval Engine     (Month 6–9)
Phase 5 → Developer Experience (Month 9–12)
Phase 6 → Scaling & Governance (Month 12–24)
```

---

## Phase 1 — Foundations (Month 0–1)

**Goal:** Establish the conceptual and structural backbone. Make the architecture clear enough that a developer can implement it without asking questions.

**Deliverables:**
- `docs/NORTH_STAR.md` — manifesto & purpose ✓
- `docs/PHILOSOPHY.md` — core beliefs ✓
- `docs/ARCHITECTURE.md` — system overview ✓
- `docs/MEMORY_MODEL.md` — ontology ✓
- Directory structure under `/docs` ✓
- MIT license ✓

**Success Criteria:**
- Architecture is clear, opinionated, and stable
- Contributors understand the philosophy without reading code
- A developer can start implementing the schema from docs alone

---

## Phase 2 — Reference Stack (Month 1–3)

**Goal:** Build the minimal, official implementation using Postgres + pgvector + Redis.

**Deliverables:**
- `docs/DATABASE/SCHEMA.md` — full DDL ✓
- `docs/DATABASE/KNOWLEDGE_GRAPH.md` — graph spec ✓
- `docs/IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md` ✓
- `/src/db/` — migration files, DB client
- `/src/utils/embeddings.py` — embedding generation (wraps any model)
- `docker-compose.yml` — Postgres + pgvector + Redis

**Success Criteria:**
- System can ingest a report and store it with embeddings
- Knowledge and decisions can be inserted and retrieved
- Docker Compose brings up a working local environment in one command

---

## Phase 3 — Agent Ecosystem (Month 3–6)

**Goal:** Implement the two core agents: Scribe and Archivist.

**Deliverables:**
- `docs/AGENTS/SCRIBE.md` — spec ✓
- `docs/AGENTS/ARCHIVIST.md` — spec ✓
- `/src/agents/scribe/` — Scribe service
  - Input normalization
  - Report generation
  - Knowledge extraction (strict template with scope/conditions/uncertainties)
  - Decision extraction
  - Relationship proposal
  - Contradiction detection
- `/src/agents/archivist/` — Archivist service
  - Candidate validation
  - Deduplication (pgvector similarity)
  - Contradiction resolution state machine (temporal vs. direct)
  - Staleness detection
  - Decision impact tracing (`needs_reassessment` propagation)
  - Human review routing

**Hard Problems to Solve in This Phase:**

1. **Scribe compression loss** — use dual-layer output (full report + atomic knowledge with source excerpts). Enforce `scope_conditions` and `uncertainties` slots for high-stakes domains.

2. **Contradiction resolution** — build explicit state machine: temporal evolution vs. genuine conflict. When ambiguous, always route to `human_review_queue`.

3. **Decision impact tracing** — when knowledge is deprecated, automatically flag all linked decisions as `needs_reassessment`.

**Success Criteria:**
- Reports → knowledge → decisions pipeline works end-to-end
- Archivist prevents memory bloat (duplicates merged, stale items deprecated)
- Contradictions are detected, classified, and either resolved or routed for human review
- Decision reassessment propagates correctly when knowledge changes

---

## Phase 4 — Retrieval Engine (Month 6–9)

**Goal:** Build the hybrid retrieval system that keeps agent context minimal.

**Deliverables:**
- `docs/RETRIEVAL.md` ✓
- `/src/api/retrieve.py` — retrieval endpoint
  - Keyword search (Postgres full-text)
  - Semantic search (pgvector)
  - Hybrid ranking (configurable α/β/γ weights)
  - Graph traversal (expand via relationships)
  - Filtering (confidence, status, topic, temporal validity)
  - Context assembly (minimal package)
- Redis caching layer
- Retrieval mode routing (report / knowledge / decision / entity-centric)

**Success Criteria:**
- Agents retrieve only what they need for a given query
- Retrieval is fast, explainable, and evidence-based
- System remains fully usable with 10,000+ reports
- Same query returns the same result (deterministic with cache)

---

## Phase 5 — Developer Experience (Month 9–12)

**Goal:** Make North Star easy to adopt, extend, and integrate into any agent system.

**Deliverables:**
- Public REST API:
  - `POST /scribe/process`
  - `POST /archivist/process`
  - `GET /retrieve`
  - `GET /reports/{id}`, `GET /knowledge/{id}`, `GET /decisions/{id}`
  - `GET /entities/{id}`
- SDK (Python first, TypeScript second):
  - `northstar.ingest(conversation)` → report + candidates
  - `northstar.retrieve(query)` → minimal context package
  - `northstar.report(id)` → full report
- Templates:
  - New specialist agent stub
  - New entity type
  - New decision workflow
- Documentation site (generated from `/docs`)
- `CONTRIBUTING.md` — how to swap components and extend schemas

**Success Criteria:**
- A developer can integrate North Star into their agent system in under 2 hours
- External systems can push reports and retrieve knowledge via the public API
- Users can swap Postgres for SQLite or pgvector for Qdrant with minimal friction

---

## Phase 6 — Scaling & Governance (Month 12–24)

**Goal:** Prepare North Star for large organizations, multi-agent systems, and compliance requirements.

**Deliverables:**
- Multi-agent orchestration — agents retrieve from the same store without context collision
- Human-in-the-loop workflows:
  - `human_review_queue` UI or webhook
  - Approval flows for high-impact knowledge changes
  - Contradiction resolution interface
- Memory governance:
  - Configurable retention policies (archive knowledge older than N months)
  - Audit trails (all deprecations, merges, and contradiction flags logged)
  - Knowledge versioning (full history of status transitions)
- Graph hygiene automation:
  - Periodic high-degree node detection
  - Orphan knowledge cleanup
  - Stale decision flagging
- Optional advanced integrations:
  - Neo4j sync (for organizations needing advanced graph reasoning)
  - Qdrant / Weaviate (for high-volume semantic search)
  - Kafka / Pub-Sub (for event streaming at scale)
- Performance benchmarks at 10k, 100k, 1M reports

**Success Criteria:**
- North Star supports hundreds of concurrent agents
- Memory remains coherent and curated at scale
- Decisions are fully traceable even years after they were made
- Humans remain in control of what becomes institutional knowledge

---

## Long-Term Vision (Beyond 24 Months)

North Star becomes:
- A **standard architecture** for AI organizations — the way microservices became a standard for web backends
- A **reference implementation** for curated memory that others fork and adapt
- A **platform** for multi-agent collaboration without shared context pollution
- A **knowledge engine** that organizations trust to grow wiser over time

The long-term goal is simple:

> **Build AI organizations that scale in wisdom, not in context.**

---

## Guiding Principles for All Phases

1. **Each phase must be independently useful** — don't build for the future at the cost of the present
2. **Never add complexity without a clear problem it solves**
3. **Human readability is never sacrificed for performance**
4. **Contradictions are modeled, never hidden**
5. **Provenance is non-negotiable at every phase**
