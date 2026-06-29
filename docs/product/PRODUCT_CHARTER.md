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

### Tier D — Decision memory (meta-learning)

Distinct from **outcome memory** (confirmed labels in SQLite). Decision memory captures how the user responds to **AI proposals**:

```text
AI proposal → user accept | edit | reject | defer
        │
        ▼
  decision_events (append-only log)
        │
        ▼
  Learning Agent (scheduled, opt-in) → ai_insights → Workspace inbox
        │
        ▼
  Accepted insights → prompt context for review_suggest (still HITL)
```

| Action surface | Logged when |
|----------------|-------------|
| Confirm Categories | User confirms with optional AI suggestion snapshot |
| Classification audit | User dismisses a finding |
| AI Rules | User applies taxonomy proposals |
| Edit Transactions | User changes labels (correction vs prior row) |
| Learning Agent inbox | User accepts or rejects an insight |

Implementation: [product/DECISION_MEMORY.md](DECISION_MEMORY.md), [product/AGENT_WORKSPACE.md](AGENT_WORKSPACE.md).

**Goal:** suggestions become **more accurate and faster** because known truth is reused — not because we grow keyword `if` statements in code.

RAG (vector retrieval over text chunks) is one industry pattern for combining a fixed LLM with external knowledge. This project’s primary pattern is **structured memory in SQLite** plus prompt context — better suited to repeat merchants, exact rules, and SQL-grounded chat. Semantic retrieval may help later for fuzzy merchant matching or decision notes; it is not the core design for “always categorize Merchant A as Category X.”

Implementation detail: [pipeline/PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md), [pipeline/PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md).

---

## 4. Data & ingest requirements

1. User loads a CSV through the web app.
2. Minimum useful columns: **date**, **amount**, **category**, and **one or more description** fields.
3. Additional columns (account, bank expense type, etc.) are welcome — the pipeline should use them when present, not require them.
4. Source data is preserved for audit in SQLite.

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

### 5.5 Where AI runs (by tab / phase)

Default posture: **AI proposes → you confirm → then data is saved**. Nothing in the review or taxonomy flows auto-writes without your approval.

| When | Where in app | What the LLM does | Persists without you? |
|------|----------------|-------------------|------------------------|
| **Run processing** | Workspace import strip or Import & Categorize | **Descriptions** — bank text → merchant label. **Classification** — category, sub-category, fixed/variable for unknown merchants. **Business rules** — personal vs business nuance. **Custom Rules** — compiles Pending rules to JSON, then applies. | Yes — pipeline writes to `finance.db` and lookup tables. Re-run improves as cache grows. |
| **Classification audit** | Import & Categorize (alerts) or inbox (`quality_flag`) | Sampled re-check: heuristics + stronger audit model vs pipeline labels; shows audit-time vs live DB labels. | No — **View in Edit Transactions** or dismiss; stale alerts auto-clear when labels match. |
| **Review queue** | Inbox (`merchant_label`) or Confirm Categories → ✨ Suggest labels | Proposes labels for merchants still `needs_review` (lookup-first, then LLM). | No — you confirm in the modal. |
| **After an edit** | Edit Transactions → Apply → AI insight modal | Explains the pattern; may suggest a **Custom Rule** (plain English). | No — save rule is optional. |
| **Cadence** | Cadence tab → ✨ Suggest cadence (AI); Chat | Proposes recurring vs lump vs one-time from merchant history + your hint. | No — Review & save in modal or cadence queue. |
| **Label cleanup** | **AI Rules** tab | **Analyze** (heuristics only) or **Suggest with AI** — duplicate categories, sub-categories, merchant spellings. | No — you select proposals, preview, then Apply. |
| **Analytics & intelligence** | Workspace (Chat) | Plain-English questions → LLM **`query_sql`**; **`run_decision_analysis`**, **`propose_custom_rule`**, insight accept/reject. Saved custom reports — see [chat/CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md). | Saved reports in `custom_reports`; insights/rules after you confirm. |

**Not AI:** Import upload, inbox archive, table counts, most Edit Transactions field updates (direct SQLite), and cadence **math** (`effective_amount`, cash/core/normalized views).

Models are configured in `config/.env`. Split by role: **`PIPELINE_MODEL`** (bulk import), **`CHAT_MODEL`** (interactive), optional **`CLASSIFICATION_AUDIT_MODEL`** and **`LEARNING_AGENT_MODEL`** — see [setup/LLM_SETUP.md](../setup/LLM_SETUP.md).

### 5.6 Improving logic over time (which tool when)

| Scope | Tool | Detail doc |
|-------|------|------------|
| Per-merchant or bank-category defaults | Lookups & confirmed labels (`merchant_labels`, `category_rules`, `description_lookup`, `cadence_rules`) | [pipeline/PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md), [classification/CONFIRM_CATEGORIES.md](../classification/CONFIRM_CATEGORIES.md) |
| Per-pattern if/then (amount, merchant, description) | **Custom Rules** (English → compiled JSON) | [rules/CUSTOM_RULES.md](../rules/CUSTOM_RULES.md), [rules/EDIT_INSIGHTS.md](../rules/EDIT_INSIGHTS.md) |
| Global label vocabulary (synonyms, duplicate spellings) | **AI Rules** tab | [classification/AI_TAXONOMY_RULES.md](../classification/AI_TAXONOMY_RULES.md) |

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
