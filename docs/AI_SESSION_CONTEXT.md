# AI session context — Transaction Insight

**Purpose:** Paste or `@`-reference this file when opening a **new chat** so the agent inherits product direction, shipped work, and pitfalls without replaying long threads.

**Maintenance:** Update when major decisions ship. **Status/checklists** live in [ROADMAP.md](./ROADMAP.md); **implementation detail** in linked phase docs — do not duplicate those here.

---

## 1. Product direction (non-negotiable)

| Principle | Meaning |
|-----------|---------|
| **AI-first judgment** | LLM handles ambiguous calls (cadence, routing, label nuance). Avoid growing rule/heuristic sprawl for new patterns. |
| **Rules for exact math** | Layer 0 amounts, dedup fingerprints, SQL aggregation, `effective_amount` / `expense_view` — deterministic code. |
| **User confirm before persist** | AI *proposes*; user confirms (edit insights modal, cadence modal) before writes to rules/DB. |
| **Excel + SQLite** | Pipeline lookups default to **SQLite** (`LOOKUP_SOURCE=db`). Excel seeded once if DB empty; optional export with `EXPORT_LOOKUPS=1`. |

---

## 2. Stack & run

| Item | Value |
|------|--------|
| App | `./start.sh` → http://127.0.0.1:8000 |
| DB | `data/finance.db` |
| Inbox | `input/` CSVs |
| Config | `config/.env` — `LLM_PROVIDER=ollama`, local `127.0.0.1:11434` |
| **Chat model** | `qwen2.5:14b` |
| **Pipeline seed** | `qwen2.5-coder:32b` (quality) |
| **Routine pipeline** | `qwen2.5:7b-instruct` after lookups exist |

---

## 3. Data model essentials

### `transaction_id` (dedup fingerprint)

Hash of: **date + amount + account + original description** (first 200 chars).  
Same bank line → same id → **upsert on re-process**. Format changes → new id → possible duplicate rows.

### `flow_type` (not sign alone)

| Type | Counted in spend? | Counted in income? |
|------|-------------------|-------------------|
| `Expense` | Yes (`amount < 0`) | No |
| `Income` | No | Yes |
| `Transfer` | No | No |
| `Adjustment` | No | No |

Internal categories → `Transfer`: Transfers, Credit Card Payments, Savings, Securities Trades.  
**Positive ≠ income** — paychecks are Income; CC payments received are Transfer.

### Expense cadence (layers)

- **Layer 0:** raw `amount` on `transactions`
- **Layer 1:** `cadence_rules` per `merchant_key`
- **Views:** `cash` | `core` (run-rate) | `normalized` (spread lumps)

### `source_file`

Provenance + replace-by-filename on web process. **Not** used for dedup.

---

## 4. What re-import / re-pipeline overwrites

| Saved how | Survives Run processing? |
|-----------|-------------------------------------|
| Edit Transactions (row only) | **No** — pipeline UPDATE overwrites labels + tx cadence columns |
| Edit + **merchant label** checkbox | **Often no** — pipeline re-upserts `merchant_labels` |
| **`cadence_rules`** (merchant rule) | **Yes** — separate table |
| **CustomRules** / pipeline lookups in SQLite | **Yes** — loaded from DB each run (`LOOKUP_SOURCE=db`) |
| **SQLite merchant_labels before LLM** | **Not implemented** — see [PIPELINE_MERCHANT_LABELS.md](./PIPELINE_MERCHANT_LABELS.md) |

---

## 5. Shipped features (this project arc)

### Expense cadence

| Phase | Status | Doc |
|-------|--------|-----|
| A — schema & APIs | Done | [EXPENSE_CADENCE.md](./EXPENSE_CADENCE.md) |
| B — `expense_view` in analytics/chat | Done | [EXPENSE_CADENCE_PHASE_B.md](./EXPENSE_CADENCE_PHASE_B.md) |
| C — Edit UI cadence | Done | [EXPENSE_CADENCE_PHASE_C.md](./EXPENSE_CADENCE_PHASE_C.md) |
| D1 — AI propose + confirm | Done | [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md) |
| D2–D3 — report layers | Not started | [REPORT_LAYERS.md](./REPORT_LAYERS.md) |

**D1 triggers:** Chat with cadence keyword + merchant name; or `POST /api/cadence-rules/propose`; tool `propose_cadence_rule`.  
**D1 gaps:** LLM may invent merchant names; use `transaction_id` for amount-specific preview; date ranges (e.g. 6-month policy) may still propose 12 months — user must verify modal.

### Edit & insights

- Edit table: cadence columns + filters ([Phase C](./EXPENSE_CADENCE_PHASE_C.md))
- Post-edit AI insight + CustomRule suggest; duplicate rule suppression ([EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md), `custom_rule_similarity.py`)

### Chat UI

- Tiers 1–2 done; routing fix for top categories ([CHAT_RICH_UI.md](./CHAT_RICH_UI.md), [CHAT_ROUTING.md](./CHAT_ROUTING.md))
- **Mic** — continuous listen, 3s silence / 30s cap, auto-send when listening ends (CHAT_RICH_UI § Voice input)
- Tier 3: save-as-report, multiline composer — pending

### Bulk history / migration

**Web workflow:** Upload monthly CSVs → Run processing writes directly to `finance.db`. Web Run uses `skip_cadence_detection=True` (demote pipeline Detected heuristics).

### Pipeline timing

Per-phase timers in `webapp/pipeline/run.py` + `webapp/processing/timer.py` `PhaseTimer`.

---

## 6. Key files map

| Area | Paths |
|------|--------|
| Pipeline | `webapp/pipeline/run.py`, `webapp/processing/`, `webapp/llm/` |
| Lookups | `webapp/adapters/lookup_store.py` |
| Web API | `webapp/main.py` |
| Chat agent | `webapp/agent/chat.py`, `tools.py`, `display.py` |
| Cadence | `webapp/services/expense_cadence.py`, `cadence_insights.py`, `cadence_rule_similarity.py` |
| Edit | `webapp/services/transaction_edit.py`, `edit_insights.py` |
| DB save | `webapp/adapters/dataframe_store.py` |
| UI | `webapp/static/app.js`, `index.html` |

---

## 7. Dataset shape notes (generic)

- **InsurerCo** appears under multiple `merchant_key`s: `InsurerCo Prem Pay`, `Insurer Co`, `Insurer Casualty Co.` — not one payee.
- Cadence rules are per `merchant_key`; a semi-annual premium on one key does not apply to a differently keyed row for the same brand.
- Flow-type mix varies by month; always check `flow_type` counts before treating a month as all spend.

---

## 8. Known pitfalls for agents

1. Do not assume positive = income; check `flow_type`.
2. Do not assume one merchant label covers all variants; match `merchant_key` from DB.
3. D1 chat without cadence keywords may still call LLM tool but can hallucinate merchant names.
4. Re-processing overwrites SQLite-only edits — promote to rules/lookups first.
5. Re-processing overwrites SQLite-only **transaction row** edits — promote to `merchant_labels`, `cadence_rules`, or pipeline lookup tables first.
6. Browser verify web UI changes via **Browser DevTools MCP only** (workspace rule).
7. Do not git commit unless user asks.

---

## 9. Suggested next work

See [ROADMAP.md](./ROADMAP.md) § Suggested order. Short list:

1. Tune D1 (merchant-from-amount, date-range → period_count) `PIPELINE_MERCHANT_LABELS` — apply SQLite labels before LLM on re-import
4. Chat Tier 3, report layers D2–D3

---

## 10. How to start a new chat (copy-paste)

```text
@docs/AI_SESSION_CONTEXT.md @docs/ROADMAP.md

Task: [one sentence goal]

Constraints: AI-first; minimal diff; read linked docs before coding.
```

### Is this the best way?

| Approach | Use for |
|----------|---------|
| **This file** | Session bootstrap — direction, pitfalls, workflows |
| **[ROADMAP.md](./ROADMAP.md)** | What's done / next; checkboxes |
| **Phase docs** (`EXPENSE_CADENCE_*.md`, etc.) | How to implement a feature |
| **Cursor rules** (`.cursor/rules/`) | Always-on constraints (browser MCP, commit policy) |
| **Long chat threads** | Short follow-ups only; summaries compress detail |

**Best practice:** New window + `@AI_SESSION_CONTEXT.md` + `@ROADMAP.md` + one-line task. Update this file when a major arc completes (don't paste 50 chat turns).

---

*Last synced: Jun 2026 — web-only pipeline under `webapp/`, DB-first lookups, expense cadence A–D1.*
