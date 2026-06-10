# Template: Adding a New Entity Type

North Star entities are named real-world objects (vehicles, people, projects, systems)
that knowledge and decisions can relate to via the `relationships` table.

---

## 1. Decide if you need a new entity type

Entities use a free-form `type` field (e.g. `"vehicle"`, `"person"`, `"project"`).
You do **not** need a new table — just use a new `type` string.

Add a new entity type when:
- The concept recurs across multiple reports and decisions
- You want to query "all knowledge about X" via the graph traverser
- You want `relates_to` edges from knowledge/decisions to this concept

---

## 2. Insert entity instances

### Via the API

```http
POST /entities
Content-Type: application/json

{
  "name": "Vehicle 259",
  "type": "vehicle",
  "metadata": {
    "registration": "AB-123-CD",
    "fleet_region": "northern",
    "year": 2019
  }
}
```

### Via Python SDK

```python
from northstar import NorthStarClient

async with NorthStarClient() as ns:
    # Direct entity creation not yet in SDK — use the REST API or DB directly.
    pass
```

### Directly in SQL

```sql
INSERT INTO entities (name, type, metadata)
VALUES ('Vehicle 259', 'vehicle', '{"registration": "AB-123-CD"}'::jsonb);
```

---

## 3. Add a `relates_to` relationship in your Scribe extraction

In your EXTRACT_TOOL schema or manual knowledge item, reference the entity:

```python
# In relationship_candidates:
{"from_ref": "K0", "to_ref": "E:Vehicle 259", "type": "relates_to"}
```

The `E:<name>` ref syntax is resolved by the Archivist's `_get_or_create_entity()`
helper, which creates the entity if it doesn't exist.

---

## 4. Query all context for an entity

```http
GET /entities/{entity_id}?graph_depth=2
```

Returns linked knowledge, decisions, reports, and contradiction pairs
via the hybrid retrieval graph traverser.

---

## 5. Checklist

- [ ] Entity name is unique and human-readable
- [ ] `type` follows a consistent slug convention (`vehicle`, `person`, `system`)
- [ ] `metadata` stores domain-specific attributes as a flat JSON object
- [ ] Knowledge items that reference this entity use `E:<name>` in their relationship candidates
- [ ] No sensitive PII in `metadata` unless your deployment complies with applicable regulations
