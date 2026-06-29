# Transaction Insight — Roadmap

Single index for planned and completed work. Use this file to pick **what to do next**; open linked detail docs for **how to implement**.

**Product direction:** [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md) · **All docs:** [../INDEX.md](../INDEX.md)

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
| [x] | Project layout (`webapp/`, lookup workbook, SQLite) | Phase 0 |
| [x] | Scan inbox + ingest CSV → `transactions` | |
| [x] | Run processing (shared pipeline in web) | Descriptions, lookups, LLM, custom rules |
| [x] | Confirm Categories tab (label queue) | → [CONFIRM_CATEGORIES.md](../classification/CONFIRM_CATEGORIES.md); bulk AI suggest (10/25/50/100) |
| [x] | Chat agent + SQL / analytics tools | `webapp/agent/` |
| [x] | Import & Categorize UI (tabs) | |
| [x] | **Choose CSV files & scan** | Upload → `input/` → scan; see Import section below |

---

## 2. Chat rich UI

**Detail:** [CHAT_RICH_UI.md](../chat/CHAT_RICH_UI.md)

| Status | Item | Detail |
|--------|------|--------|
| [x] | Tier 1 — Markdown, Tabulator, mic, tool trace, `display` API | CHAT_RICH_UI § Tier 1 |
| [x] | **Voice input UX** — focus input, continuous listen, 3s silence / 30s cap, auto-send | CHAT_RICH_UI § Voice input |
| [x] | Help panel — commands, descriptions, **+** insert | CHAT_RICH_UI § Tier 1 |
| [x] | Tier 2 — Chart.js, CSV export, chat history restore | CHAT_RICH_UI § Tier 2 |
| [x] | **Multi-turn chat memory** — prior turns sent to LLM (`CHAT_HISTORY_MESSAGES`) | CHAT_RICH_UI § Tier 2 |
| [x] | **Context usage meter** — toolbar estimate; Clear screen resets LLM anchor | CHAT_RICH_UI § Tier 2 |
| [~] | Tier 3 — **Save as report** from table replies | Shipped in chat — [CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md); [CHAT_TIER3_SAVE_REPORT.md](../chat/CHAT_TIER3_SAVE_REPORT.md) |
| [ ] | Tier 3 — **Multiline composer** (Shift+Enter) | CHAT_RICH_UI § Tier 3 |
| [ ] | Tier 3 — Streaming tokens (optional) | CHAT_RICH_UI § Tier 3; defer unless latency hurts |
| [x] | **Chat routing fix** — “top N categories” must not hit `list_transactions` shortcut | → [CHAT_ROUTING.md](../chat/CHAT_ROUTING.md) |

---

## 3. Edit transactions & AI-assisted rules

**Detail:** [EDIT_INSIGHTS.md](../rules/EDIT_INSIGHTS.md)

| Status | Item | Detail |
|--------|------|--------|
| [x] | Edit Transactions tab — search, bulk label, scopes | |
| [x] | **Custom Rules tab** — preview matches, compiled JSON, Flow Type, apply one/all | [CUSTOM_RULES.md](../rules/CUSTOM_RULES.md) |
| [x] | **Custom Rules builder** — “Build a Simple Rule” helper + “What can I use?” cheatsheet (plain English → composer) | [CUSTOM_RULES.md](../rules/CUSTOM_RULES.md) |
| [ ] | **Complex custom rules** — extend engine beyond 3 AND keys: amount ranges (`>`, `<`, between), date/month filters, multi-pattern AND on text, boolean OR across conditions; update compiler prompt, matcher, tests, and rule builder | → [CUSTOM_RULES.md](../rules/CUSTOM_RULES.md) (detail when work starts) |
| [x] | **Edit insights (post-apply)** — AI pattern + suggested CustomRule | EDIT_INSIGHTS |
| [x] | **AI Rules tab** — taxonomy merge proposals (heuristic + LLM), user confirm apply | [AI_TAXONOMY_RULES.md](../classification/AI_TAXONOMY_RULES.md) |
| [ ] | **Minimal category vocabulary** — broad AI Category + few sub-categories; reduce Salary vs Paychecks/Salary drift | → [CLASSIFICATION_TAXONOMY.md](../classification/CLASSIFICATION_TAXONOMY.md) (vocab hint + normalize shipped; auto-merge future) |
| [x] | **Classification audit** — post-import + scheduled sampled re-check (14b vs audit model); in-app alerts | [CLASSIFICATION_AUDIT.md](../classification/CLASSIFICATION_AUDIT.md) |
| [ ] | Auto-apply taxonomy rules at confidence threshold | Future — `automation_ready` on proposals |
| [x] | Pipeline reads confirmed user/web SQLite `merchant_labels` before LLM on re-import | Authoritative before LLM; Custom Rules remain final → [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md) |

---

## 4. Expense cadence & monthly reporting (layered model)

**Architecture (agreed):** Layer 0 = raw `amount` on `transactions`; Layer 1 = `cadence_rules` per merchant; later layers = AI-saved report rules; **views** = `cash` \| `core` \| `normalized` on read.

**Phase A detail:** [EXPENSE_CADENCE.md](../cadence/EXPENSE_CADENCE.md)

### Phase A — Schema & persistence

| Status | Item | Detail |
|--------|------|--------|
| [x] | `transactions` cadence columns | EXPENSE_CADENCE § Transaction columns |
| [x] | `cadence_rules` table | EXPENSE_CADENCE § Merchant rules |
| [x] | Persist cadence on Run processing | `dataframe_store.py` |
| [x] | Import cadence rules into SQLite | `cadence_rules` via pipeline / Cadence tab |
| [x] | `normalize_monthly_amount` + `effective_amount` | EXPENSE_CADENCE § Normalization |
| [x] | Cadence APIs (`/api/cadence-rules`, `/api/transactions/{id}/cadence`) | EXPENSE_CADENCE § API |

### Phase B — Analytics & chat use views

| Status | Item | What to implement |
|--------|------|-------------------|
| [x] | `expense_view` param on `month_total`, `top_categories`, `flow_totals_by_month` | → [EXPENSE_CADENCE_PHASE_B.md](../cadence/EXPENSE_CADENCE_PHASE_B.md) |
| [x] | Wire view into `query_sql` cheat sheet / agent system prompt | EXPENSE_CADENCE_PHASE_B § Agent prompts |
| [x] | Charts/tables show view label (Cash / Core / Normalized) | EXPENSE_CADENCE_PHASE_B § Display |
| [x] | Fix chat shortcut for “top categories” (see §2) | → [CHAT_ROUTING.md](../chat/CHAT_ROUTING.md) |

### Phase C — Edit UI for cadence

| Status | Item | What to implement |
|--------|------|-------------------|
| [x] | Cadence fields on Edit panel — kind, every N months/weeks | → [EXPENSE_CADENCE_PHASE_C.md](../cadence/EXPENSE_CADENCE_PHASE_C.md) |
| [x] | Save to transaction + optional merchant rule | EXPENSE_CADENCE_PHASE_C § Apply |
| [x] | Show effective amounts (cash / core / normalized) in edit summary | EXPENSE_CADENCE_PHASE_C § Preview |

### Phase D — AI cadence & report layers

**Detail:** [EXPENSE_CADENCE_PHASE_D.md](../cadence/EXPENSE_CADENCE_PHASE_D.md) (Slice D1 first) · broader layers [REPORT_LAYERS.md](../reporting/REPORT_LAYERS.md)

#### Slice D1 — AI cadence propose + confirm (shipped)

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
| [ ] | “Promote layer” — flatten rules into columns or snapshot table | → [REPORT_LAYERS.md](../reporting/REPORT_LAYERS.md) § Phase E |
| [ ] | Optional materialized cache for pinned reports | REPORT_LAYERS § Materialized cache |

---

## 5. Import & inbox

| Status | Item | Notes |
|--------|------|-------|
| [x] | File picker → copy to `input/` → run processing | `POST /api/ingest/upload`, `/api/process/stream` |
| [x] | Scan existing inbox (manual drop) | |
| [x] | Run processing → SQLite (web) | Import & Categorize → Run processing |
| [x] | Pipeline per-phase timing | `webapp/pipeline/run.py`, `webapp/processing/timer.py` |
| [ ] | Same file picker pattern for **Run processing** | → [IMPORT_PROCESS_UPLOAD.md](../pipeline/IMPORT_PROCESS_UPLOAD.md) |

**Monthly workflow (web):** Upload CSV(s) in **Import & Categorize** — files copy to `input/` and processing runs automatically. Use **Run processing** to re-run files already in the inbox.

**Models (from A/B on May 2026):** seed `PIPELINE_MODEL=qwen2.5-coder:32b`; routine months `qwen2.5:7b-instruct` after lookups exist; chat `CHAT_MODEL=qwen2.5:14b`.

---

## 6. Custom reports & saved analytics

| Status | Item | Notes |
|--------|------|-------|
| [x] | `custom_reports` table + chat save/run/list | |
| [x] | Add `:expense_view` to allowed report parameters | EXPENSE_CADENCE_PHASE_B § Custom reports (metadata; SQL stays cash) |
| [x] | Conversational build + save in Chat (prompt + SQL, rename/delete/version) | → [CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md) |
| [~] | Chat Tier 3 save-as-report button | Shipped in chat; multiline composer still open |
| [ ] | UI to manage saved reports (non-chat Settings tab) | → [CUSTOM_REPORTS_UI.md](../chat/CUSTOM_REPORTS_UI.md) |

---

## 7. Pipeline storage — SQLite only

**Detail:** [PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md)

**Context:** Run processing loads and saves pipeline lookups from **`finance.db` only**. Confirmed user/web merchant labels apply before LLM and are protected from pipeline overwrites. Remaining work: pure in-memory merge on save (no legacy workbook helpers in the save path).

| Status | Item | Detail |
|--------|------|--------|
| [x] | **SQLite authoritative for pipeline lookups** | Load + save via `lookup_store.py` |
| [x] | Phase 1 — DB schema for description cache, category rules, custom rules | `description_lookup`, `category_rules`, `pipeline_custom_rules` |
| [x] | Phase 2 — Pipeline loads lookups from DB (web path) | `webapp/adapters/lookup_store.py` |
| [~] | Phase 3 — Pipeline persists lookup updates to DB | Save works; in-memory merge hardening still open |
| [x] | Confirmed `merchant_labels` before LLM on re-import | [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md) |
| [x] | Remove Excel lookup import/export and dead code | Tier A–C: Settings import removed; `webapp/excel/` deleted; `openpyxl` removed |

---

## 8. Decision memory & Agent Workspace

**Detail:** [DECISION_MEMORY.md](DECISION_MEMORY.md) · [AGENT_WORKSPACE.md](AGENT_WORKSPACE.md)

| Status | Item | Detail |
|--------|------|--------|
| [x] | `decision_events` schema + HITL instrumentation | Confirm, audit dismiss, taxonomy apply, edit corrections |
| [x] | Unified pending inbox API | `GET /api/pending-confirmations` |
| [x] | Learning Agent (opt-in scheduler + CLI) | `ai_insights`, `POST /api/learning-agent/run` |
| [x] | Agent Workspace UI (`UI_AGENT_WORKSPACE`) | Sidebar shell: Import / Chat / Review + Transactions, Rules, Settings |
| [x] | Cadence tab hidden; cadence proposals respect `UI_SHOW_CADENCE` | Hidden from inbox/chat proposals when `0` (default with workspace) |
| [x] | Accepted insights → `review_suggest` context | `recent_user_corrections` in prompt |
| [x] | LLM Decision Analyst (`LEARNING_AGENT_MODEL`, `query_sql` loop) | DECISION_MEMORY § Learning Agent |
| [x] | Chat `query_sql` cheat sheet includes decision memory tables | DATA_CHEATSHEET.md |
| [x] | Chat tools: `propose_custom_rule`, insights, `run_decision_analysis` | AGENT_WORKSPACE.md |
| [x] | Chat + inbox unified HITL (Workspace confirm modal) | AGENT_WORKSPACE.md |
| [ ] | Full orchestrator + `monthly_close` workflow | AGENTIC_AI_DESIGN Phase 1–2 |

---

## Suggested order (next work)

1. **Pipeline lookup save hardening** — pure in-memory merge on save; keep confirmed user/web merchant labels authoritative ([PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md))
2. **Chat Tier 3 — multiline composer** (save-as-report shipped — [CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md))
3. **Classification vocabulary / auto-merge** — reduce category drift ([CLASSIFICATION_TAXONOMY.md](../classification/CLASSIFICATION_TAXONOMY.md))
4. **Phase D2–D3 — report layers** + layered reports ([REPORT_LAYERS.md](../reporting/REPORT_LAYERS.md))
5. **Optional:** cadence AI tuning (D1 polish); import file-picker for re-run ([IMPORT_PROCESS_UPLOAD.md](../pipeline/IMPORT_PROCESS_UPLOAD.md))

---

## Detail document index

| Document | Scope |
|----------|--------|
| [../INDEX.md](../INDEX.md) | **Documentation index** — all docs by area |
| [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md) | **Master product reference** — vision, core design, decision gate |
| [AI_SESSION_CONTEXT.md](AI_SESSION_CONTEXT.md) | **New chat bootstrap** — product direction, pitfalls, workflows |
| [ROADMAP.md](ROADMAP.md) | This file — master checklist |
| [DECISION_MEMORY.md](DECISION_MEMORY.md) | Decision event log + Learning Agent |
| [AGENT_WORKSPACE.md](AGENT_WORKSPACE.md) | Unified Workspace UI + inbox |
| [EXPENSE_CADENCE.md](../cadence/EXPENSE_CADENCE.md) | Phase A — schema, normalization, APIs |
| [EXPENSE_CADENCE_PHASE_B.md](../cadence/EXPENSE_CADENCE_PHASE_B.md) | Analytics + chat views (`expense_view`) |
| [EXPENSE_CADENCE_PHASE_C.md](../cadence/EXPENSE_CADENCE_PHASE_C.md) | Edit UI for cadence |
| [EXPENSE_CADENCE_PHASE_D.md](../cadence/EXPENSE_CADENCE_PHASE_D.md) | AI cadence propose + confirm (Slice D1) |
| [REPORT_LAYERS.md](../reporting/REPORT_LAYERS.md) | Phase D2–E — report layers, bake, cache |
| [EDIT_INSIGHTS.md](../rules/EDIT_INSIGHTS.md) | Post-edit AI insight + custom rule suggestion |
| [AI_TAXONOMY_RULES.md](../classification/AI_TAXONOMY_RULES.md) | AI Rules tab — taxonomy merge proposals |
| [CHAT_RICH_UI.md](../chat/CHAT_RICH_UI.md) | Chat UI tiers 1–3, `display` API |
| [CHAT_ROUTING.md](../chat/CHAT_ROUTING.md) | Fix top-categories vs list-transactions shortcut |
| [CHAT_TIER3_SAVE_REPORT.md](../chat/CHAT_TIER3_SAVE_REPORT.md) | Save-as-report button + API |
| [CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md) | Chat conversational custom reports (save, tweak, version) |
| [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md) | SQLite labels on re-import |
| [CONFIRM_CATEGORIES.md](../classification/CONFIRM_CATEGORIES.md) | Confirm merchant → SQLite `merchant_labels` + transactions |
| [PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md) | SQLite pipeline lookups (save hardening in progress) |
| [CLASSIFICATION_TAXONOMY.md](../classification/CLASSIFICATION_TAXONOMY.md) | Classify payload, broad category / minimal sub-category goals |
| [CLASSIFICATION_AUDIT.md](../classification/CLASSIFICATION_AUDIT.md) | Sampled classification quality audit |
| [CUSTOM_REPORTS_UI.md](../chat/CUSTOM_REPORTS_UI.md) | Settings UI for saved reports |
| [IMPORT_PROCESS_UPLOAD.md](../pipeline/IMPORT_PROCESS_UPLOAD.md) | File picker for Run processing |

---

## Maintenance

- Mark items `[x]` in this file when a feature ships.  
- Keep **one paragraph max** per checkbox here; move implementation detail into linked MD files.  
- Rename `EDIT_INSIGHTS.md` title if confusing: it is **edit workflow**, not expense-cadence Phase B.
