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
| [x] | Review inbox + confirm labels | |
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
| [ ] | Tier 3 — **Save as report** from table replies | CHAT_RICH_UI § Tier 3 |
| [ ] | Tier 3 — **Multiline composer** (Shift+Enter) | CHAT_RICH_UI § Tier 3 |
| [ ] | Tier 3 — Streaming tokens (optional) | CHAT_RICH_UI § Tier 3; defer unless latency hurts |
| [ ] | **Chat routing fix** — “top N categories” must not hit `list_transactions` shortcut | Bug: `show me` + `top 10` misroutes; fix `_maybe_list_transactions_answer` / add category intent |

---

## 3. Edit transactions & AI-assisted rules

**Detail:** [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md)

| Status | Item | Detail |
|--------|------|--------|
| [x] | Edit Transactions tab — search, bulk label, scopes | |
| [x] | **Edit insights (post-apply)** — AI pattern + suggested CustomRule | EDIT_INSIGHTS |
| [ ] | Pipeline reads SQLite `merchant_labels` before LLM on re-import | Aligns web edits with next month’s categorize |
| [ ] | Sync web edits → `transaction-lookups.xlsx` (optional export) | Today: SQLite only unless user saves custom rule |

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
| [ ] | `expense_view` param on `month_total`, `top_categories`, `flow_totals_by_month` | Use `resolve_effective_cadence` + `effective_amount`; default `cash` |
| [ ] | Wire view into `query_sql` cheat sheet / agent system prompt | DATA_CHEATSHEET + `chat.py` |
| [ ] | Charts/tables show view label (Cash / Core / Normalized) | `display.py` title/summary |
| [ ] | Fix chat shortcut for “top categories” (see §2) | Can ship with Phase B |

*Detail doc:* create `docs/EXPENSE_CADENCE_PHASE_B.md` when starting (SQL patterns, tool args, UI copy).

### Phase C — Edit UI for cadence

| Status | Item | What to implement |
|--------|------|-------------------|
| [ ] | Cadence fields on Edit panel — kind, every N months/weeks | Maps to `period_count` + `period_unit` |
| [ ] | Save to transaction + optional merchant rule | Reuse `upsert_cadence_rule` |
| [ ] | Show effective amounts (cash / core / normalized) in edit summary | Call `/api/transactions/{id}/cadence` |

### Phase D — AI-authored layers (chat)

| Status | Item | What to implement |
|--------|------|-------------------|
| [ ] | Chat proposes `cadence_rules` after user explains a charge | Like edit insights confirm flow |
| [ ] | `report_layers` or extend `custom_reports` with `:expense_view` | Parameterized SQL + view |
| [ ] | List / enable / disable layers | Docker-layer metaphor |

### Phase E — Bake & performance

| Status | Item | What to implement |
|--------|------|-------------------|
| [ ] | “Promote layer” — flatten rules into columns or snapshot table | Manual trigger |
| [ ] | Optional materialized cache for pinned reports | When layer stack gets slow |

---

## 5. Import & inbox

| Status | Item | Notes |
|--------|------|-------|
| [x] | File picker → copy to `input/` → scan | `POST /api/ingest/upload-and-scan` |
| [x] | Scan existing inbox (manual drop) | |
| [ ] | Same file picker pattern for **Run processing** | Optional UX parity |

---

## 6. Custom reports & saved analytics

| Status | Item | Notes |
|--------|------|-------|
| [x] | `custom_reports` table + chat save/run/list | |
| [ ] | Add `:expense_view` to allowed report parameters | Tied to Phase B |
| [ ] | UI to manage saved reports (non-chat) | Settings or Chat Tier 3 |

---

## Suggested order (next work)

1. **Expense cadence Phase B** — normalized May insurance (~$77 vs $928) in chat and tools  
2. **Chat routing fix** — top categories queries (quick win, can merge with B)  
3. **Phase C** — edit cadence in UI (semi-annual, bi-weekly tweaks)  
4. **Chat Tier 3** — save-as-report, multiline composer  
5. **Phase D–E** — AI layers and bake when experimentation stabilizes  

---

## Detail document index

| Document | Scope |
|----------|--------|
| [ROADMAP.md](./ROADMAP.md) | This file — master checklist |
| [EXPENSE_CADENCE.md](./EXPENSE_CADENCE.md) | Phase A — schema, normalization, APIs |
| [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md) | Post-edit AI insight + custom rule suggestion |
| [CHAT_RICH_UI.md](./CHAT_RICH_UI.md) | Chat UI tiers 1–3, `display` API |
| *TBD* `EXPENSE_CADENCE_PHASE_B.md` | Analytics + chat views |
| *TBD* `REPORT_LAYERS.md` | Phase D — layered rules in DB |

---

## Maintenance

- Mark items `[x]` in this file when a feature ships.  
- Keep **one paragraph max** per checkbox here; move implementation detail into linked MD files.  
- Rename `EDIT_INSIGHTS.md` title if confusing: it is **edit workflow**, not expense-cadence Phase B.
