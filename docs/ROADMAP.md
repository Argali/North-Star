# North Star — Roadmap

## Product Strategy

North Star ships in two tiers:

**North Star** (this repository, MIT license)
Targets solo developers, freelancers, and small teams. Full-featured memory
architecture. Free forever. The reference implementation the community builds on.

**North Star Enterprise** (future, commercial license)
Targets organizations running multiple agents, multiple teams, and compliance
requirements. Built on top of North Star with additional governance, auth,
audit, and scale layers. Revenue funds continued development of the open-source core.

The open-source version is never crippled to sell the Enterprise version.
Enterprise features are genuinely different problems (multi-tenancy, audit
compliance, SSO) that most solo users do not need.

---

## Phase 1 — Architecture & Documentation (Month 1–2) ✅

**Goal:** Define the memory model and architecture before writing code.

**Deliverables:**
- `docs/NORTH_STAR.md` — core principles and North Star rule
- `docs/PHILOSOPHY.md` — why curated memory beats raw context
- `docs/MEMORY_MODEL.md` — 6 entity types and their lifecycle
- `docs/ARCHITECTURE.md` — system design and component overview
- `docs/RETRIEVAL.md` — hybrid retrieval specification
- `docs/AGENTS/SCRIBE.md` — Scribe agent full specification
- `docs/AGENTS/ARCHIVIST.md` — Archivist agent full specification
- `docs/DATABASE/SCHEMA.md` — full schema with lifecycle states
- `docs/IMPLEMENTATIONS/POSTGRES_PGVECTOR_REDIS.md` — reference stack

---

## Phase 2 — Reference Stack (Month 3–4) ✅

**Goal:** Build the Python reference implementation.

**Deliverables:**
- Postgres + pgvector schema (Alembic migrations)
- asyncpg DB client with connection pooling
- Redis queue system (LPUSH/BRPOP pattern)
- OpenAI and local embedding providers
- FastAPI REST API (all CRUD endpoints)
- Docker Compose stack

---

## Phase 3 — Agents (Month 5–6) ✅

**Goal:** Implement Scribe and Archivist as production-grade async agents.

**Deliverables:**
- Scribe pipeline: normalize → report → embed → extract → contradiction check → queue
- Archivist pipeline: validate → deduplicate → contradiction state machine → decisions → relationships → impact tracing
- Queue workers with graceful shutdown and circuit breakers
- Anthropic tool_use for deterministic JSON extraction

---

## Phase 4 — Hybrid Retrieval (Month 6–7) ✅

**Goal:** Implement α·semantic + β·keyword + γ·recency retrieval with graph traversal.

**Deliverables:**
- Migration 002: GIN FTS index on knowledge
- `src/retrieval/scorer.py` — hybrid ranking with pgvector + ts_rank + recency decay
- `src/retrieval/graph.py` — BFS graph traversal (depth-limited, contradiction surfacing)
- `/retrieve` endpoint fully implemented (replaces Phase 4 stub)

---

## Phase 5 — Developer Experience (Month 7–8) ✅

**Goal:** Make North Star easy to adopt and integrate.

**Deliverables:**
- Python SDK (`sdk/python/`) — async + sync NorthStarClient
- TypeScript SDK (`sdk/typescript/`) — fetch-based, zero dependencies
- Templates: new agent stub, new entity type, new decision workflow
- `CONTRIBUTING.md` — component swap guides (SQLite, Qdrant, local embeddings)
- MkDocs documentation site

---

## Phase 6 — Operational Maturity (Month 8–10) ✅

**Goal:** Make North Star deployable and maintainable by a solo developer.

**Deliverables:**
- Human review API (`GET /human-review`, `POST /human-review/{id}/resolve`)
- Staleness scan (`POST /scan`, configurable age threshold)
- Embedding backfill script (`python -m src.tools.backfill`)
- CLI (`northstar review`, `northstar scan`, `northstar stats`)
- API locked at v1.0.0

**Success Criteria:**
- A solo developer can deploy, use, and maintain North Star with no ops burden
- Contradictions and flagged items surface and are actionable
- Switching embedding models does not require manual DB surgery

---

## North Star Enterprise — Phase 6 Strong (Future, commercial)

**Goal:** Support organizations with multiple agents, teams, and compliance requirements.

**Planned deliverables:**
- Multi-tenant isolation (team/org scoping on all tables)
- Role-based approval workflows (route contradictions to the right person, not just a queue)
- Immutable audit log (every status transition logged: who, when, why)
- Scheduled graph hygiene (orphan cleanup, high-degree node detection, automated stale flagging)
- Auth layer (API keys per team, JWT, SSO)
- Performance at scale (100k+ reports, connection pooling tuning, index optimization)
- Optional integrations: Kafka event streaming, Neo4j graph sync, Qdrant/Weaviate

**Design principle:** Enterprise features solve genuinely different problems.
The open-source core is never restricted to push Enterprise upgrades.

---

## Long-Term Vision

North Star becomes:
- A **standard architecture** for AI organizations — the way microservices became a standard for web backends
- A **reference implementation** for curated memory that others fork and adapt
- A **knowledge engine** that organizations trust to grow wiser over time

> **Build AI organizations that scale in wisdom, not in context.**

---

## Guiding Principles

1. Each phase must be independently useful — don't build for the future at the cost of the present
2. Never add complexity without a clear problem it solves
3. Human readability is never sacrificed for performance
4. The open-source version is never crippled to sell the Enterprise version
