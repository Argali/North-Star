# North Star — Knowledge Seed Packs

Seed packs are collections of pre-validated knowledge items that can be loaded into a North Star instance to give it an immediate baseline of domain knowledge — without requiring any source reports to exist first.

They are the starting point, not the ceiling. Once an instance is running and processing reports, North Star builds its own knowledge from actual organisational experience. Seed packs simply ensure the system is useful on day one.

---

## How seed packs work

Each pack is a JSON file containing a list of `knowledge` items. Each item has the same structure as a knowledge record in the database:

```json
{
  "statement": "The knowledge claim, written as a declarative fact.",
  "topics": ["topic_a", "topic_b"],
  "confidence": 0.95,
  "tags": ["keyword_1", "keyword_2"]
}
```

The `manifest.json` file in this directory is the registry of all available packs. It lists the pack name, file path, and a short description.

When a seed pack is loaded, each item is:
1. Inserted into the knowledge store as a validated item
2. Embedded and indexed in the vector store for semantic retrieval
3. Tagged with `source: seed` so it can be distinguished from knowledge derived from real reports

---

## Loading seed packs

Use the seed loader CLI (from the project root):

```bash
# Load a single pack
python -m north_star.seeds.loader --pack organizational_memory

# Load all developer_core packs
python -m north_star.seeds.loader --collection developer_core

# Load all packs
python -m north_star.seeds.loader --all

# Dry run (validate without inserting)
python -m north_star.seeds.loader --pack procurement --dry-run
```

Packs are idempotent: loading the same pack twice will not create duplicates. Items are matched by statement hash.

---

## Pack collections

### `developer_core` — 9 packs, ~498 items

Foundational software engineering knowledge. Suitable for any organisation building or operating software systems.

| Pack | Items | Coverage |
|---|---|---|
| `architecture` | 60 | Software architecture principles, patterns, and trade-offs |
| `design_patterns` | 63 | GoF patterns, enterprise integration patterns, architectural patterns |
| `testing` | 53 | Testing strategies, methodologies, and tooling concepts |
| `documentation` | 48 | Code and system documentation best practices |
| `security` | 63 | Application security, OWASP, authentication, and secure coding |
| `deployment` | 59 | CI/CD, containerisation, orchestration, and release strategies |
| `observability` | 49 | Logging, metrics, tracing, alerting, and SLOs |
| `data_modeling` | 54 | Database design, schema patterns, indexing, and migrations |
| `performance` | 49 | Caching, query optimisation, async patterns, and scalability |

---

### `cross_domain` — 20 packs, ~1,040 items

Domain knowledge for operations, management, and professional practice. Each pack targets the decisions and patterns that arise repeatedly in a specific field — the knowledge that currently lives in people's heads or nowhere at all.

#### Meta / Foundational

| Pack | Items | Coverage |
|---|---|---|
| `organizational_memory` | 49 | Meeting outcomes, decision records, lessons learned, knowledge governance, and institutional memory. This pack is about *how to build a learning organisation* — which is exactly what North Star enables. It is the most self-referential pack in the library: it documents the theory behind the tool you are using. |
| `decision_making` | 62 | Frameworks, biases, and structured approaches to decision-making |
| `human_ai_collaboration` | 72 | Human-AI teaming patterns, oversight, and collaboration frameworks |
| `learning_certification` | 50 | Learning science, knowledge maps, certification paths, common mistakes |

#### Operations & Management

| Pack | Items | Coverage |
|---|---|---|
| `project_management` | 62 | Planning, estimation, risk, and delivery across methodologies |
| `business_analysis` | 63 | Requirements, stakeholder management, and business process analysis |
| `compliance` | 50 | Regulatory frameworks, audit readiness, and compliance operations |
| `investment_decision` | 51 | Capital allocation, ROI analysis, and investment decision frameworks |
| `procurement` | 46 | Supplier evaluation, vendor scoring, tender analysis, contract risk, and award decisions |
| `proposal_writing` | 49 | Proposal structures, scope definitions, pricing models, risk clauses, and project estimations |

#### Technical Operations

| Pack | Items | Coverage |
|---|---|---|
| `cybersecurity` | 71 | Threat modeling, security operations, and defence-in-depth |
| `technical_support` | 49 | Issue patterns, root cause analysis, fixes, workarounds, and knowledge extraction from tickets |
| `observability` | 49 | *(also in developer_core)* |

#### Industry Verticals

| Pack | Items | Coverage |
|---|---|---|
| `fleet_management` | 55 | Vehicle lifecycle, maintenance scheduling, and fleet operations |
| `logistics` | 52 | Supply chain, routing, warehousing, and last-mile delivery |
| `transport_route_optimization` | 47 | Route planning, VRP/TSP, capacity utilization, and scheduling |
| `maintenance` | 50 | Preventive, predictive, and corrective maintenance strategies |
| `property_management` | 50 | Maintenance cycles, tenant relations, lease renewals, energy cost control, and vendor management |
| `healthcare_administration` | 44 | NHS operational workflows, compliance (CQC, GDPR, MHA), scheduling, and patient flow |

#### Sales & Customer-Facing

| Pack | Items | Coverage |
|---|---|---|
| `sales_engineering` | 33 | Customer requirements, technical objection handling, competitor comparisons, PoC design |
| `customer_success` | 31 | Health scoring, renewal risk, escalation management, and feature request pipelines |

---

## The `organizational_memory` pack

This pack deserves a specific note because it is different from the others.

Every other pack contains knowledge *about a domain*. The `organizational_memory` pack contains knowledge *about how organisations capture, maintain, and use knowledge* — which is the problem North Star itself solves.

Its items cover:
- Why organisations lose knowledge (turnover, silos, informal communication)
- The difference between explicit and tacit knowledge (Nonaka & Takeuchi)
- How to write decision records that are actually useful
- Why lessons-learned meetings fail and how to run ones that don't
- Knowledge governance — who owns knowledge quality, how conflicts are resolved
- The theory of the learning organisation (Argyris, Senge)

If you are implementing North Star and need to explain *why* to stakeholders, this pack is the theoretical grounding. If you are running North Star and want it to help users think about knowledge management, load this pack first.

---

## Creating a new pack

A pack file must conform to this structure:

```json
{
  "pack": "your_pack_name",
  "version": "1.0.0",
  "description": "One sentence describing what domain this covers and who it is for.",
  "knowledge": [
    {
      "statement": "A declarative claim that is true, general, and actionable. Written as a single complete sentence. Not a tip or instruction — a fact about how the domain works.",
      "topics": ["primary_topic", "secondary_topic"],
      "confidence": 0.95,
      "tags": ["keyword_1", "keyword_2", "relevant_concept"]
    }
  ]
}
```

### Statement quality guidelines

A good seed pack statement:
- **Is declarative**, not imperative. "Temporal contradictions in knowledge bases require a supersession record" — not "Always create a supersession record."
- **Is general**, not specific to one organisation. It describes a pattern, not an instance.
- **Is actionable**. Someone reading it should be able to do something differently as a result.
- **Cites the mechanism**, not just the outcome. "X causes Y *because* Z" is more useful than "X causes Y."
- **Is falsifiable**. If it cannot be wrong, it is not a knowledge item — it is a platitude.

A poor statement:
> "Good communication is important in projects."

A good statement:
> "In projects where the project manager communicates status weekly with all stakeholders, escalation delays decrease by an average of 40% compared to projects using ad-hoc communication. The mechanism is early signal — weekly updates surface issues before they become blockers."

### Confidence values

| Value | Meaning |
|---|---|
| `0.99` | Established principle with strong empirical or theoretical backing |
| `0.95–0.97` | Well-supported, minor caveats exist |
| `0.90–0.94` | Generally true, meaningful variance by context |
| `< 0.90` | Use with caution — contested, emerging, or highly context-dependent |

Do not use `1.0`. No knowledge item is certain.

### Registering a new pack

After creating the file, add it to `manifest.json`:

```python
import json

with open('seeds/manifest.json') as f:
    manifest = json.load(f)

manifest['packs'].append({
    "name": "your_pack_name",
    "file": "cross_domain/your_pack_name.json",
    "description": "Short description matching the pack file."
})

with open('seeds/manifest.json', 'w') as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
```

Always use Python to update the manifest — do not edit it manually in a text editor, as partial writes corrupt the JSON.

---

## Pack versioning

Pack files carry a `version` field. When items are updated in a later version:
- The loader compares statement hashes against existing records
- New items are inserted
- Changed items (same statement, different confidence or tags) are updated
- Removed items are **not** deleted — they may have been superseded by real organisational knowledge

Version history for each pack is tracked in `CHANGELOG.md` at the pack level (to be created when packs reach v1.1+).

---

## Total coverage

| Collection | Packs | Items |
|---|---|---|
| `developer_core` | 9 | ~498 |
| `cross_domain` | 20 | ~1,040 |
| **Total** | **29** | **~1,538** |
