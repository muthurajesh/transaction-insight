# Optional: Sync web edits → transaction-lookups.xlsx

**Status:** Partial — Confirm Categories syncs per merchant; bulk export still optional  
**Roadmap:** §3 Edit transactions

## Problem

Web **Edit Transactions** writes to **SQLite only**. `transaction-lookups.xlsx` is unchanged unless user:

- Confirms a merchant on **Confirm Categories** (writes **MerchantCategories** / **BusinessCategoryRules**), or
- Saves a **CustomRule** via edit insights modal or the Custom rules panel, or
- Manually edits Excel

Next **CLI-only** run or another machine using Excel alone won’t see web edits.

## Goal

User-triggered **export/sync** of confirmed web labels into the shared lookup workbook — not automatic on every edit.

## Scope (v1 — minimal)

### What to sync

| SQLite source | Excel sheet | Match key |
|---------------|-------------|-----------|
| `merchant_labels` where `label_status='confirmed'` | **BusinessCategoryRules** or **Categories** | `Generated Description` = `merchant_key` |
| User-confirmed cadence (Phase C+) | **ExpenseCadenceRules** | `Generated Description` |

Do **not** overwrite user Excel edits blindly — **merge** like `merge_description_lookup` in CLI.

### Trigger

- **Settings** button: “Export labels to lookup workbook”
- Or post-edit modal checkbox: “Also update lookup file” (off by default)

### API

```http
POST /api/lookups/export-merchant-labels
```

Response: counts merged per sheet, path to `scripts/transaction-lookups.xlsx`.

### Implementation sketch

1. Read `merchant_labels` + optional `cadence_rules` from SQLite
2. Load existing workbook `load_lookup_workbook(path)`
3. Merge rows (append new merchants; update only if `rationale` starts with `user` / `import: web`)
4. Write via `open_excel_workbook` / existing persist helpers in `transaction_insight/core.py`

## Non-goals (v1)

- Two-way real-time sync
- Sync every transaction row (only merchant-level rules)
- Replacing CustomRules compilation flow

## Open decisions (ask user if unclear)

1. BusinessCategoryRules vs Categories sheet for personal labels?
2. Overwrite Excel row if merchant exists with different category?
3. Backup file before write (`transaction-lookups.backup.xlsx`)?

## Files

| File | Role |
|------|------|
| `webapp/services/lookups_export.py` | New merge logic |
| `webapp/main.py` | POST endpoint |
| `webapp/static` Settings UI | Button + result |

## Related

- [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md) — CustomRule path already updates Excel CustomRules sheet
- [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) — opposite direction (DB → pipeline)

## Context

User asked in project chat whether edits update Excel; answer was no — this doc captures optional remedy.
