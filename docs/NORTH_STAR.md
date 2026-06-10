# North Star

*The Manifesto*

## Problem Statement

AI systems are accumulating context the way organizations accumulate paper: indiscriminately, endlessly, without curation.

The result:
- **Memory bloat** — context windows fill with noise
- **Fragmented knowledge** — facts scattered across conversations, never organized
- **Untraceable decisions** — "we decided that" with no record of why
- **Context overload** — agents spend more cycles remembering than reasoning
- **Loss of trust** — humans can't audit what the system knows or why it acted

The more a system learns, the slower and less reliable it becomes. This is the wrong direction.

## The North Star Principle

> **Keep active context small.**
> **Store knowledge externally.**
> **Retrieve only what is needed.**

This principle applies everywhere: code, documentation, memory, reports, agents, organizations.

Complexity should be *organized*, not *loaded*.

## The Model: Organizations Remember. Agents Do Not.

Human organizations don't function because every employee remembers every conversation. They function because they create:
- Reports
- Procedures
- Decisions
- Archives
- Institutional knowledge

The organization remembers even when individuals do not.

North Star applies this principle to AI systems. An agent's job is to *act*. The system's job is to *remember*.

## Core Distinctions

| Concept | Definition | Lifecycle |
|---------|-----------|-----------|
| **Conversation** | Ephemeral exchange | Discarded after report creation |
| **Report** | Durable work artifact | Immutable, permanent evidence |
| **Knowledge** | Atomic validated fact extracted from reports | Curated, versioned, depreciable |
| **Decision** | Explicit organizational choice | Traceable to knowledge, owned by a human or agent |

Reports are **evidence**, not memory.
Knowledge is **extracted**, not stored raw.
Decisions are **separate** from knowledge.

## Success Criteria

North Star succeeds when:

1. A system with 10,000 reports is as usable as a system with 10
2. Every decision can be traced back to the evidence that justified it
3. Contradictions are visible, not silently overwritten
4. Active agent context stays small regardless of organizational history
5. A human can read any report, knowledge item, or decision without a model to interpret it
6. Growth increases capability, not complexity

## What North Star Is Not

- Not a chatbot
- Not a RAG system
- Not a vector database
- Not an agent framework

North Star is an **architecture** for AI organizations — a blueprint for how agents, memory, and knowledge should be structured so a system grows wiser over time without growing heavier.

## Related Documents

- [PHILOSOPHY.md](PHILOSOPHY.md) — the intellectual backbone
- [ARCHITECTURE.md](ARCHITECTURE.md) — how the system works end-to-end
- [MEMORY_MODEL.md](MEMORY_MODEL.md) — the ontology
- [ROADMAP.md](ROADMAP.md) — how we get there
