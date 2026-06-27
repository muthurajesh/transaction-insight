# Pipeline lookups — SQLite only

**Status:** Shipped — DB is the runtime source; save path writes SQLite only  
**Roadmap:** §7 Pipeline storage  
**Related:** [PIPELINE_MERCHANT_LABELS.md](PIPELINE_MERCHANT_LABELS.md) (labels before LLM)

## Current state

| Step | Behavior |
|------|----------|
| **Load** | `webapp/adapters/lookup_store.py` builds in-memory lookup dicts from `finance.db` |
| **Process** | `webapp/pipeline/run.py` — validated description cache, then LLM (bank text only); implausible cached labels rejected |
| **Save** | SQLite upsert only (`save_lookup_workbook_to_db`) when `save_lookups=True` on the pipeline config |

### Table mapping (internal sheet names → SQLite)

| Lookup concept | SQLite table | Notes |
|----------------|--------------|-------|
| Description cache | `description_lookup` | Cached bank text → generated description |
| Bank category rules | `category_rules` | Bank category → AI category/type |
| Custom rules | `pipeline_custom_rules` | Compiled JSON rules |
| Merchant labels | `merchant_labels` | Per-merchant category, type, classification |
| Cadence | `cadence_rules` | Merchant cadence for run-rate views |

## Remaining gaps

| Gap | Detail |
|-----|--------|
| **Save-path hardening** | Pure in-memory merge on save; confirmed user/web merchant labels must stay authoritative (see ROADMAP §7) |

**Shipped:** Confirmed SQLite `merchant_labels` applied before LLM on re-import — [PIPELINE_MERCHANT_LABELS.md](PIPELINE_MERCHANT_LABELS.md).  
**Shipped (Tier C):** Removed `webapp/excel/`, workbook load/save helpers, and `openpyxl` dependency.

## Phased checklist

### Phase 1 — Schema

- [x] `description_lookup`, `category_rules`, `pipeline_custom_rules`, `merchant_labels`, `cadence_rules`

### Phase 2 — Pipeline reads DB

- [x] `lookup_store.load_lookup_workbook_from_db`
- [x] Confirmed `merchant_labels` before LLM

### Phase 3 — Pipeline writes DB

- [x] After processing, upsert into SQLite
- [~] Pure in-memory merge on save (hardening still open)

## Key files

| Area | Path |
|------|------|
| Schema | `webapp/db/schema.py` |
| Load / save | `webapp/adapters/lookup_store.py` |
| Apply rules | `webapp/processing/lookups.py` |
| Process | `webapp/services/process.py`, `webapp/pipeline/run.py` |

## Verification

1. Fresh DB + CSV → Run processing completes with no workbook on disk
2. Second run on same merchant text skips description LLM (cache hit from DB)
3. Confirm Categories + CustomRules persist across re-process

## Open decisions

1. Retain `lookup_snapshots` or fold into typed tables only
