# Template: Adding a New Decision Workflow

A decision workflow describes how a category of decisions flows through North Star —
from proposal to execution to reassessment.

---

## 1. Decision lifecycle

```
proposed → validated (by Archivist) → planned → executed
                                             ↘ reverted
                                             ↘ needs_reassessment
```

`needs_reassessment` is set automatically by the Archivist when knowledge
that supports a decision is deprecated or superseded.

---

## 2. Define the decision

A decision candidate (emitted by Scribe or inserted directly) requires:

| Field                   | Requirement                                             |
|-------------------------|---------------------------------------------------------|
| `statement`             | Active voice. "X will be done." Not "We should do X."  |
| `rationale`             | Why. Must reference specific evidence from the source.  |
| `linked_knowledge_ids`  | ≥ 1 validated knowledge item that supports this.        |
| `owner`                 | Who made or owns this decision (name or role).          |
| `status`                | `"planned"` (future) or `"executed"` (already done).   |

---

## 3. Insert via API

```http
POST /decisions
Content-Type: application/json

{
  "statement": "Vehicle 259 will be sold at the next fleet rotation.",
  "rationale": "Maintenance costs exceeded threshold for 3 consecutive quarters (K-uuid-here).",
  "linked_knowledge_ids": ["<uuid-of-supporting-knowledge>"],
  "owner": "Fleet Manager",
  "status": "planned"
}
```

---

## 4. Custom decision status values

The schema supports custom statuses beyond the default lifecycle.
Add domain-specific statuses by extending the CHECK constraint in a new migration:

```sql
-- Example: add "deferred" status
ALTER TABLE decisions
  DROP CONSTRAINT IF EXISTS decisions_status_check;

ALTER TABLE decisions
  ADD CONSTRAINT decisions_status_check
  CHECK (status IN (
    'proposed', 'planned', 'executed',
    'reverted', 'needs_reassessment',
    'deferred'          -- your new status
  ));
```

---

## 5. Automatically flagging decisions for reassessment

When you deprecate or supersede a knowledge item that a decision relies on,
the Archivist automatically sets `status = 'needs_reassessment'` on all
linked decisions. No code changes needed — this is the default behaviour.

To handle `needs_reassessment` in your workflow:

```python
# Poll for decisions needing review
decisions = await ns.list_decisions(status="needs_reassessment")
for d in decisions["items"]:
    print(d["statement"], d["id"])
    # Route to human review, re-evaluate, or execute a reassessment pipeline
```

---

## 6. Checklist

- [ ] Decision statement is clear, active, and non-speculative
- [ ] At least one `validated` knowledge item is linked
- [ ] Owner is named (name or role — not "the team")
- [ ] If you added a custom status, migration is in `src/db/migrations/versions/`
- [ ] Any downstream system that reads decisions filters out `reverted` items
