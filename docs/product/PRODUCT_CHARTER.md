# Transaction Insight — Product Charter

**Purpose:** Master reference for product direction. Before any feature, refactor, or shortcut: check against this document. If work would materially deviate, stop and discuss.

**Not this doc:** task lists ([ROADMAP.md](ROADMAP.md)), stack notes ([AI_SESSION_CONTEXT.md](AI_SESSION_CONTEXT.md)), or implementation specs (linked phase docs in [docs/INDEX.md](../INDEX.md)).

---

## 1. Vision

Transaction Insight is a personal finance workspace — not a one-shot CSV converter.

1. Give the user an **interactive** way to engage with expenses: review, question, correct, and explore.
2. Use **AI** to surface insights: anomalies, spending patterns, reports, and charts — especially through conversation with the data.

---

## 2. Core design (non-negotiable)

**AI makes programmatic decisions about meaning. Code provides structure.**

| Layer | Owns |
|-------|------|
| **AI (local LLM)** | Merchant/description cleanup, categories, sub-categories, flow type nuance, fixed vs variable judgment, personal vs business advice, chat interpretation, pattern explanations |
| **User + SQLite memory** | Confirmed merchant labels, category rules, cadence rules, custom rules, saved reports — intelligence the user approved |
| **Code (structural only)** | CSV parsing, IDs, amount signs, enums (`Expense` / `Income` / `Transfer` / `Adjustment`, `Fixed` / `Variable`, etc.), SQL, dedup, UI plumbing |

**Do not** encode merchant names, category taxonomies, or description keyword logic in Python/JS as a substitute for AI judgment. When something feels like “if description contains X, category = Y,” that belongs in the LLM, user rules, or DB lookups — not application code.

**Default interaction model:** AI **proposes** → user **confirms** → persisted memory improves the next run. Auto-save without review is an explicit exception, not the default.

**Phase I platform:** Local LLM via Ollama or LM Studio. Privacy-first; no cloud dependency required for core workflows.

---

## 3. Iterative intelligence (memory, not model surgery)

We do not edit local model weights. Intelligence grows through the **flow**:

1. AI proposes (category, cadence, rule text, chat answer).
2. User accepts, edits, or rejects.
3. Outcomes persist in SQLite (labels, rules, lookups, cadence).
4. The next run uses that memory **before** or **during** the next LLM call.

```text
User action (confirm / edit / rule / cadence save)
        │
        ▼
┌───────────────────────────────────────┐
│  SQLite memory (source of truth)      │
│  merchant_labels · category_rules ·   │
│  description_lookup · cadence_rules · │
│  pipeline_custom_rules · transactions │
└───────────────────────────────────────┘
        │
        ├─► Tier A: Apply directly (no LLM) ─────────► row labeled
        │
        ├─► Tier B: Rules engine (no LLM) ───────────► row labeled
        │
        └─► Tier C: LLM call with context ───────────► propose → user confirms → back to SQLite
              · known_vocabulary
              · merchant history / similar rows (future)
              · optional semantic retrieval (future)
              · chat: query_sql tools
```

| Tier | What | LLM called? |
|------|------|-------------|
| Confirmed merchant labels & description cache | Exact reuse | Often no |
| Custom / category / cadence rules | User-approved logic | No |
| Prompt context (e.g. known vocabulary) | Steer ambiguous rows | Yes, informed |
| Chat & analytics | LLM + SQL tools over live DB | Yes, grounded in data |
| Optional future: semantic retrieval (RAG) | Fuzzy “similar past decisions” | Yes, when keys are weak |

**Goal:** suggestions become **more accurate and faster** because known truth is reused — not because we grow keyword `if` statements in code.

RAG (vector retrieval over text chunks) is one industry pattern for combining a fixed LLM with external knowledge. This project’s primary pattern is **structured memory in SQLite** plus prompt context — better suited to repeat merchants, exact rules, and SQL-grounded chat. Semantic retrieval may help later for fuzzy merchant matching or decision notes; it is not the core design for “always categorize Merchant A as Category X.”

Implementation detail: [pipeline/PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md), [pipeline/PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md).

---

## 4. Data & ingest requirements

1. User loads a CSV through the web app.
2. Minimum useful columns: **date**, **amount**, **category**, and **one or more description** fields.
3. Additional columns (account, bank expense type, etc.) are welcome — the pipeline should use them when present, not require them.
4. Source data is preserved for audit (SQLite; optional Excel export for lookups).

---

## 5. AI capabilities

### 5.1 Interactive chat

The chat window uses the LLM to:

- Interpret natural-language questions about the user’s data
- Query the database (read-only analytics)
- Return insights, tables, charts, and saveable reports

Chat is a first-class way to work with expenses — not an add-on to static screens.

### 5.2 Simplified description (per transaction)

Bank CSVs often split description across multiple columns. AI combines those fields with date, amount, and category to produce a **simple, human-readable description** (merchant label) suitable for review and grouping.

### 5.3 Classification (per transaction)

Using description(s), amount, date, and the bank’s category, AI assigns:

- **AI Category**
- **AI Sub-category**

Bank category is input, not the final word — AI applies intelligence on top.

### 5.4 Pattern recognition (across transactions)

AI observes history and advises on dimensions that single rows cannot settle alone:

**Transaction kind (flow)**  
Classify expense, income, adjustment, or transfer — as insight, not a dumb sign rule. Examples the system should reason about:

- Salary that lands at the **end of the previous month**, not the 1st
- Transfers between accounts for **savings**, vs **monthly set-aside** for **annual or semi-annual** bills (property insurance, auto insurance)

**Expense type (fixed vs variable)**  
Same-amount recurrence suggests fixed; variability suggests variable. The system should notice **pattern changes** — e.g. internet bill stable for months, then increases.

**Personal vs business**  
AI advises; the user decides. Those decisions feed back into memory (labels, rules) so later runs are sharper.

Pattern recognition is a **learning process**: propose → user corrects → store → re-run gets better.

---

## 6. User outcomes (success looks like)

- I can load a month of bank data and trust AI-enriched labels after a short review pass.
- I can ask “why did fixed expenses jump?” or “show transfers that look like insurance set-asides” in chat.
- When AI is wrong, I fix it once; the app remembers without re-teaching every import.
- I never wonder whether a category was chosen by a hidden keyword list in code.

---

## 7. Decision gate (for features and shortcuts)

Ask before shipping:

1. **Judgment:** Is AI (+ user DB) doing the decision, or new hardcoded finance rules in code?
2. **Confirmation:** Does the user see proposals before writes to rules/labels?
3. **Engagement:** Does this make the app more interactive, or only batch processing?
4. **Patterns:** Does this help cross-transaction intelligence (cadence, flow, fixed/variable shifts)?
5. **Local-first:** Does Phase I still run on local LLM without requiring cloud?
6. **Memory loop:** Does user feedback land in SQLite and change the next decision path?

If the answer conflicts with §2 or §3, treat it as a **charter deviation** — discuss before merging.

---

## 8. Relationship to other docs

| Doc | Use when |
|-----|----------|
| This charter | “Should we build this?” / “Is this true to the product?” |
| [ROADMAP.md](ROADMAP.md) | “What’s next?” / status |
| [AI_SESSION_CONTEXT.md](AI_SESSION_CONTEXT.md) | New agent session — stack, models, pitfalls |
| [docs/INDEX.md](../INDEX.md) | Find any implementation doc by area |
| Phase & pipeline docs | How to implement a shipped or planned area |

---

*Established: Jun 2026*
