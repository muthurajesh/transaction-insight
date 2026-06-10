# Expense cadence (Phase A)

Layer **0** stays raw `amount` on `transactions`. Cadence metadata describes how a charge should be interpreted for monthly reporting.

## Transaction columns

| Column | Meaning |
|--------|---------|
| `cadence_kind` | `recurring`, `lump`, `one_time`, `exclude`, `unknown` |
| `period_count` | User-tweakable period (e.g. `6` for semi-annual, `2` for bi-weekly) |
| `period_unit` | `months`, `weeks`, or `days` |
| `include_in_run_rate` | `1` / `0` / `NULL` (NULL → default by kind) |
| `cadence_source` | `pipeline`, `lookup`, `user`, `detected`, … |
| `cadence_note` | Free text |

Populated on **Run processing** from pipeline `Expense Cadence` columns.

## Merchant rules (`cadence_rules`)

One row per `merchant_key` — Layer **1**. Overrides unknown transaction cadence when resolving.

Imported from **ExpenseCadenceRules** in `transaction-lookups.xlsx` (Import lookups or pipeline).

## Normalization

```text
normalized_monthly =
  lump + months     → abs(amount) / period_count   (e.g. yearly → /12, semi-annual → /6)
  recurring + weeks → abs(amount) × (52 / period_count) / 12
  recurring monthly → abs(amount)
  one_time / exclude → 0
```

## Views (Phase B — wired in chat & analytics)

| View | Use |
|------|-----|
| `cash` | Raw outflow (default) |
| `core` | Run-rate only (`include_in_run_rate`) |
| `normalized` | Spread lumps / scale recurring |

Chat tools: `month_total`, `top_categories`, `flow_totals_by_month` accept `expense_view`.
Detail: [EXPENSE_CADENCE_PHASE_B.md](./EXPENSE_CADENCE_PHASE_B.md).

## Phase roadmap

| Phase | Status | Doc |
|-------|--------|-----|
| A — schema & APIs | Done | this file |
| B — analytics views | Done | [EXPENSE_CADENCE_PHASE_B.md](./EXPENSE_CADENCE_PHASE_B.md) |
| C — Edit UI | Done | [EXPENSE_CADENCE_PHASE_C.md](./EXPENSE_CADENCE_PHASE_C.md) |
| D — AI propose + confirm | **Next** | [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md) |
| E — bake / cache | Planned | [REPORT_LAYERS.md](./REPORT_LAYERS.md) |

## API

- `GET /api/cadence-rules` — list merchant rules
- `POST /api/cadence-rules` — upsert rule
- `GET /api/cadence-rules/{merchant_key}`
- `GET /api/transactions/{id}/cadence` — resolved cadence + effective amounts

Implementation: `webapp/services/expense_cadence.py`.
