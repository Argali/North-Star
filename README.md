# North Star

**An architecture for AI organizations that accumulate knowledge without accumulating context.**

North Star defines how a multi-agent system stores, curates, and retrieves institutional memory — so it grows wiser over time, not heavier.

## The Core Principle

> Keep active context small. Store knowledge externally. Retrieve only what is needed.

## Why North Star Exists

Most AI memory systems treat everything as memory: conversations, conclusions, logs, decisions, preferences. This leads to memory bloat, fragmented knowledge, untraceable decisions, and context overload.

North Star rejects that approach. It draws from how human organizations actually work — not by making individuals remember everything, but by creating reports, procedures, decisions, and archives. The organization remembers. Agents do not.

## How It Works

Raw activity → **Scribe** → Report → **Archivist** → Validated Knowledge → Retrieval

Two core agents:
- **Scribe** — transforms raw conversations and tasks into structured Reports, Knowledge candidates, and Decision candidates
- **Archivist** — validates, deduplicates, resolves contradictions, and maintains long-term memory quality

One reference stack:
- **Postgres** — structured reports, knowledge, decisions
- **pgvector** — semantic discovery
- **Redis** — agent state, queues, ephemeral memory

## Documentation

```
docs/
├── NORTH_STAR.md          # Manifesto & purpose
├── PHILOSOPHY.md          # Core beliefs & principles
├── ARCHITECTURE.md        # System overview & data flows
├── MEMORY_MODEL.md        # Ontology: reports, knowledge, decisions
├── RETRIEVAL.md           # Hybrid retrieval architecture
├── ROADMAP.md             # 24-month phased plan
├── AGENTS/
│   ├── SCRIBE.md          # Scribe agent specification
│   └── ARCHIVIST.md       # Archivist agent specification
├── DATABASE/
│   ├── SCHEMA.md          # Postgres + pgvector DDL
│   └── KNOWLEDGE_GRAPH.md # Graph nodes, edges, query patterns
└── IMPLEMENTATIONS/
    └── POSTGRES_PGVECTOR_REDIS.md  # Official reference implementation
```

## Reference Stack

| Component | Role |
|-----------|------|
| Postgres | Structured memory (reports, knowledge, decisions) |
| pgvector | Semantic search & discovery |
| Redis | Queues, agent state, caching |

North Star is **storage-agnostic**. The reference implementation uses Postgres + pgvector + Redis, but any system implementing the same interfaces is compatible.

## License

MIT — use it, fork it, build on it.
