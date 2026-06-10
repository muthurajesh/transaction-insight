# Transaction Insight — Roadmap

Single index for planned and completed work. Use this file to pick **what to do next**; open linked detail docs for **how to implement**.

**Legend:** `[x]` done · `[ ]` not started · `[~]` partial

---

## How to use this document

| Style | When |
|-------|------|
| **Checkbox here** | Track status; one-line scope is enough to start |
| **→ Detail doc** | Design, APIs, files, formulas, verification |
| **No link yet** | Idea agreed in chat; detail doc to be written when work starts |

---

## 1. Foundation (web app)

| Status | Item | Notes |
|--------|------|-------|
| [x] | Project layout (`webapp/`, `scripts/`, SQLite) | Phase 0 |
| [x] | Scan inbox + ingest CSV → `transactions` | |
| [x] | Run processing (CLI pipeline in web) | Descriptions, lookups, LLM, custom rules |
| [x] | Confirm Categories tab (label queue) | |
| [x] | Chat agent + SQL / analytics tools | `webapp/agent/` |
| [x] | Import & Categorize UI (tabs) | |
| [x] | **Choose CSV files & scan** | Upload → `input/` → scan; see Import section below |

---

## 2. Chat rich UI

**Detail:** [CHAT_RICH_UI.md](./CHAT_RICH_UI.md)

| Status | Item | Detail |
|--------|------|--------|
| [x] | Tier 1 — Markdown, Tabulator, mic, tool trace, `display` API | CHAT_RICH_UI § Tier 1 |
| [x] | Help panel — commands, descriptions, **+** insert | CHAT_RICH_UI § Tier 1 |
| [x] | Tier 2 — Chart.js, CSV export, chat history restore | CHAT_RICH_UI § Tier 2 |
| [ ] | Tier 3 — **Save as report** from table replies | → [CHAT_TIER3_SAVE_REPORT.md](./CHAT_TIER3_SAVE_REPORT.md) |
| [ ] | Tier 3 — **Multiline composer** (Shift+Enter) | CHAT_RICH_UI § Tier 3 |
| [ ] | Tier 3 — Streaming tokens (optional) | CHAT_RICH_UI § Tier 3; defer unless latency hurts |
| [x] | **Chat routing fix** — “top N categories” must not hit `list_transactions` shortcut | → [CHAT_ROUTING.md](./CHAT_ROUTING.md) |

---

## 3. Edit transactions & AI-assisted rules

**Detail:** [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md)

| Status | Item | Detail |
|--------|------|--------|
| [x] | Edit Transactions tab — search, bulk label, scopes | |
| [x] | **Edit insights (post-apply)** — AI pattern + suggested CustomRule | EDIT_INSIGHTS |
| [ ] | Pipeline reads SQLite `merchant_labels` before LLM on re-import | → [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) |
| [ ] | Sync web edits → `transaction-lookups.xlsx` (optional export) | → [PIPELINE_EXCEL_SYNC.md](./PIPELINE_EXCEL_SYNC.md) |

---

## 4. Expense cadence & monthly reporting (layered model)

**Architecture (agreed):** Layer 0 = raw `amount` on `transactions`; Layer 1 = `cadence_rules` per merchant; later layers = AI-saved report rules; **views** = `cash` \| `core` \| `normalized` on read.

**Phase A detail:** [EXPENSE_CADENCE.md](./EXPENSE_CADENCE.md)

### Phase A — Schema & persistence

| Status | Item | Detail |
|--------|------|--------|
| [x] | `transactions` cadence columns | EXPENSE_CADENCE § Transaction columns |
| [x] | `cadence_rules` table | EXPENSE_CADENCE § Merchant rules |
| [x] | Persist cadence on Run processing | `dataframe_store.py` |
| [x] | Import ExpenseCadenceRules from Excel | `lookups_import.py` |
| [x] | `normalize_monthly_amount` + `effective_amount` | EXPENSE_CADENCE § Normalization |
| [x] | Cadence APIs (`/api/cadence-rules`, `/api/transactions/{id}/cadence`) | EXPENSE_CADENCE § API |

### Phase B — Analytics & chat use views

| Status | Item | What to implement |
|--------|------|-------------------|
| [x] | `expense_view` param on `month_total`, `top_categories`, `flow_totals_by_month` | → [EXPENSE_CADENCE_PHASE_B.md](./EXPENSE_CADENCE_PHASE_B.md) |
| [x] | Wire view into `query_sql` cheat sheet / agent system prompt | EXPENSE_CADENCE_PHASE_B § Agent prompts |
| [x] | Charts/tables show view label (Cash / Core / Normalized) | EXPENSE_CADENCE_PHASE_B § Display |
| [x] | Fix chat shortcut for “top categories” (see §2) | → [CHAT_ROUTING.md](./CHAT_ROUTING.md) |

### Phase C — Edit UI for cadence

| Status | Item | What to implement |
|--------|------|-------------------|
| [x] | Cadence fields on Edit panel — kind, every N months/weeks | → [EXPENSE_CADENCE_PHASE_C.md](./EXPENSE_CADENCE_PHASE_C.md) |
| [x] | Save to transaction + optional merchant rule | EXPENSE_CADENCE_PHASE_C § Apply |
| [x] | Show effective amounts (cash / core / normalized) in edit summary | EXPENSE_CADENCE_PHASE_C § Preview |

### Phase D — AI cadence & report layers

**Detail:** [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md) (Slice D1 first) · broader layers [REPORT_LAYERS.md](./REPORT_LAYERS.md)

#### Slice D1 — AI cadence propose + confirm (next)

| Status | Item | What to implement |
|--------|------|-------------------|
| [x] | `POST /api/cadence-rules/propose` — LLM + merchant stats → proposal JSON | EXPENSE_CADENCE_PHASE_D § APIs |
| [x] | Chat tool `propose_cadence_rule` + confirm modal (like edit insights) | EXPENSE_CADENCE_PHASE_D § UX |
| [x] | Duplicate check vs existing `cadence_rules` before save | `cadence_rule_similarity.py` |
| [x] | Post-import batch queue for unknown-cadence merchants | `POST /api/cadence-rules/propose-batch` |
| [x] | Demote pipeline **Detected** cadence on web Run processing | `skip_cadence_detection=True` |

#### Slice D2–D3 — Report layers (after D1)

| Status | Item | What to implement |
|--------|------|-------------------|
| [ ] | `report_layers` table + save/list chat tools | REPORT_LAYERS § Data model |
| [ ] | List / enable / disable layers in UI | REPORT_LAYERS § List / enable |
| [ ] | `run_layered_report` with `expense_view` | REPORT_LAYERS § Chat tools |

### Phase E — Bake & performance

| Status | Item | What to implement |
|--------|------|-------------------|
| [ ] | “Promote layer” — flatten rules into columns or snapshot table | → [REPORT_LAYERS.md](./REPORT_LAYERS.md) § Phase E |
| [ ] | Optional materialized cache for pinned reports | REPORT_LAYERS § Materialized cache |

---

## 5. Import & inbox

| Status | Item | Notes |
|--------|------|-------|
| [x] | File picker → copy to `input/` → scan | `POST /api/ingest/upload-and-scan` |
| [x] | Scan existing inbox (manual drop) | |
| [x] | Split master CSV → monthly files | `scripts/split_export_by_month.py` |
| [x] | Bulk CLI seed (32B) + resume | `scripts/reset_and_seed_pipeline_32b.sh` (`--all`, `--no-reset`, `--force`) |
| [x] | Excel → SQLite without re-LLM | `scripts/import_processed_to_db.py` (`--source history` 9,319 tx · `--source output` 12,088 incl. Transfers) |
| [x] | Pipeline per-phase timing | `transaction_insight/pipeline.py`, `core.py` `PhaseTimer` |
| [ ] | Same file picker pattern for **Run processing** | → [IMPORT_PROCESS_UPLOAD.md](./IMPORT_PROCESS_UPLOAD.md) |
| [ ] | Web UI: import processed xlsx → DB | Discussed; use CLI import script for now |

**Bulk history workflow (Jul 2021–May 2026):**
```bash
./scripts/reset_and_seed_pipeline_32b.sh --all --input-dir original-data   # fresh seed
./scripts/reset_and_seed_pipeline_32b.sh --all --input-dir original-data --no-reset  # resume
python scripts/import_processed_to_db.py --clear --source output           # web DB (full history)
```
CLI seed writes Excel + lookups + history; **not** `finance.db` until import script or web Run processing.

**Models (from A/B on May 2026):** seed `PIPELINE_MODEL=qwen2.5-coder:32b`; routine months `qwen2.5:7b-instruct` after lookups exist; chat `CHAT_MODEL=qwen2.5:14b`.

---

## 6. Custom reports & saved analytics

| Status | Item | Notes |
|--------|------|-------|
| [x] | `custom_reports` table + chat save/run/list | |
| [x] | Add `:expense_view` to allowed report parameters | EXPENSE_CADENCE_PHASE_B § Custom reports (metadata; SQL stays cash) |
| [ ] | UI to manage saved reports (non-chat) | → [CUSTOM_REPORTS_UI.md](./CUSTOM_REPORTS_UI.md) |

---

## Suggested order (next work)

1. **Load web DB from 32B seed** — `import_processed_to_db.py --source output` if CLI batch finished  
2. **Phase D Slice D1** — cadence AI on full merchant history (D1 shipped; tune + use post-import)  
3. **PIPELINE_MERCHANT_LABELS** — SQLite confirmed labels before LLM on re-import  
4. **Chat Tier 3** — save-as-report, multiline composer  
5. **Phase D2–D3** — report layers + layered reports  

---

## Detail document index

| Document | Scope |
|----------|--------|
| [AI_SESSION_CONTEXT.md](./AI_SESSION_CONTEXT.md) | **New chat bootstrap** — product direction, pitfalls, workflows |
| [ROADMAP.md](./ROADMAP.md) | This file — master checklist |
| [EXPENSE_CADENCE.md](./EXPENSE_CADENCE.md) | Phase A — schema, normalization, APIs |
| [EXPENSE_CADENCE_PHASE_B.md](./EXPENSE_CADENCE_PHASE_B.md) | Analytics + chat views (`expense_view`) |
| [EXPENSE_CADENCE_PHASE_C.md](./EXPENSE_CADENCE_PHASE_C.md) | Edit UI for cadence |
| [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md) | AI cadence propose + confirm (Slice D1) |
| [REPORT_LAYERS.md](./REPORT_LAYERS.md) | Phase D2–E — report layers, bake, cache |
| [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md) | Post-edit AI insight + custom rule suggestion |
| [CHAT_RICH_UI.md](./CHAT_RICH_UI.md) | Chat UI tiers 1–3, `display` API |
| [CHAT_ROUTING.md](./CHAT_ROUTING.md) | Fix top-categories vs list-transactions shortcut |
| [CHAT_TIER3_SAVE_REPORT.md](./CHAT_TIER3_SAVE_REPORT.md) | Save-as-report button + API |
| [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) | SQLite labels on re-import |
| [PIPELINE_EXCEL_SYNC.md](./PIPELINE_EXCEL_SYNC.md) | Optional export web edits → Excel |
| [CUSTOM_REPORTS_UI.md](./CUSTOM_REPORTS_UI.md) | Settings UI for saved reports |
| [IMPORT_PROCESS_UPLOAD.md](./IMPORT_PROCESS_UPLOAD.md) | File picker for Run processing |
| `scripts/split_export_by_month.py` | Master CSV → monthly `input/` files |
| `scripts/reset_and_seed_pipeline_32b.sh` | Fresh/ resume bulk CLI pipeline (32B) |
| `scripts/import_processed_to_db.py` | Processed Excel → `finance.db` |

---

## Maintenance

- Mark items `[x]` in this file when a feature ships.  
- Keep **one paragraph max** per checkbox here; move implementation detail into linked MD files.  
- Rename `EDIT_INSIGHTS.md` title if confusing: it is **edit workflow**, not expense-cadence Phase B.
