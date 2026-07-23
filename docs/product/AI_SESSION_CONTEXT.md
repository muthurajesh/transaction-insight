# AI session context — Transaction Insight

**Purpose:** Paste or `@`-reference this file when opening a **new chat** so the agent inherits product direction, shipped work, and pitfalls without replaying long threads.

**Maintenance:** Update when major decisions ship. **Non-negotiable direction:** [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md). **Status/checklists:** [ROADMAP.md](ROADMAP.md). **Implementation detail:** linked phase docs in [docs/INDEX.md](../INDEX.md) — do not duplicate here.

---

## 1. Product direction (non-negotiable)

Full charter: [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md).

| Principle | Meaning |
|-----------|---------|
| **AI-first judgment** | LLM handles ambiguous calls (cadence, routing, label nuance). Avoid growing rule/heuristic sprawl for new patterns. |
| **Rules for exact math** | Layer 0 amounts, dedup fingerprints, SQL aggregation, `effective_amount` / `expense_view` — deterministic code. |
| **User confirm before persist** | AI *proposes*; user confirms (edit insights modal, cadence modal) before writes to rules/DB. |
| **SQLite only** | Pipeline lookups live in `finance.db`; load/save via `lookup_store.py`. |

---

## 2. Stack & run

| Item | Value |
|------|--------|
| App | `./start.sh` → http://127.0.0.1:8000 |
| DB | `data/finance.db` |
| Inbox | `input/` CSVs |
| Sample CSV | `samples/sample_transactions.csv` |
| Config | Beginners: `cp config/.env.ollama.beginner config/.env` — see [LLM_SETUP.md](../setup/LLM_SETUP.md) |
| **Default (beginner)** | One model: `qwen2.5:14b` (or `7b` on smaller RAM) for pipeline + chat |
| **Expert roles** | Optional: seed/audit `qwen2.5-coder:32b`; routine `7b`; chat `14b` |

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
| **CustomRules** / pipeline lookups in SQLite | **Yes** — loaded from DB each run |
| **Confirmed SQLite merchant_labels before LLM** | **Yes** — user/web confirmed labels applied before LLM review; see [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md) |

---

## 5. Shipped features (this project arc)

### Expense cadence

| Phase | Status | Doc |
|-------|--------|-----|
| A — schema & APIs | Done | [EXPENSE_CADENCE.md](../cadence/EXPENSE_CADENCE.md) |
| B — `expense_view` in analytics/chat | Done | [EXPENSE_CADENCE_PHASE_B.md](../cadence/EXPENSE_CADENCE_PHASE_B.md) |
| C — Edit UI cadence | Done | [EXPENSE_CADENCE_PHASE_C.md](../cadence/EXPENSE_CADENCE_PHASE_C.md) |
| D1 — AI propose + confirm | Done | [EXPENSE_CADENCE_PHASE_D.md](../cadence/EXPENSE_CADENCE_PHASE_D.md) |
| D2–D3 — report layers | Not started | [REPORT_LAYERS.md](../reporting/REPORT_LAYERS.md) |

**D1 triggers:** Chat with cadence keyword + merchant name; or `POST /api/cadence-rules/propose`; tool `propose_cadence_rule`.  
**D1 gaps:** LLM may invent merchant names; use `transaction_id` for amount-specific preview; date ranges (e.g. 6-month policy) may still propose 12 months — user must verify modal.

### Edit & insights

- Edit table: cadence columns + filters ([Phase C](../cadence/EXPENSE_CADENCE_PHASE_C.md))
- Post-edit AI insight + CustomRule suggest; duplicate rule suppression ([EDIT_INSIGHTS.md](../rules/EDIT_INSIGHTS.md), `custom_rule_similarity.py`)
- **Custom Rules tab** — preview matches (current vs proposed), read-only compiled JSON, `flow_type` in rules, apply one/all ([CUSTOM_RULES.md](../rules/CUSTOM_RULES.md))

### Agent Workspace & decision memory

- **`UI_AGENT_WORKSPACE=1` (default):** Sidebar shell — **Import**, **Chat**, **Review** + Transactions, Custom Rules, Settings; legacy Import / Confirm / AI Rules / Cadence tabs hidden ([AGENT_WORKSPACE.md](AGENT_WORKSPACE.md))
- **`decision_events`** + **`ai_insights`**; HITL logging on confirm, audit dismiss, taxonomy apply, edit corrections ([DECISION_MEMORY.md](DECISION_MEMORY.md))
- **Learning Agent:** opt-in (`LEARNING_AGENT_ENABLED=0` default); LLM Decision Analyst (`LEARNING_AGENT_MODEL`, `query_sql` loop) + heuristic fallback → inbox; scheduler + `POST /api/learning-agent/run`; accepted insights → `review_suggest` context
- **Pending:** Full orchestrator / monthly_close workflow (Steps 4–5 chat+HITL shipped)

### Chat UI

- Tiers 1–2 done; routing fix for top categories ([CHAT_RICH_UI.md](../chat/CHAT_RICH_UI.md), [CHAT_ROUTING.md](../chat/CHAT_ROUTING.md))
- **Multi-turn memory** — last `CHAT_HISTORY_MESSAGES` user/assistant turns sent on each `/api/chat` (short follow-ups work)
- **Context meter** — toolbar estimates conversation tokens; **Clear screen** resets LLM context anchor (export/history modal still has full log)
- **Mic** — continuous listen, 3s silence / 30s cap, auto-send when listening ends (CHAT_RICH_UI § Voice input)
- Tier 3: **save-as-report** shipped in chat ([CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md)); multiline composer still pending
- First-visit onboarding tour + Import progress ETA (`PIPELINE_SECONDS_PER_ROW`)

### Pipeline LLM behavior (recent)

- **Descriptions:** validated `description_lookup` cache, then LLM — bank text fields only (Original/User/Simple); no category/amount in the LLM payload; no verbatim copy of User/Simple as the label.
- **Classification:** review spend rows only; minimal LLM payload; vocabulary hint + normalize from DB (`CLASSIFY_VOCABULARY_HINT`) per [CLASSIFICATION_TAXONOMY.md](../classification/CLASSIFICATION_TAXONOMY.md).
- **Classification audit:** post-import + optional scheduled sample; heuristics + `CLASSIFICATION_AUDIT_MODEL` spot-check; Import tab alerts with **At audit / Suggested / Current in DB**; **View in Edit Transactions** (merchant search); auto-resolve when live labels match suggestion — [CLASSIFICATION_AUDIT.md](../classification/CLASSIFICATION_AUDIT.md).
- **Logging:** `LLM_LOG_CALLS=1` → console + `data/llm.log` (`webapp/llm/request_log.py`).

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
| Edit | `webapp/services/transaction_edit.py`, `edit_insights.py`, `custom_rules.py` |
| DB save | `webapp/adapters/dataframe_store.py` |
| UI | `webapp/static/app.js`, `index.html` |
| Decision memory | `webapp/services/decision_events.py`, `learning_agent.py`, `webapp/agent/learning_analyst.py`, `pending_confirmations.py` |

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

See [ROADMAP.md](ROADMAP.md) § Suggested order. Short list:

1. Tune D1 (merchant-from-amount, date-range → period_count)
2. Chat Tier 3 multiline composer; report layers D2–D3

---

## 10. How to start a new chat (copy-paste)

```text
@docs/product/PRODUCT_CHARTER.md @docs/product/AI_SESSION_CONTEXT.md @docs/product/ROADMAP.md

Task: [one sentence goal]

Constraints: AI-first; minimal diff; read linked docs before coding.
```

### Is this the best way?

| Approach | Use for |
|----------|---------|
| **This file** | Session bootstrap — direction, pitfalls, workflows |
| **[ROADMAP.md](ROADMAP.md)** | What's done / next; checkboxes |
| **Phase docs** (`EXPENSE_CADENCE_*.md`, etc.) | How to implement a feature |
| **Cursor rules** (`.cursor/rules/`) | Always-on constraints (browser MCP, commit policy) |
| **Long chat threads** | Meter tracks conversation only; use **Clear screen** for a fresh thread; raise `CHAT_HISTORY_MESSAGES` if needed |

**Best practice:** New window + `@AI_SESSION_CONTEXT.md` + `@ROADMAP.md` + one-line task. Update this file when a major arc completes (don't paste 50 chat turns).

---

*Last synced: Jun 2026 — web-only pipeline under `webapp/`, DB-first lookups, expense cadence A–D1.*
