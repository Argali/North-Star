# Scribe Agent Specification

*The front door of North Star's institutional memory.*

## 1. Purpose

The Scribe transforms raw activity into structured, durable organizational artifacts.

It converts:
- conversations
- task logs
- agent outputs
- human instructions
- external documents

into:
- **Reports** — durable, human-readable artifacts
- **Knowledge candidates** — atomic facts awaiting validation
- **Decision candidates** — explicit choices awaiting validation
- **Relationship candidates** — graph edges awaiting validation

The Scribe is the **bridge** between ephemeral activity and durable institutional memory.

## 2. Core Functions

The Scribe performs six functions — in this order:

1. **Summarization** — condense raw input into human-readable narrative
2. **Topic classification** — tag content by domain and subject
3. **Knowledge extraction** — identify atomic, declarative facts
4. **Decision extraction** — identify explicit organizational choices
5. **Relationship discovery** — identify links between nodes
6. **Contradiction detection** — flag conflicts with existing knowledge

**The Scribe does not curate memory.** That is the Archivist's role.

## 3. Inputs

The Scribe accepts:
- Conversation transcripts
- Agent task logs
- Human instructions or meeting notes
- Intermediate agent outputs
- External documents (optional)

All inputs are treated as **evidence**, not memory.

## 4. Outputs

### 4.1 Report

A durable, immutable artifact with:

```json
{
  "id": "UUID",
  "title": "string",
  "created_at": "timestamptz",
  "author": "string",
  "context_summary": "string",
  "analysis": "string",
  "conclusions": "string",
  "raw_source": {},
  "tags": ["string"]
}
```

### 4.2 Knowledge Candidates

Each candidate uses a strict extraction template:

```json
{
  "statement": "Declarative fact — one per item",
  "confidence": 0.0–1.0,
  "scope_conditions": "When/where this applies",
  "uncertainties": "What this doesn't account for",
  "source_excerpt": "Verbatim or near-verbatim source text",
  "source_section": "Section/paragraph reference in the report",
  "topics": ["tag1", "tag2"]
}
```

### 4.3 Decision Candidates

```json
{
  "statement": "What was decided",
  "rationale": "Why",
  "linked_knowledge_refs": ["K-001", "K-002"],
  "owner": "agent or human name",
  "status": "planned | executed"
}
```

### 4.4 Relationship Candidates

```json
{
  "from_id": "UUID",
  "to_id": "UUID",
  "type": "supports | informs | contradicts | relates_to"
}
```

## 5. Knowledge Extraction Rules

Knowledge must be:
- **Atomic** — one fact per statement
- **Declarative** — "X is true", not "X might be"
- **Evidence-based** — must reference a source excerpt
- **Non-speculative** — uncertainty belongs in `uncertainties`, not the statement
- **Scoped** — include conditions when a fact doesn't apply universally

**Good examples:**
- ✅ "TargetCross lacks reliable odometer data."
- ✅ "Vehicle 259 exceeded maintenance cost threshold in Q2 2026."

**Bad examples:**
- ❌ "TargetCross seems unreliable." *(speculative)*
- ❌ "Vehicle 259 is probably failing." *(speculative)*
- ❌ "Vehicle 259 has high costs and reliability issues." *(compound)*

### Compression Budget

For high-stakes domains (safety, compliance, finance):
- No single-sentence knowledge without attached source excerpt
- No summarization beyond critical sections
- Uncertainty field is mandatory

### Paragraph-Level Provenance

Each knowledge item stores a pointer to the source section (e.g., `section-3` or a content hash). This ensures:
- The original nuance is always recoverable
- The link survives report edits via content hashing rather than line numbers

## 6. Decision Extraction Rules

Decisions must be:
- **Explicit** — clearly stated, not implied
- **Actionable** — something is being done or committed to
- **Traceable** — references at least one knowledge item
- **Owned** — assigned to a human or agent

**Good examples:**
- ✅ "Vehicle 259 will be sold."
- ✅ "The TargetCross integration is postponed pending data quality resolution."

**Bad examples:**
- ❌ "We should look into the vehicle situation." *(not a decision)*
- ❌ "Maybe we'll postpone TargetCross." *(not explicit)*

## 7. Contradiction Detection

The Scribe compares extracted knowledge against existing validated knowledge using:
- Semantic similarity (pgvector)
- Keyword overlap
- Conflicting logical statements

When a potential contradiction is found, the candidate is flagged:
```json
{
  "contradiction_flag": true,
  "contradicts_id": "UUID of conflicting knowledge item",
  "contradiction_type": "direct | temporal | contextual"
}
```

The Scribe **does not resolve** contradictions — it flags and routes to the Archivist.

## 8. Pipeline

```
Input (conversation / log / document)
    │
    ▼
Normalize & clean text
    │
    ▼
Generate context summary
    │
    ▼
Extract reasoning & analysis
    │
    ▼
Generate conclusions
    │
    ▼
Build structured Report → INSERT into Postgres
    │
    ▼
Generate embeddings → INSERT into pgvector
    │
    ▼
Extract Knowledge candidates (strict template)
    │
    ▼
Extract Decision candidates
    │
    ▼
Discover relationship candidates
    │
    ▼
Flag contradiction candidates
    │
    ▼
Push all candidates → archivist_queue (Redis)
    │
    ▼
Return report_id + candidate counts
```

## 9. Interfaces

### Input

```
POST /scribe/process
{
  "source_type": "conversation | task | document",
  "payload": { ... },
  "author": "agent-name",
  "tags": ["optional", "tags"]
}
```

### Output

```json
{
  "report_id": "UUID",
  "status": "processed | needs_review",
  "knowledge_candidates": 4,
  "decision_candidates": 1,
  "relationship_candidates": 3,
  "contradiction_flags": 1
}
```

### Redis Queues

| Queue | Purpose |
|-------|---------|
| `scribe_queue` | Incoming raw inputs |
| `archivist_queue` | Candidates awaiting validation |

## 10. Quality Requirements

| Requirement | Description |
|-------------|-------------|
| **Accuracy** | No invented facts — only what the source contains |
| **Traceability** | Every knowledge item links back to a source section |
| **Separation** | Scribe extracts; Archivist curates — no blending of roles |
| **Human readability** | Reports must be understandable without a model |
| **Deterministic structure** | Consistent JSON output for every run |
| **Compression budget** | High-stakes domains require full excerpt retention |

## 11. Failure Modes

The Scribe must detect and handle:

| Failure | Action |
|---------|--------|
| Incomplete input | Request more context or flag `needs_review` |
| Ambiguous conclusions | Output `status: needs_review` |
| Missing evidence | Reject knowledge extraction, flag for human |
| Potential hallucination | Flag `confidence: 0` and add to review queue |
| Duplicate knowledge detected | Flag for Archivist deduplication |
| Malformed output | Log, retry once, then route to human queue |

On failure, the Scribe outputs:
```json
{ "status": "needs_review", "reason": "string" }
```

## 12. Non-Goals

The Scribe does **not**:
- Validate or curate knowledge
- Resolve contradictions
- Merge duplicates
- Prune memory
- Enforce retention policies
- Make decisions about what should be remembered

These are the Archivist's responsibilities.

## 13. Summary

The Scribe is the front door of North Star's institutional memory. Its job is to transform raw activity into structured, traceable, human-readable artifacts.

**The Scribe creates structure. The Archivist protects quality. Together, they ensure the system grows wiser, not heavier.**
