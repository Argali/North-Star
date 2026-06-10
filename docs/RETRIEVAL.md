# Retrieval Architecture

*How North Star keeps context small while delivering precise, evidence-based answers.*

## 1. Purpose

Retrieval is the mechanism that allows North Star to answer questions and support agents without loading entire histories.

Retrieval must be:
- **Minimal** — only what is needed
- **Targeted** — relevant to the specific query
- **Evidence-based** — every item traces to a report
- **Explainable** — humans can audit what was returned and why
- **Fast** — adds no meaningful latency to agent workflows

North Star retrieval is not a memory dump. It is a **precision tool**.

## 2. Philosophy

### Keep Active Context Small
Agents should never receive:
- Full conversation histories
- Full report archives
- Full knowledge bases

They receive only what is relevant to the current task.

### Retrieval Is Evidence-Based
Every retrieved item must trace back to:
- A **Report** (primary evidence)
- Validated **Knowledge** (curated fact)
- A **Decision** (explicit organizational choice)
- A **Relationship** (connection between nodes)

### Retrieval Is Multi-Layered
North Star uses a hybrid pipeline — not a single lookup method:

1. Keyword search (precision)
2. Semantic search (breadth)
3. Graph traversal (depth)
4. Filtering (quality)
5. Context assembly (minimal output)

## 3. Architecture

```
Query (from user or agent)
        │
        ▼
┌──────────────────┐
│  Query Processor │  → keywords[], query_embedding, entity_ids[]
└────────┬─────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌──────────┐
│Postgres│ │ pgvector │
│keyword │ │ semantic │
│ search │ │  search  │
└───┬────┘ └────┬─────┘
    └─────┬─────┘
          ▼
┌──────────────────┐
│  Hybrid Ranking  │  α·semantic + β·keyword + γ·recency
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Graph Traversal  │  expand via relationships
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│    Filtering     │  confidence, status, topic, recency
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Context Assembler│  minimal package
└──────────────────┘
         │
         ▼
    Agent receives
    only what it needs
```

## 4. Components

### 4.1 Query Processor

Normalizes the input into retrieval signals:

**Input:** natural language query

**Output:**
```json
{
  "keywords": ["vehicle", "259", "maintenance"],
  "query_embedding": [...],
  "entity_ids": ["UUID of Vehicle 259"],
  "intent": "knowledge | report | decision | entity"
}
```

The Query Processor identifies:
- Key terms for Postgres full-text search
- An embedding for pgvector similarity search
- Any entity references (e.g., "Vehicle 259", "TargetCross")
- Query intent to shape which tables to prioritize

### 4.2 Keyword Search (Postgres)

Used for precise, structured matches.

```sql
SELECT r.*, ts_rank(
  to_tsvector('english', r.title || ' ' || COALESCE(r.context_summary,'') || ' ' || COALESCE(r.conclusions,'')),
  plainto_tsquery($1)
) AS keyword_score
FROM reports r
WHERE to_tsvector('english', r.title || ' ' || COALESCE(r.context_summary,'') || ' ' || COALESCE(r.conclusions,''))
  @@ plainto_tsquery($1)
LIMIT 20;
```

Also runs against `knowledge.statement` and `decisions.statement`.

**Strengths:** fast, precise, exact matches on tags and structured fields

### 4.3 Semantic Search (pgvector)

Used for conceptual similarity and fuzzy matching.

```sql
SELECT e.object_id, e.object_type,
       e.embedding <-> $1 AS semantic_distance
FROM embeddings e
WHERE e.object_type = $2  -- 'report', 'knowledge', or 'decision'
ORDER BY e.embedding <-> $1
LIMIT 20;
```

**Strengths:** finds relevant items even when exact terminology differs

### 4.4 Hybrid Ranking

Results from keyword and semantic search are merged and ranked:

```
final_score = α · (1 - semantic_distance) 
            + β · keyword_rank 
            + γ · recency_bonus
```

Where:
- `α` weights semantic relevance (default: 0.5)
- `β` weights keyword precision (default: 0.3)
- `γ` weights recency (default: 0.2)
- `recency_bonus` decays based on `created_at` age

These weights are configurable per deployment.

### 4.5 Graph Traversal

Once candidate nodes are identified, the graph is traversed to expand context:

```sql
-- Expand: knowledge → linked decisions
SELECT d.*
FROM decisions d
JOIN knowledge k ON k.id = ANY(d.linked_knowledge_ids)
WHERE k.id = ANY($1);  -- $1 = array of retrieved knowledge IDs

-- Expand: knowledge → source reports
SELECT r.*
FROM reports r
WHERE r.id = ANY(
  SELECT UNNEST(source_report_ids)
  FROM knowledge
  WHERE id = ANY($1)
);
```

Graph traversal adds **depth** to the context: not just the fact, but its evidence chain.

### 4.6 Filtering Layer

Before final assembly, low-quality items are removed:

| Filter | Rule |
|--------|------|
| Knowledge status | Only `validated` items (unless explicitly requesting proposed) |
| Confidence floor | Minimum confidence threshold (configurable, default: 0.5) |
| Decision status | Exclude `reverted` unless specifically requested |
| Temporal validity | Exclude knowledge where `valid_until < NOW()` |
| Topic scope | Filter by topic tags if query specifies a domain |

Filtering ensures **quality over quantity**.

### 4.7 Context Assembler

Produces the minimal context package:

```json
{
  "reports": [
    {
      "id": "UUID",
      "title": "string",
      "context_summary": "string",
      "conclusions": "string",
      "created_at": "timestamptz",
      "relevance_score": 0.87
    }
  ],
  "knowledge": [
    {
      "id": "UUID",
      "statement": "string",
      "confidence": 0.9,
      "topics": ["tag1"],
      "source_report_ids": ["UUID"],
      "relevance_score": 0.92
    }
  ],
  "decisions": [
    {
      "id": "UUID",
      "statement": "string",
      "rationale": "string",
      "status": "executed",
      "relevance_score": 0.78
    }
  ],
  "relationships": [...]
}
```

This is the **only context** the agent receives.

## 5. Retrieval Modes

### 5.1 Report Retrieval

Used when the query is investigative or historical.

> "Show me the integration review for TargetCross."

Pipeline: keyword search → semantic search → return top reports

### 5.2 Knowledge Retrieval

Used when the query is factual.

> "What do we know about vehicle 259?"

Pipeline: entity detection → knowledge search → graph expansion (source reports, linked decisions)

### 5.3 Decision Retrieval

Used when the query is action-oriented.

> "What decisions were made about maintenance last quarter?"

Pipeline: decision search → linked knowledge → source reports

### 5.4 Entity-Centric Retrieval

Used when the query references a domain object.

> "Show me everything related to Vehicle 259."

Pipeline: entity lookup → `relates_to` relationships → knowledge → reports → decisions

## 6. Retrieval Examples

### Example 1: "Why was Vehicle 259 sold?"

```
1. Keyword search → "Vehicle 259", "sold"
2. Semantic search → reports about Vehicle 259 maintenance and cost
3. Hybrid ranking → top 5 results
4. Graph traversal:
   - Knowledge: "Maintenance costs for Vehicle 259 exceed threshold."
   - Decision: "Vehicle 259 will be sold."
5. Context assembled:

{
  "knowledge": ["Maintenance costs exceeded threshold"],
  "decisions": ["Vehicle 259 will be sold (owner: fleet-manager)"],
  "reports": ["Q2 Fleet Maintenance Review"]
}
```

### Example 2: "What do we know about TargetCross?"

```
1. Keyword search → integration reports, TargetCross mentions
2. Semantic search → similar topic embeddings
3. Graph traversal → related knowledge items
4. Context assembled:

{
  "knowledge": [
    "TargetCross lacks reliable odometer data.",
    "TargetCross integration was postponed (2026-Q2)."
  ],
  "decisions": ["TargetCross integration postponed pending data quality resolution"],
  "reports": ["TargetCross Integration Review"]
}
```

## 7. Retrieval Guarantees

| Guarantee | Description |
|-----------|-------------|
| **Minimality** | Only what is needed — no full-history dumps |
| **Traceability** | Every item links to evidence (report ID at minimum) |
| **Explainability** | Relevance scores are included; chains are auditable |
| **Quality** | Only validated knowledge by default |
| **Human readability** | No opaque embeddings — only structured text fields |

## 8. Caching

Retrieval results are cached in Redis for performance:

```
retrieval_cache:{sha256(query + filters)} → JSON result, TTL 5 min
```

Cache is invalidated when:
- New knowledge is validated for matching topics
- A relevant decision status changes
- A new report is added with overlapping tags

## 9. Summary

North Star retrieval is:
- **Hybrid** — keyword + semantic + graph, not a single method
- **Minimal** — agents receive only what they need
- **Evidence-based** — every result traces back to a report
- **Explainable** — relevance scores and provenance included
- **Fast** — Redis caching, indexed Postgres, ivfflat pgvector index

Retrieval is the **antidote to context bloat**. It is how North Star ensures the system remains usable at 10,000 reports just as at 10.
