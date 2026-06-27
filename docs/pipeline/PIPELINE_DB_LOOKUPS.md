# Pipeline lookups — SQLite only

**Status:** Shipped — DB is the runtime source; save path writes SQLite only  
**Roadmap:** §7 Pipeline storage  
**Related:** [PIPELINE_MERCHANT_LABELS.md](PIPELINE_MERCHANT_LABELS.md) (labels before LLM)

## Current state

| Step | Behavior |
|------|----------|
| **Load** | `webapp/adapters/lookup_store.py` builds in-memory lookup dicts from `finance.db` |
| **Process** | `webapp/pipeline/run.py` — validated description cache, then LLM (bank text only); implausible cached labels rejected |
| **Save** | SQLite upsert only (`save_lookup_workbook_to_db`) when `update_lookup_workbook=True` on the pipeline config |

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

## Phased checklist

### Phase 1 — Schema

- [x] `description_lookup` — `source_key`, descriptions, `generated_description`, model, `updated_at`
- [x] `category_rules` — bank source category → AI category, budget tier, type, notes
- [x] `pipeline_custom_rules` — rule text, status, compiled JSON, errors, timestamps
- [~] Extend `merchant_labels` for Budget Tier, Classification, Flow Type, notes (partial)
- [x] `cadence_rules` covers merchant cadence behavior

### Phase 2 — Pipeline reads DB

- [x] `lookup_store.py` — in-memory dict shape for pipeline phases
- [x] Web path calls `run_pipeline` with DB-backed lookups
- [x] Description cache from `description_lookup`
- [x] CustomRules from `pipeline_custom_rules`
- [x] Confirmed `merchant_labels` before LLM — [PIPELINE_MERCHANT_LABELS.md](PIPELINE_MERCHANT_LABELS.md)

### Phase 3 — Pipeline writes DB

- [x] After processing, upsert description keys, merchant labels, rules into SQLite
- [~] Pure in-memory merge on save (drop legacy workbook helper code from save path — Tier C)

## Non-goals

- Per-run Excel ledgers — removed; not part of web workflow
- Workbook import/export in Settings — removed Jun 2026

## Key files

| Area | Path |
|------|------|
| Schema | `webapp/db/schema.py` |
| Load / save | `webapp/adapters/lookup_store.py` |
| Process | `webapp/services/process.py`, `webapp/pipeline/run.py` |
| Merge logic | `webapp/processing/lookups.py`, `webapp/llm/descriptions.py` |

## Verification

1. Fresh DB + CSV → Run processing completes with no workbook on disk
2. Second run on same merchant text skips description LLM (cache hit from DB)
3. Confirm Categories + CustomRules persist across re-process

## Open decisions

1. Remove dead `webapp/excel/` and Excel paths in `lookups.py` (Tier C — code prune)
2. Retain `lookup_snapshots` or fold into typed tables only
