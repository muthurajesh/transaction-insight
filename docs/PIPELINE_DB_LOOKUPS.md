# Pipeline lookups — SQLite-first (Excel optional)

**Status:** Shipped — DB is default runtime source; save path is SQLite-only (Excel export optional via `EXPORT_LOOKUPS=1`)  
**Roadmap:** §7 Pipeline storage  
**Related:** [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) (labels before LLM) · [PIPELINE_EXCEL_SYNC.md](./PIPELINE_EXCEL_SYNC.md) (export web → Excel backup)

## Current state

With **`LOOKUP_SOURCE=db`** (default in `config/.env`):

| Step | Behavior |
|------|----------|
| **Load** | `webapp/adapters/lookup_store.py` builds in-memory lookup dicts from `finance.db` |
| **First run** | Optional: `LOOKUP_SEED_FROM_EXCEL=1` imports from `scripts/transaction-lookups.xlsx` when DB tables are empty (off by default) |
| **Process** | `webapp/pipeline/run.py` — validated description cache, then LLM; User/Simple fields are LLM context only (not copied verbatim); implausible cached labels rejected |
| **Save** | SQLite upsert only (`save_lookup_workbook_to_db`); optional Excel refresh when `EXPORT_LOOKUPS=1` |

Legacy mode: `LOOKUP_SOURCE=excel` reads/writes the workbook only (not recommended).

### Sheet → table mapping

| Legacy Excel sheet | SQLite table | Notes |
|--------------------|--------------|-------|
| **DescriptionLookup** | `description_lookup` | Cached bank text → generated description |
| **CategoryRules** | `category_rules` | Bank category → AI category/type |
| **CustomRules** | `pipeline_custom_rules` | Compiled JSON rules |
| **MerchantCategories** / **BusinessCategoryRules** | `merchant_labels` | Per-merchant labels; some meta columns still Excel-oriented |
| **ExpenseCadenceRules** | `cadence_rules` | Manual cadence tags |
| **Categories**, **Types** | — | Reference only; not modeled in DB |

## Remaining gaps

| Gap | Detail |
|-----|--------|
| **merchant_labels before LLM** | Confirmed SQLite labels not applied on re-import — see [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) |
| **Bulk export to workbook** | Optional Settings export — see [PIPELINE_EXCEL_SYNC.md](./PIPELINE_EXCEL_SYNC.md) |

## Phased checklist

### Phase 1 — Schema

- [x] `description_lookup` — `source_key`, descriptions, `generated_description`, model, `updated_at`
- [x] `category_rules` — bank source category → AI category, budget tier, type, notes
- [x] `pipeline_custom_rules` — rule text, status, compiled JSON, errors, timestamps
- [~] Extend `merchant_labels` for Budget Tier, Classification, Flow Type, notes (partial)
- [x] `cadence_rules` covers ExpenseCadenceRules behavior

### Phase 2 — Pipeline reads DB

- [x] `lookup_store.py` — same shape as legacy `load_lookup_workbook()`
- [x] Web path calls `run_pipeline` with DB-backed lookups
- [x] Description cache from `description_lookup`
- [x] CustomRules from `pipeline_custom_rules`
- [ ] Ship [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) — confirmed `merchant_labels` before LLM

### Phase 3 — Pipeline writes DB

- [x] After processing, upsert description keys, merchant labels, rules into SQLite
- [~] Stop Excel scratch on save — gate with `EXPORT_LOOKUPS=0` (default skips Excel refresh; internal merge may still touch workbook helpers)

### Phase 4 — Migration & optional Excel

- [x] Auto-seed from workbook when DB empty (`ensure_lookups_seeded()`)
- [ ] Settings **Export to Excel** for full backup ([PIPELINE_EXCEL_SYNC.md](./PIPELINE_EXCEL_SYNC.md))
- [x] `transaction-lookups.xlsx` not required at runtime with `LOOKUP_SOURCE=db` — documented in README

## Non-goals

- Per-run Excel ledgers (`output/*.xlsx`) — removed; not part of web workflow
- Real-time bidirectional Excel sync on every edit — DB wins; export is explicit

## Key files

| Area | Path |
|------|------|
| Schema | `webapp/db/schema.py` |
| Load / save | `webapp/adapters/lookup_store.py` |
| Import | `webapp/services/lookups_import.py` |
| Process | `webapp/services/process.py`, `webapp/pipeline/run.py` |
| Merge logic | `webapp/processing/lookups.py`, `webapp/llm/descriptions.py`, `webapp/excel/` |
| Config | `webapp/config.py` — `LOOKUP_SOURCE`, `EXPORT_LOOKUPS` |

## Verification

1. Fresh DB + CSV → Run processing completes with **no** `transaction-lookups.xlsx` present (after seed or with empty lookups)
2. Second run on same merchant text skips description LLM (cache hit from DB)
3. Confirm Categories + CustomRules persist across re-process without Excel
4. Optional `EXPORT_LOOKUPS=1` refreshes workbook for audit/backup

## Open decisions

1. Pure in-memory merge on save (drop Excel scratch entirely) vs keep export-only helpers in `webapp/excel/`
2. Retain `lookup_snapshots` or fold into typed tables only
