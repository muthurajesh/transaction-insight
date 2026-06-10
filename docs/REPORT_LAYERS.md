# Report layers — Phase D & E (AI rules, bake, cache)

**Status:** Not implemented (Slice D1 cadence AI → [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md))  
**Depends on:** [EXPENSE_CADENCE.md](./EXPENSE_CADENCE.md) (Phase A), [EXPENSE_CADENCE_PHASE_B.md](./EXPENSE_CADENCE_PHASE_B.md), [EXPENSE_CADENCE_PHASE_C.md](./EXPENSE_CADENCE_PHASE_C.md)

## Concept (from project chat)

User-approved **Docker-layer metaphor** for personal finance views:

```text
Layer 0: transactions.amount          (immutable bank truth)
Layer 1: cadence_rules + tx cadence   (Mercury annual → normalized monthly)
Layer 2+: AI-saved report rules       (e.g. “exclude transfers from spend lens”)
Report lens: same date range + expense_view (cash | core | normalized)
```

**Bake** = promote a stable layer into columns or a snapshot so reads stay fast.

## Phase D — AI-authored layers

**Slice D1 (cadence AI):** implement first per [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md).  
**Slices D2–D3 below** = report layers beyond cadence.

### User stories

1. “Mercury is yearly insurance — spread it monthly” → chat saves `cadence_rules` row (confirm modal like edit insights) — **EXPENSE_CADENCE_PHASE_D Slice D1**
2. “For my monthly budget report, exclude one-time home repairs” → saved **report layer** with filter rule
3. “Show all my custom report layers” → list, enable/disable, run with `:month`

### Data model options

| Option | Pros | Cons |
|--------|------|------|
| **A:** Extend `custom_reports` | Reuse save/run/list | SQL-only layers hard for cadence |
| **B:** New `report_layers` table | Clear separation | More schema |
| **C:** `report_layers` + `layer_type` enum | `cadence`, `sql_filter`, `category_mask` | Flexible |

**Recommended:** **C** — single table:

```sql
CREATE TABLE report_layers (
  layer_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  layer_type TEXT NOT NULL,  -- cadence | sql_template | category_filter
  config_json TEXT NOT NULL, -- type-specific payload
  enabled INTEGER DEFAULT 1,
  sort_order INTEGER DEFAULT 0,
  created_at TEXT,
  source TEXT DEFAULT 'user'  -- user | ai | import
);
```

`config_json` examples:

```json
// cadence — same shape as cadence_rules upsert
{ "merchant_key": "MERCURY INS", "cadence_kind": "recurring_lump", "period_count": 12, "period_unit": "months" }

// sql_template — parameterized
{ "sql_template": "...", "parameters": ["month", "expense_view"] }

// category_filter
{ "exclude_categories": ["Transfer", "Credit Card Payment"] }
```

### Chat tools (new or extend)

| Tool | Action |
|------|--------|
| `propose_cadence_rule` | LLM suggests rule; UI confirm → `upsert_cadence_rule` |
| `save_report_layer` | Persist layer after user confirms |
| `list_report_layers` | Enabled layers + descriptions |
| `run_layered_report` | Apply stack: base query → filters → view |

### UX flow (cadence via chat)

Mirror [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md):

1. User explains charge in chat
2. Assistant proposes cadence + rationale
3. Modal: **Save rule** / **Save for this transaction only** / Dismiss
4. On save → `POST /api/cadence-rules` or layer API

### `:expense_view` on custom reports

Add to `_ALLOWED_PARAMS` in `custom_reports.py`:

```python
"expense_view": ("cash", "core", "normalized")
```

**Caveat:** Raw SQL reports cannot normalize per-row until Phase B helper exists. Layered `run_layered_report` should call Python aggregation, not pure SQL SUM.

### List / enable / disable

- Settings or Chat: “disable Mercury normalization layer”
- `enabled=0` skips layer in stack; Layer 0 amounts unchanged

## Phase E — Bake & performance

### When to bake

- User pins a report used weekly
- Layer stack > 3 layers and queries slow
- Cadence rules stable for 30+ days

### Bake strategies

| Strategy | What happens |
|----------|----------------|
| **Column bake** | Write `normalized_amount` / `core_amount` columns on `transactions` for matched rows |
| **Snapshot bake** | `report_snapshots(month, view, category, amount)` materialized |
| **Merchant bake** | Copy `cadence_rules` into `transactions.cadence_*` for all matching txs |

### API sketch

```http
POST /api/report-layers/{id}/bake
{ "strategy": "column", "target": "normalized_amount" }
```

Manual trigger only (v1). Show warning: baked values stale until re-bake.

### Materialized cache

Optional `pinned_reports` table:

```sql
pinned_reports(report_id, last_run_at, result_json, params_hash)
```

Refresh on demand or after new transactions ingested.

## Implementation order

1. Phase B views working in tools
2. Phase C edit UI for cadence (reduces need for chat cadence)
3. Phase D: `propose_cadence_rule` + confirm (smallest D slice)
4. Phase D: `report_layers` + list/enable
5. Phase E: bake only if profiling shows need

## Files (expected)

| Area | Files |
|------|-------|
| Schema | `webapp/db/schema.py` |
| Services | `webapp/services/report_layers.py`, extend `expense_cadence.py` |
| Agent | `webapp/agent/tools.py`, `chat.py` |
| UI | Confirm modals in `index.html` / `app.js` |
| Docs | This file, ROADMAP §4 D–E |

## Context from project chat

- Mercury Ins $928.87 annual → ~$77.41/mo normalized
- Semi-annual (6 months), bi-weekly (2 weeks) via `period_count` + `period_unit`
- User wants AI woven into workflow, not rules-only
- Same date range, different **lenses** — not different databases

## Out of scope (initial D)

- Automatic layer inference without user confirm
- Multi-user / shared layers
- Streaming layer updates
