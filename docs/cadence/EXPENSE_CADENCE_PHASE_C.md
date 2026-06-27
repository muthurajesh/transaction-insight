# Expense cadence — Phase C (Edit UI)

**Status:** Implemented  
**Depends on:** [EXPENSE_CADENCE.md](EXPENSE_CADENCE.md) (Phase A — APIs exist)

## Goal

Let users set cadence on the **Edit Transactions** panel without SQL or chat — e.g. mark InsurerCo as **every 12 months**, bi-weekly gym as **every 2 weeks**.

## User examples (from chat)

| Merchant | Charge | Cadence |
|----------|--------|---------|
| InsurerCo | ~$928 once/year in April | `recurring_lump`, 12 months |
| Auto insurance | $600 every 6 months | `recurring_lump`, 6 months |
| Payroll-adjacent | bi-weekly | `recurring`, 2 weeks |
| One-time repair | single debit | `one_time` or exclude from run-rate |

## APIs (already exist)

| Endpoint | Use in UI |
|----------|-----------|
| `GET /api/transactions/{id}/cadence` | Show cash / core / normalized for selected row |
| `POST /api/cadence-rules` | Save merchant-level rule |
| Edit apply endpoint | Extend payload with per-transaction cadence fields |

Backend: `upsert_cadence_rule()`, transaction cadence columns in `transaction_edit.py` apply path.

## UI requirements

### Location

**Edit Transactions** panel — new section **“Expense cadence”** below label fields (or collapsible).

### Fields

| Field | Control | Maps to |
|-------|---------|---------|
| Cadence kind | Select | `cadence_kind`: `recurring`, `lump`, `one_time`, `exclude`, `unknown` (no change) |
| Every | Number input | `period_count` |
| Unit | Select | `period_unit`: `months`, `weeks`, `days` |
| Include in run-rate | Checkbox | `include_in_run_rate` |
| Note | Optional text | `cadence_note` |
| Apply to | Radio | **This transaction only** vs **All future for this merchant** |

Presets (optional chips): Monthly (1 mo), Yearly (12 mo), Semi-annual (6 mo), Bi-weekly (2 wk).

### Effective amounts preview

After field change (debounced), call `GET /api/transactions/{id}/cadence` or compute client-side from known amount:

```text
Cash: $900.00  |  Core: $0  |  Normalized: $75.00/mo
```

Show in edit summary before **Apply**.

### Apply behavior

| Scope | Writes |
|-------|--------|
| This transaction | `transactions.cadence_*`, `cadence_source='user'` |
| Merchant rule | `cadence_rules` via `upsert_cadence_rule` + optional tx columns |

Reuse edit scopes pattern (`single` / `merchant_amount` / `merchant`) — cadence merchant scope likely **merchant** only.

### Bulk edit

v1: single-row or same merchant scope only. Defer bulk cadence on unrelated multi-select.

## Integration with edit insights

Optional later: after label apply, edit insights modal also suggests cadence if amount pattern looks annual. Not required for Phase C MVP.

## Files to change

| File | Change |
|------|--------|
| `webapp/static/index.html` | Cadence form markup in edit panel |
| `webapp/static/app.js` | Bind fields, preview, include in apply payload |
| `webapp/static/styles.css` | Section layout |
| `webapp/services/transaction_edit.py` | Accept cadence fields on apply |
| `webapp/main.py` | Extend edit API schema if needed |

## Verification

1. Open Edit → select InsurerCo April row
2. Set Yearly (12 months), Apply to merchant
3. `GET /api/transactions/{id}/cadence` → normalized ~$77
4. Re-open edit — fields show saved values
5. Phase B (when done): chat “normalized April categories” reflects change

## Context

Discussed alongside Phase B; user wants semi-annual and bi-weekly without enum-only rigidity — use `period_count` + `period_unit`.
