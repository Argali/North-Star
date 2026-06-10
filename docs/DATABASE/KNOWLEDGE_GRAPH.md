# Knowledge Graph Specification

*How North Star connects reports, knowledge, decisions, and entities.*

## 1. Purpose

The North Star Knowledge Graph provides structure, traceability, and reasoning capability across the four core entity types.

It is **not** a full graph database like Neo4j. It is a **lightweight graph layer implemented in Postgres**, designed to:
- support provenance tracing
- detect contradictions
- trace decisions back to evidence
- enable multi-hop reasoning
- remain human-readable
- avoid memory bloat

See [DATABASE/SCHEMA.md](SCHEMA.md) for the `relationships` table DDL.

## 2. Conceptual Model

```
Report ──supports──► Knowledge ──informs──► Decision
                         │                      │
                    contradicts            relates_to
                         │                      │
                    Knowledge              Entity
                                               │
                                          relates_to
                                               │
                                          Knowledge / Decision
```

Four node types. Four edge types. No ad-hoc additions.

## 3. Node Types

### Report
- Represents durable evidence
- The **origin** of all knowledge and decisions
- Identified by: `id`, `title`, `tags`, `created_at`

### Knowledge
- Represents an atomic, validated fact
- **Reusable** across multiple decisions and future tasks
- Status lifecycle: `proposed → validated → deprecated / superseded`

### Decision
- Represents an explicit organizational choice
- An **action**, not a fact
- Status lifecycle: `planned → executed / reverted / needs_reassessment`

### Entity
- Represents a domain object: vehicle, system, supplier, project, customer
- Enables **cross-report linking** ("Vehicle 259" across many reports)

## 4. Edge Types

### `supports`
A report provides evidence for a knowledge item.

```
Report A ──supports──► Knowledge B
```

- Created by the Scribe when a report yields a knowledge candidate
- Validated by the Archivist before insertion
- Used for: provenance tracing, trust scoring

### `informs`
A knowledge item supports a decision.

```
Knowledge B ──informs──► Decision C
```

- Created when a decision is linked to knowledge
- Used for: decision traceability, impact tracing

### `contradicts`
Two knowledge items conflict.

```
Knowledge X ◄──contradicts──► Knowledge Y
```

- Created by the Archivist when a contradiction is detected
- Both items remain in the graph — neither is silently deleted
- Used for: contradiction visibility, human review routing

### `relates_to`
A knowledge item or decision references a domain entity.

```
Knowledge B ──relates_to──► Entity D
Decision C  ──relates_to──► Entity D
```

- Used for: entity-centric retrieval ("show me everything about Vehicle 259")

## 5. Graph Construction Pipeline

The graph is built collaboratively:

**Scribe** proposes:
- `supports` edges (report → knowledge)
- `informs` edges (knowledge → decision)
- `relates_to` edges (knowledge/decision → entity)
- Potential `contradicts` flags

**Archivist** validates and finalizes:
- Validates nodes exist before inserting edges
- Resolves contradictions (temporal vs. genuine)
- Inserts `contradicts` edges for confirmed conflicts
- Enforces graph hygiene rules

## 6. Graph Hygiene Rules

| Rule | Purpose |
|------|---------|
| No edge without both nodes existing | Prevents orphan edges |
| No orphan knowledge (must link to ≥1 report) | Every fact is auditable |
| No decision without linked knowledge | Every action is justified |
| No circular relationships | Prevents infinite traversal loops |
| No ad-hoc edge types | Strict vocabulary keeps the graph navigable |
| Contradictions are modeled, not overwritten | History is preserved |
| High-degree nodes are aggregated | Prevents spaghetti graph growth |

### High-Degree Node Aggregation

When a node accumulates excessive connections (configurable threshold, default: 50):
- Create an intermediate **topic node** (e.g., "TargetCross Odometer Reliability")
- Route connections through the topic node
- This preserves the structure without creating unmanageable hubs

## 7. Query Patterns

### Trace a decision back to source evidence

```sql
-- Decision → Knowledge → Reports
SELECT DISTINCT r.*
FROM decisions d
JOIN knowledge k ON k.id = ANY(d.linked_knowledge_ids)
JOIN reports r ON r.id = ANY(k.source_report_ids)
WHERE d.id = $1;
```

### Find all knowledge supporting a report

```sql
-- Report → Knowledge (via supports)
SELECT k.*
FROM relationships rel
JOIN knowledge k ON k.id = rel.to_id
WHERE rel.from_id = $1
  AND rel.type = 'supports';
```

### Detect contradictions for a knowledge item

```sql
-- Knowledge ↔ contradicts ↔ Knowledge
SELECT *
FROM relationships
WHERE type = 'contradicts'
  AND (from_id = $1 OR to_id = $1);
```

### Entity-centric retrieval

```sql
-- All knowledge related to an entity
SELECT k.*
FROM relationships rel
JOIN knowledge k ON k.id = rel.from_id
WHERE rel.to_id = $1
  AND rel.type = 'relates_to'
  AND k.status = 'validated';

-- All decisions related to an entity
SELECT d.*
FROM relationships rel
JOIN decisions d ON d.id = rel.from_id
WHERE rel.to_id = $1
  AND rel.type = 'relates_to';
```

### Multi-hop: decisions influenced by an entity

```sql
-- "Show me all decisions influenced by reports about Vehicle 259"
-- Entity → Knowledge → Decisions
SELECT DISTINCT d.*
FROM entities e
JOIN relationships r1 ON r1.to_id = e.id AND r1.type = 'relates_to'
JOIN knowledge k ON k.id = r1.from_id AND k.status = 'validated'
JOIN decisions d ON k.id = ANY(d.linked_knowledge_ids)
WHERE e.id = $1;
```

## 8. Multi-Hop Reasoning

The graph enables chained reasoning without loading full histories.

**Example 1: Impact analysis**
"What decisions might be affected if TargetCross odometer data is confirmed unreliable?"
```
Entity (TargetCross) → Knowledge → Decisions (via informs)
```

**Example 2: Audit trail**
"Why was Vehicle 259 sold?"
```
Decision (sell Vehicle 259) → Knowledge (cost threshold) → Report (Q2 Fleet Review)
```

**Example 3: Contradiction context**
"What's the current status on Server infrastructure?"
```
Knowledge (AWS) ↔ contradicts ↔ Knowledge (Azure) → both visible, human review pending
```

## 9. Why Not Neo4j?

North Star uses Postgres for the graph layer intentionally:

| Consideration | Postgres Graph | Neo4j |
|--------------|---------------|-------|
| Operational complexity | Low | Higher (2 databases) |
| Query capability | Sufficient for North Star patterns | More powerful for complex traversal |
| Human readability | SQL is auditable | Cypher is less familiar |
| Deployment | Same DB as reports/knowledge/decisions | Separate service + sync pipeline |
| Philosophy fit | Keeps context small | Adds infrastructure weight |

**When to upgrade to Neo4j:**
- Multi-hop traversal exceeds 4–5 hops regularly
- Contradiction resolution requires complex graph algorithms
- Advanced relationship inference is needed

Users who need Neo4j can sync the `relationships` table via CDC. The Postgres layer remains the source of truth.

## 10. Visualization Principles

Never show the raw graph to users. It will look impressive and be functionally useless at scale.

Instead, show **curated paths**:
- "This decision was based on these facts."
- "These facts came from these reports."
- "This entity appears in these decisions."

Linear provenance chains are what humans can actually audit and trust.

## 11. Summary

The North Star Knowledge Graph is:
- **Simple** — four node types, four edge types
- **Durable** — stored in Postgres alongside all other data
- **Evidence-based** — every node traces back to a report
- **Human-readable** — SQL queries, not proprietary graph language
- **Optimized for provenance and traceability** — not for general graph analytics

It is not a general-purpose graph database. It is a **memory architecture** designed to keep AI organizations clear, coherent, and trustworthy.
