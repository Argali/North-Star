# Memory Model

*The ontology: what North Star stores, and why.*

## 1. Purpose

This document defines the **canonical entities** of North Star's memory architecture — what each concept means, how it is structured, and the rules that govern its lifecycle.

## 2. Entity Overview

North Star has four canonical memory types, ordered by durability and abstraction:

| Entity | Durability | Abstraction | Created By |
|--------|-----------|-------------|-----------|
| **Conversation / Log** | Ephemeral | Raw | Any agent |
| **Report** | Permanent | Structured narrative | Scribe |
| **Knowledge** | Long-term (curated) | Atomic fact | Scribe → Archivist |
| **Decision** | Long-term (curated) | Explicit choice | Scribe → Archivist |

Plus two supporting types:
- **Entity** — domain objects referenced across reports/knowledge/decisions
- **Relationship** — edges in the lightweight knowledge graph

## 3. Conversation / Log

### What It Is
The raw ephemeral stream of activity: chat messages, task outputs, intermediate agent reasoning.

### Key Property
**Temporary.** A conversation is not memory. It is the raw material from which memory is built.

Once the Scribe processes a conversation into a Report, the conversation's job is done. It may be archived for audit purposes, but it does not enter the active knowledge store.

### Rule
> No conversation becomes knowledge directly. It must pass through a Report first.

## 4. Report

### What It Is
A durable, structured, human-readable artifact summarizing a completed piece of work or analysis.

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `title` | text | Human-readable title |
| `created_at` | timestamptz | Creation timestamp |
| `author` | text | Agent or human who produced it |
| `context_summary` | text | What was the situation |
| `analysis` | text | What was examined and how |
| `conclusions` | text | What was found |
| `raw_source` | jsonb | Optional: original logs/transcript |
| `tags` | text[] | Topic classification |

### Key Properties
- **Immutable** after creation — reports are evidence, not editable drafts
- **Human-readable** — no model required to understand content
- **Provenance root** — every knowledge item must trace back to at least one report

### Rules
> Reports are created by the Scribe. They are never modified after creation.
> Every knowledge item must link to at least one source report.

## 5. Knowledge

### What It Is
An atomic, declarative, validated fact extracted from one or more reports.

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `statement` | text | A single declarative fact |
| `confidence` | float | 0–1 score |
| `status` | text | `proposed` / `validated` / `deprecated` / `superseded` |
| `source_report_ids` | UUID[] | Provenance — which reports support this |
| `source_section` | text | Optional: paragraph-level pointer within report |
| `valid_from` | timestamptz | When this became true |
| `valid_until` | timestamptz | When this stopped being true (optional) |
| `topics` | text[] | Classification tags |

### Extraction Rules

Knowledge must be:
- **Atomic** — one fact per statement, no compound claims
- **Declarative** — "X is true", not "X seems to be the case"
- **Evidence-based** — must link to at least one source report
- **Non-speculative** — uncertainty belongs in a dedicated field, not in the statement itself
- **Scoped** — include conditions or scope when a fact doesn't apply universally

**Good examples:**
- ✅ "TargetCross lacks reliable odometer data."
- ✅ "Vehicle 259 exceeded maintenance cost threshold in Q2 2026."

**Bad examples:**
- ❌ "TargetCross seems unreliable." *(speculative)*
- ❌ "Vehicle 259 is probably failing." *(speculative)*
- ❌ "Vehicle 259 has high costs and reliability issues." *(compound)*

### Status Lifecycle

```
proposed → validated → deprecated
                    ↘ superseded
```

- `proposed` — created by Scribe, awaiting Archivist validation
- `validated` — accepted by Archivist, active in retrieval
- `deprecated` — no longer true, kept for audit trail
- `superseded` — replaced by a newer, more accurate knowledge item

### Rules
> Knowledge is never deleted. Status changes preserve history.
> Only `validated` knowledge is included in retrieval by default.
> When knowledge is deprecated or superseded, all decisions that referenced it are flagged `needs_reassessment`.

## 6. Decision

### What It Is
An explicit organizational choice — an action taken or committed to — traceable to validated knowledge.

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `statement` | text | The decision text |
| `rationale` | text | Why this decision was made |
| `linked_knowledge_ids` | UUID[] | Which knowledge items support this decision |
| `owner` | text | Human or agent responsible |
| `timestamp` | timestamptz | When the decision was made |
| `status` | text | `planned` / `executed` / `reverted` / `needs_reassessment` |

### Rules

Decisions must:
- Reference at least one `validated` knowledge item
- Include a rationale
- Have a clear owner
- Not silently contradict existing decisions

**Good examples:**
- ✅ "Vehicle 259 will be sold." *(actionable, explicit, ownable)*
- ✅ "TargetCross integration is postponed pending data quality resolution."

**Bad examples:**
- ❌ "We should probably look into the vehicle situation." *(not a decision)*

### Status Lifecycle

```
planned → executed
       ↘ reverted
         needs_reassessment  (triggered when supporting knowledge changes)
```

### Rules
> A decision without a rationale or linked knowledge is rejected by the Archivist.
> When a linked knowledge item is deprecated or superseded, the decision is marked `needs_reassessment`.

## 7. Entity

### What It Is
A domain object referenced across reports, knowledge, and decisions — vehicles, systems, suppliers, customers, projects.

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `name` | text | Entity name |
| `type` | text | e.g., `vehicle`, `system`, `supplier` |
| `metadata` | jsonb | Arbitrary domain data |

### Purpose
Entities enable cross-report linking. Without them, "Vehicle 259" in one report has no traceable connection to "Vehicle 259" in another.

## 8. Relationship

### What It Is
An edge in the lightweight knowledge graph. Stored in Postgres — no separate graph database required.

### Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `from_id` | UUID | Source node |
| `to_id` | UUID | Target node |
| `type` | text | `supports` / `informs` / `contradicts` / `relates_to` |
| `created_at` | timestamptz | Timestamp |

### Edge Types

| Type | Meaning | Example |
|------|---------|---------|
| `supports` | Report provides evidence for knowledge | Report A → supports → Knowledge B |
| `informs` | Knowledge supports a decision | Knowledge B → informs → Decision C |
| `contradicts` | Two knowledge items conflict | Knowledge X ↔ contradicts ↔ Knowledge Y |
| `relates_to` | Knowledge or decision references an entity | Knowledge B → relates_to → Vehicle 259 |

### Rules
> No edge without provenance.
> No orphan knowledge — every knowledge item must link to at least one report.
> No decision without linked knowledge.
> Contradictions are never silently resolved — they are modeled explicitly.

## 9. Curation Principles

1. **Not everything is worth remembering.** The Archivist rejects low-value candidates.
2. **History is never deleted.** Deprecated knowledge is retained with `status = deprecated`.
3. **Provenance is non-negotiable.** Every fact must trace to a report.
4. **Contradictions are explicit.** They are modeled as relationships, not silently overwritten.
5. **Decisions depend on knowledge.** Break the dependency and the decision is flagged.

## 10. Knowledge Item Format (File-Based)

For lightweight implementations using markdown files instead of a database, knowledge items use frontmatter:

```markdown
---
id: K-259
type: knowledge
status: validated
topics: [fleet-maintenance, cost-analysis]
source: /docs/REPORTS/2026_Q2_Fleet_Review.md#section-3
valid_from: 2026-06-10
contradicts: [K-112]
---

# Knowledge

Maintenance costs for Vehicle 259 exceed the critical threshold ($15k/yr).

### Scope / Conditions
Applies only to diesel-engine models active for more than 5 years.

### Uncertainties
Does not factor in potential salvage value of parts.
```

This format preserves paragraph-level provenance and allows static analysis without a database.
