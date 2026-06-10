# Archivist Agent Specification

*The guardian of North Star's long-term memory.*

## 1. Purpose

Where the Scribe *creates* structure, the Archivist *protects* it.

The Archivist ensures that:
- knowledge is **validated**, not blindly stored
- decisions are **traceable** and supported by evidence
- contradictions are **detected and resolved** — never silently overwritten
- memory remains **small, coherent, and high-quality**
- reports → knowledge → decisions form a **clean chain of provenance**

The Archivist is the **editor**, **librarian**, and **quality gate** of the system.

## 2. Core Functions

1. **Validation** — accept or reject knowledge and decision candidates
2. **Deduplication** — merge overlapping or equivalent knowledge
3. **Contradiction resolution** — classify, flag, and route conflicts
4. **Staleness detection** — deprecate knowledge that no longer applies
5. **Relationship management** — maintain the knowledge graph
6. **Memory pruning** — enforce retention policies

The Archivist is the **final authority** on what enters long-term memory.

## 3. Inputs

The Archivist receives candidates from the Scribe via Redis:

```
archivist_queue contents:
- knowledge_candidates[]
- decision_candidates[]
- relationship_candidates[]
- contradiction_flags[]
- source_report_id
```

## 4. Outputs

| Output | Destination |
|--------|------------|
| Validated knowledge | `knowledge` table (Postgres) |
| Validated decisions | `decisions` table (Postgres) |
| Curated relationships | `relationships` table (Postgres) |
| Contradiction records | `relationships` table (type = `contradicts`) |
| Deprecation events | `knowledge.status = deprecated` |
| Human review requests | `human_review_queue` (Redis) |

## 5. Validation Rules

### 5.1 Knowledge Validation

Accept a knowledge candidate only if it meets ALL of the following:

| Criterion | Check |
|-----------|-------|
| **Supported** | Links to at least one existing report |
| **Non-speculative** | Statement is declarative, not hedged |
| **Atomic** | One fact only, no compound claims |
| **Non-contradictory** | Does not conflict with validated knowledge (unless flagged) |
| **Not a duplicate** | Semantic similarity below merge threshold |
| **Not stale** | Applies to current context |

The Archivist checks:
- Semantic similarity via pgvector (duplicate/near-duplicate detection)
- Keyword overlap for exact match detection
- Conflicting statement logic
- Temporal validity (`valid_from` / `valid_until`)
- Provenance completeness

### 5.2 Decision Validation

Accept a decision candidate only if:
- References at least one `validated` knowledge item
- Includes a non-empty rationale
- Has a clear owner (human or agent)
- Does not contradict existing `executed` decisions without explicit supersession

## 6. Deduplication Logic

The Archivist merges knowledge items when:
- Semantic similarity exceeds the configured threshold (default: 0.92)
- Statements differ only in wording, not meaning
- Provenance overlaps (same source reports)
- No contradiction exists between them

**Merge outcome:**
- A single canonical knowledge item is kept
- `source_report_ids` is merged from both items
- The lower-confidence item is deprecated with a reference to the merged item

**Example:**
- "Vehicle 259 exceeded maintenance cost thresholds."
- "Maintenance costs for vehicle 259 are above operational limits."
→ Merged into one canonical fact with combined provenance.

## 7. Contradiction Handling

### 7.1 Detection

The Archivist compares new candidates against:
- Existing validated knowledge
- Existing decisions
- Existing relationships

### 7.2 Classification

| Type | Definition | Example |
|------|-----------|---------|
| **Direct** | Logically opposite statements | "Server is AWS" vs "Server is Azure" |
| **Temporal** | True at different times | "Server is down" (then) vs "Server is up" (now) |
| **Contextual** | True under different conditions | "X is reliable" vs "X is unreliable under load Y" |

### 7.3 Resolution State Machine

```
New knowledge arrives
    │
    ├── No conflict → validate and store
    │
    └── Conflict detected
            │
            ├── Temporal evolution?
            │       → Mark old as superseded (valid_until = now)
            │       → Store new as validated
            │
            ├── Genuine conflict?
            │       → Mark both: type = contradicts
            │       → Push to human_review_queue
            │       → Neither is promoted to validated until resolved
            │
            └── High-impact domain (safety/compliance/finance)?
                    → Always push to human_review_queue
                    → No automatic resolution permitted
```

### 7.4 Timeline Reasoning Guidance

The Archivist must distinguish evolution from conflict. Key signals:

- **Evolution:** new report is more recent AND covers the same context (temporal supersession)
- **Conflict:** reports are roughly contemporaneous AND describe the same condition differently

When ambiguous: **route to human review**.

### 7.5 Decision Impact Tracing

When knowledge is deprecated or superseded:
1. Find all decisions with `linked_knowledge_ids` containing the affected item
2. Set their `status = needs_reassessment`
3. Log which knowledge change triggered the flag

This turns North Star from a passive archive into an **active risk management system**.

## 8. Staleness Detection

Knowledge becomes stale when:
- Superseded by newer validated knowledge on the same subject
- Contradicted by multiple independent reports
- Unused in retrieval for an extended period (configurable)
- Tied to an entity that no longer exists (e.g., a sold vehicle)

**Action:**
```sql
UPDATE knowledge
SET status = 'deprecated', valid_until = NOW()
WHERE id = $1;
```

**Important:** Stale knowledge is **never deleted**. It is retained with `status = deprecated` for audit and historical reasoning.

## 9. Relationship Management

The Archivist maintains the lightweight graph in Postgres:

| Edge Type | Validated When |
|-----------|---------------|
| `supports` | Report is real, knowledge is validated |
| `informs` | Both knowledge and decision are validated |
| `contradicts` | Conflict is confirmed, both items flagged |
| `relates_to` | Entity exists in the `entities` table |

**Graph hygiene rules enforced by the Archivist:**
- No edge without provenance
- No orphan knowledge (must link to ≥1 report)
- No decision without linked knowledge
- No circular relationships
- Degree limits: nodes with excessive connections are aggregated via intermediate topic nodes

## 10. Pipeline

```
Receive candidates from archivist_queue (Redis)
    │
    ▼
Load relevant existing memory (knowledge, decisions, relationships)
    │
    ▼
Validate each knowledge candidate
    │
    ├── Duplicate? → Merge
    ├── Contradiction? → Classify (temporal / direct / contextual)
    │       ├── Temporal → supersede old, store new
    │       └── Direct / ambiguous → flag, route to human_review_queue
    └── Clean? → Validate, store
    │
    ▼
Validate each decision candidate
    │
    ├── Missing knowledge link? → Reject
    ├── Missing rationale? → Reject
    └── Clean? → Validate, store
    │
    ▼
Validate and insert relationships
    │
    ▼
Run staleness scan on affected knowledge items
    │
    ▼
Flag decisions needing reassessment
    │
    ▼
Emit human review tasks if needed
```

## 11. Interfaces

### Input

```
POST /archivist/process
{
  "knowledge_candidates": [...],
  "decision_candidates": [...],
  "relationship_candidates": [...],
  "source_report_id": "UUID"
}
```

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

### Redis Queues

| Queue | Purpose |
|-------|---------|
| `archivist_queue` | Incoming candidates from Scribe |
| `human_review_queue` | Items requiring human decision |

## 12. Quality Requirements

| Requirement | Description |
|-------------|-------------|
| **No hallucinated facts** | Reject anything without report provenance |
| **No unsupported decisions** | Reject decisions without validated knowledge links |
| **No silent contradictions** | Every conflict is modeled explicitly |
| **No memory bloat** | Duplicates are merged, stale items deprecated |
| **No unresolved stale items** | Periodic staleness scans are mandatory |
| **Audit trail** | Every deprecation, merge, and contradiction is logged |

## 13. Failure Modes

| Failure | Action |
|---------|--------|
| Ambiguous knowledge | Flag `needs_review`, push to human queue |
| Conflicting evidence with equal weight | Push to human queue, neither promoted |
| Missing provenance | Reject candidate, log reason |
| Malformed candidate | Reject, log, push report ID to review queue |
| Circular relationship detected | Reject edge, log |
| Duplicate decision | Merge if identical, flag if contradictory |

## 14. Non-Goals

The Archivist does **not**:
- Generate or summarize reports
- Extract knowledge from raw text
- Perform semantic search for users
- Execute tasks or interact with users
- Make domain-level decisions (e.g., "should we sell this vehicle")

These belong to the Scribe, retrieval layer, or specialist agents.

## 15. Summary

The Archivist is the guardian of institutional memory. It ensures that:
- knowledge is **true**
- decisions are **traceable**
- contradictions are **visible**
- memory stays **small and high-quality**
- the system grows **wiser**, not heavier

**The Scribe creates structure. The Archivist protects quality. Together, they form the foundation of North Star.**
