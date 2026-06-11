# Transaction Insight — Agentic AI Architecture Design

**Status:** Design proposal (Jun 2026)  
**Scope:** Web app + SQLite + local LLM. **Out of scope:** CLI scripts, Excel workbooks, bulk import utilities.  
**Audience:** Future implementation chats — pair with [AI_SESSION_CONTEXT.md](./AI_SESSION_CONTEXT.md) and [ROADMAP.md](./ROADMAP.md).

---

## 1. Executive summary

Transaction Insight today is a **well-built personal finance enrichment system** with a deterministic processing pipeline, a tab-based review UI, and a single chat agent bolted on for analytics. LLM calls are embedded inside fixed pipeline phases; the chat agent uses a flat tool loop with regex shortcuts.

This document proposes evolving the product into an **agent-native personal finance platform** where:

1. **Agents orchestrate work** — ingest, classify, review, cadence, and reporting become multi-step, resumable workflows rather than button clicks on siloed tabs.
2. **MCP is the integration layer** — finance capabilities are exposed as MCP tools (for Cursor, automations, and future external agents) and the app consumes MCP servers for context (calendar, email receipts, bank aggregators when available).
3. **Human-in-the-loop is first-class** — every write to rules or labels flows through propose → preview → confirm, unified across all agents.
4. **SQLite is the sole source of truth** — lookups, merchant labels, cadence rules, and custom rules live in the database; the pipeline reads/writes DB, not spreadsheets.
5. **Specialized agents replace monolithic phases** — each domain (classification, cadence, rules) gets its own agent with scoped tools, memory, and eval hooks.

The transformation is **incremental**: the existing `transaction_insight/pipeline.py` phases become *implementations behind agent tools*, not a rewrite on day one.

---

## 2. Current state analysis

### 2.1 What exists today

| Layer | Implementation | Agentic maturity |
|-------|----------------|------------------|
| **Data store** | SQLite `finance.db` — transactions, merchant_labels, cadence_rules, custom_reports, chat_messages | Solid foundation |
| **Ingest** | CSV upload → scan → raw rows in `transactions` | Deterministic; no agent |
| **Pipeline** | `run_pipeline()` — 8 fixed phases with embedded LLM calls | Monolithic; not agent-driven |
| **Review** | Confirm Categories tab + bulk AI suggest | LLM-assisted but UI-driven |
| **Edit** | Edit Transactions tab + post-edit AI insights | Propose/confirm for CustomRules |
| **Cadence** | `cadence_rules` + AI propose via chat/edit | Best agentic pattern in codebase |
| **Analytics chat** | `webapp/agent/chat.py` — JSON tool loop, max 6 rounds | Single generalist agent |
| **Observability** | `agent_runs` table defined, **never written to** | Schema only |

### 2.2 Architectural gaps

```text
┌─────────────────────────────────────────────────────────────────┐
│  TODAY: Tab-siloed UI + monolithic pipeline + bolt-on chat     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [Import tab] ──► run_pipeline() ──► SQLite                    │
│       │              (linear, no branching)                     │
│       │                                                         │
│  [Confirm tab] ──► review_suggest.py (isolated LLM)            │
│  [Edit tab]    ──► edit_insights.py (isolated LLM)             │
│  [Chat tab]    ──► chat.py (separate tool set, regex shortcuts) │
│                                                                 │
│  No shared workflow state · No agent coordination · No MCP      │
└─────────────────────────────────────────────────────────────────┘
```

**Specific limitations:**

1. **Pipeline is not agentic** — phases in `pipeline.py` run in fixed order; the LLM cannot decide to skip classification, re-run descriptions, or branch based on data quality.
2. **Chat shortcuts bypass reasoning** — `_maybe_direct_answer()` in `chat.py` uses regex to route "top categories" and cadence intents without LLM planning, creating two parallel routing systems.
3. **No workflow persistence** — if processing fails at phase 6, there is no checkpoint; user re-runs from scratch.
4. **Propose/confirm is fragmented** — cadence modal, edit insights modal, and Confirm Categories are three different UX patterns for the same HITL principle.
5. **Lookup state split** — pipeline still reads `transaction-lookups.xlsx`; web edits in SQLite may be overwritten on re-process (documented pitfall in AI_SESSION_CONTEXT).
6. **`agent_runs` unused** — no trace of multi-step agent executions, no resume, no audit trail.
7. **Single model role** — pipeline model vs chat model is configured, but there is no model routing per agent task (fast vs reasoning).

### 2.3 Strengths to preserve

| Principle (from AI_SESSION_CONTEXT) | Keep in agentic design |
|--------------------------------------|------------------------|
| AI-first judgment for ambiguity | Specialist agents own judgment calls |
| Deterministic code for math/SQL | Tool layer stays pure Python |
| User confirm before persist | Unified HITL gate on all writes |
| Layered expense views (cash/core/normalized) | Report agent uses same `effective_amount` |
| Local-first, privacy | No cloud dependency required |

---

## 3. Vision and design principles

### 3.1 Product vision

> **One conversational workspace** where you drop a bank export, an orchestrator agent runs a multi-step enrichment workflow, surfaces only what needs your judgment, and answers any question about your money — with full traceability of every AI decision.

### 3.2 Design principles

| # | Principle | Implication |
|---|-----------|-------------|
| P1 | **Agents plan; tools execute** | LLM decides *what* to do; Python/SQL does *how* with guaranteed correctness |
| P2 | **Workflows are first-class** | Long-running tasks are state machines with checkpoints, not one-shot API calls |
| P3 | **Propose → preview → confirm** | No agent writes labels, rules, or cadence without explicit user approval |
| P4 | **Specialists over generalists** | Domain agents with narrow tool sets outperform one chat agent doing everything |
| P5 | **MCP for interoperability** | Same capabilities serve the web UI, Cursor, and future automations |
| P6 | **Observable by default** | Every agent step logged to `agent_runs` with inputs, outputs, model, latency |
| P7 | **SQLite sole truth** | All lookup/rule state in DB; pipeline is a consumer, not an Excel writer |
| P8 | **Graceful degradation** | If LLM unavailable, deterministic fallbacks + queue for later AI review |

---

## 4. Target architecture

### 4.1 High-level diagram

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                         Presentation Layer                                │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌───────────────────────┐ │
│  │ Agent Workspace UI  │  │ Workflow Timeline │  │ Confirmation Modals  │ │
│  │ (chat + artifacts)  │  │ (live SSE steps)  │  │ (unified HITL)       │ │
│  └──────────┬──────────┘  └────────┬─────────┘  └───────────┬───────────┘ │
└─────────────┼───────────────────────┼─────────────────────────┼────────────┘
              │                       │                         │
┌─────────────▼───────────────────────▼─────────────────────────▼────────────┐
│                         Orchestration Layer                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ Orchestrator Agent                                                   │   │
│  │  · interprets user intent · delegates to specialists · synthesizes   │   │
│  └───────┬──────────┬──────────┬──────────┬──────────┬─────────────────┘   │
│          │          │          │          │          │                       │
│  ┌───────▼───┐ ┌────▼────┐ ┌───▼────┐ ┌───▼────┐ ┌───▼─────┐ ┌──────────┐  │
│  │ Ingestion │ │Classify │ │ Review │ │Cadence │ │  Rule   │ │ Analytics│  │
│  │   Agent   │ │  Agent  │ │ Agent  │ │ Agent  │ │  Agent  │ │  Agent   │  │
│  └───────┬───┘ └────┬────┘ └───┬────┘ └───┬────┘ └───┬─────┘ └────┬─────┘  │
│          │          │          │          │          │            │         │
│  ┌───────▼──────────▼──────────▼──────────▼──────────▼────────────▼─────┐  │
│  │                    Workflow Engine (state machine)                      │  │
│  │  ingest_workflow · monthly_close_workflow · review_workflow · ...    │  │
│  └───────────────────────────────┬───────────────────────────────────────┘  │
└──────────────────────────────────┼──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                         Capability Layer (Tools)                               │
│  ┌─────────────┐ ┌──────────────┐ ┌─────────────┐ ┌────────────────────────┐  │
│  │ Data Tools  │ │ Pipeline     │ │ Rule Tools  │ │ Analytics Tools        │  │
│  │ ingest,scan │ │ phases as    │ │ compile,    │ │ query_sql, views,      │  │
│  │ dedup,query │ │ atomic tools │ │ apply,match │ │ charts, reports        │  │
│  └─────────────┘ └──────────────┘ └─────────────┘ └────────────────────────┘  │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│  SQLite (finance.db)  ·  Event bus (in-process)  ·  MCP Server (stdio/HTTP) │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Layer responsibilities

| Layer | Responsibility | Technology |
|-------|----------------|------------|
| **Presentation** | Single agent workspace; workflow timeline; confirmation modals | FastAPI SSE + enhanced SPA (or React shell) |
| **Orchestration** | Intent routing, multi-agent delegation, synthesis | Orchestrator + specialist agents |
| **Workflow engine** | Stateful multi-step flows, checkpoints, resume | Custom state machine or LangGraph |
| **Capability** | Atomic, testable tools wrapping existing services | Python functions → MCP tool descriptors |
| **Data** | SQLite + event log | Existing schema + extensions |

---

## 5. Agent taxonomy

### 5.1 Orchestrator Agent

**Role:** Primary user interface. Interprets natural language, selects workflows, delegates to specialists, presents unified responses.

| Attribute | Value |
|-----------|-------|
| Model | Reasoning-capable (e.g. `qwen2.5:14b` or cloud `gpt-4o`) |
| Tools | `start_workflow`, `get_workflow_status`, `delegate_to_agent`, `list_pending_confirmations`, all read-only analytics tools |
| Memory | Conversation history + active workflow state + user preferences |
| Does NOT | Directly write to transactions, rules, or labels |

**Example interactions:**

- "I uploaded April's CSV — process it and tell me what needs review"
- "Why was InsurerCo classified as Utilities?"
- "Compare my normalized spending to last month and fix anything that looks wrong"

### 5.2 Ingestion Agent

**Role:** Handle file arrival, validation, dedup, and raw ingest.

| Tools | `list_inbox_files`, `validate_csv_schema`, `ingest_csv`, `check_duplicates`, `archive_processed` |
| Workflow step | `ingest_workflow.scan` → `ingest_workflow.validate` → `ingest_workflow.persist_raw` |
| Autonomy | Fully autonomous until validation fails → escalates to user |

### 5.3 Classification Agent

**Role:** Replace monolithic pipeline LLM phases with agent-driven classification.

| Tools | `generate_descriptions`, `apply_merchant_labels`, `apply_category_rules`, `classify_uncertain_rows`, `suggest_business_labels`, `get_uncertain_merchants` |
| Memory | Merchant knowledge from `merchant_labels` + confirmed patterns |
| HITL | Returns queue of uncertain merchants; does not auto-confirm |

**Key change:** Instead of `run_pipeline()` running all phases, the Classification Agent *plans* which tools to call based on data state:

```text
Plan: 227 rows, 180 already have merchant_labels → skip LLM for those
      47 rows need description LLM (12 cache misses)
      23 rows need classification LLM
      → call generate_descriptions(12) then classify_uncertain_rows(23)
```

### 5.4 Review Agent

**Role:** Manage the label confirmation queue; bulk suggest; explain rationale.

| Tools | `list_review_queue`, `suggest_labels`, `preview_confirm_impact`, `confirm_merchant_labels`, `reject_suggestion` |
| Replaces | Confirm Categories tab logic + `review_suggest.py` |
| HITL | Every confirmation requires user approval; agent prepares batch proposals |

### 5.5 Cadence Agent

**Role:** Expense cadence analysis, proposal, and rule management.

| Tools | `analyze_merchant_cadence`, `propose_cadence_rule`, `list_unknown_cadence_merchants`, `upsert_cadence_rule`, `preview_effective_amounts` |
| Builds on | Existing `cadence_insights.py`, `expense_cadence.py` |
| HITL | Propose → modal → confirm (already implemented; unify modal) |

### 5.6 Rule Agent

**Role:** Custom rule authoring, compilation, dedup, application.

| Tools | `compile_custom_rule`, `list_custom_rules`, `apply_custom_rules`, `check_rule_similarity`, `propose_rule_from_edit` |
| Builds on | `custom_rules.py`, `edit_insights.py`, `custom_rule_similarity.py` |
| HITL | Rule compilation is autonomous; *activation* requires confirm |

### 5.7 Analytics Agent

**Role:** Questions about money — SQL, charts, saved reports, layered views.

| Tools | All current `webapp/agent/tools.py` tools + `run_layered_report`, `save_report_layer` |
| Builds on | `analytics.py`, `custom_reports`, future `report_layers` |
| Autonomy | Read-only; can save reports/layers with user confirm |

---

## 6. Multi-step workflows

### 6.1 Workflow engine design

Replace one-shot `POST /api/process` with **named, resumable workflows** stored in `agent_runs` + new `workflow_checkpoints` table.

```sql
CREATE TABLE workflow_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES agent_runs(id),
    step_name TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    state_json TEXT NOT NULL,      -- serialized workflow context
    status TEXT NOT NULL,          -- pending | running | waiting_user | completed | failed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE pending_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    run_id INTEGER,
    agent_name TEXT NOT NULL,
    confirmation_type TEXT NOT NULL,  -- merchant_label | cadence_rule | custom_rule | report_layer
    proposal_json TEXT NOT NULL,
    status TEXT DEFAULT 'pending',    -- pending | approved | rejected | expired
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
```

### 6.2 Core workflows

#### Workflow 1: `monthly_close`

Triggered when user uploads or requests processing of new CSV(s).

```text
Step 1: INGEST          [Ingestion Agent]
  ├─ validate_csv
  ├─ ingest_raw_rows
  └─ checkpoint → state: { file, row_count, new_tx_ids }

Step 2: ENRICH          [Classification Agent]
  ├─ generate_descriptions (cache-aware)
  ├─ apply_db_rules (merchant_labels, category_rules)
  ├─ classify_uncertain_rows
  ├─ apply_custom_rules
  └─ checkpoint → state: { enriched_df_ref, uncertain_merchants[] }

Step 3: CADENCE         [Cadence Agent]
  ├─ apply_existing_cadence_rules
  ├─ queue_unknown_cadence_merchants
  └─ checkpoint → state: { cadence_queue[] }

Step 4: REVIEW_GATE     [HITL — workflow pauses]
  ├─ surface uncertain_merchants + cadence_queue
  ├─ wait for user confirmations (may take hours/days)
  └─ on confirm → merge approved labels/rules

Step 5: PERSIST         [Deterministic]
  ├─ save_to_sqlite
  ├─ archive_csv
  └─ emit event: monthly_close.completed

Step 6: SUMMARIZE       [Analytics Agent]
  ├─ month_total, top_categories, outliers
  └─ present digest to user
```

**SSE events during workflow:**

```json
{ "type": "workflow_step", "workflow": "monthly_close", "step": "ENRICH", "status": "running", "message": "Classifying 23 uncertain rows…" }
{ "type": "workflow_step", "workflow": "monthly_close", "step": "REVIEW_GATE", "status": "waiting_user", "pending": { "merchants": 8, "cadence": 3 } }
{ "type": "workflow_step", "workflow": "monthly_close", "step": "SUMMARIZE", "status": "completed", "digest": { "month": "2026-04", "spend": 8421.50 } }
```

#### Workflow 2: `merchant_deep_dive`

User asks about a specific merchant; agent investigates and proposes fixes.

```text
Step 1: GATHER    → query all txs for merchant_key, stats, existing rules
Step 2: ANALYZE   → Classification + Cadence agents in parallel
Step 3: PROPOSE   → unified proposal (labels + cadence + optional custom rule)
Step 4: CONFIRM   → single modal with all changes
Step 5: APPLY     → write approved changes
```

#### Workflow 3: `spending_audit`

Periodic review across months.

```text
Step 1: SCAN      → list_outliers across recent months
Step 2: CLUSTER   → group anomalies by merchant/category
Step 3: TRIAGE    → agent ranks by $ impact and confidence
Step 4: PROPOSE   → batch label/cadence fixes
Step 5: CONFIRM   → user reviews prioritized list
```

#### Workflow 4: `report_build`

User describes a report; agent builds, previews, and optionally saves.

```text
Step 1: CLARIFY   → agent asks narrowing questions if needed
Step 2: QUERY     → run SQL / layered report with expense_view
Step 3: VISUALIZE → chart + table display payload
Step 4: OFFER     → "Save as custom report?" → confirm
```

### 6.3 Workflow vs current pipeline mapping

| Current `pipeline.py` phase | Becomes |
|----------------------------|---------|
| Load lookup workbook | `apply_db_rules` tool (reads SQLite) |
| Descriptions (LLM) | Classification Agent tool |
| Enrichment & lookup rules | Deterministic tools |
| AI classification (LLM) | Classification Agent tool |
| Business rules (LLM) | Classification Agent tool |
| Custom rules compile (LLM) | Rule Agent tool |
| Expense cadence | Cadence Agent tools |
| Save lookups workbook | **Removed** — write to SQLite only |

---

## 7. MCP integration

### 7.1 Why MCP

MCP (Model Context Protocol) standardizes how AI agents discover and invoke tools. Transaction Insight should be **both an MCP server** (expose finance capabilities) and an **MCP client** (consume external context).

### 7.2 Transaction Insight as MCP Server

Expose the capability layer as an MCP server so Cursor, Claude Desktop, or custom automations can interact with your finance data without the web UI.

**Server name:** `transaction-insight`  
**Transport:** stdio (local dev) + optional HTTP (LAN)

#### Tool catalog (MCP)

| Tool | Description | Writes? |
|------|-------------|---------|
| `ti_status` | DB counts, available months, inbox files, LLM health | No |
| `ti_query_sql` | Read-only SQL against transactions | No |
| `ti_month_total` | Spend/income for a month with expense_view | No |
| `ti_top_categories` | Top categories with expense_view | No |
| `ti_list_transactions` | Filtered transaction list | No |
| `ti_list_review_queue` | Merchants needing label confirmation | No |
| `ti_suggest_labels` | AI label proposal for merchant | No |
| `ti_confirm_labels` | Apply confirmed labels | **Yes** (HITL gate) |
| `ti_propose_cadence` | AI cadence proposal | No |
| `ti_confirm_cadence` | Save cadence rule | **Yes** (HITL gate) |
| `ti_start_workflow` | Begin monthly_close, merchant_deep_dive, etc. | Yes |
| `ti_workflow_status` | Poll workflow progress | No |
| `ti_list_pending_confirmations` | Awaiting user approval | No |

#### Resource catalog (MCP)

| Resource URI | Content |
|--------------|---------|
| `ti://cheatsheet` | DATA_CHEATSHEET.md |
| `ti://schema` | SQLite schema + column semantics |
| `ti://merchants/pending` | Review queue snapshot |
| `ti://reports` | Saved custom reports list |

#### Prompts (MCP)

| Prompt | Purpose |
|--------|---------|
| `monthly_summary` | Generate month digest with top categories and outliers |
| `merchant_analysis` | Deep dive template for a merchant_key |
| `spending_audit` | Cross-month anomaly triage |

### 7.3 Transaction Insight as MCP Client

Consume external MCP servers to enrich agent context:

| External MCP (future) | Data provided | Used by |
|-----------------------|---------------|---------|
| **Calendar** | Billing due dates, subscription renewals | Cadence Agent |
| **Email** | Receipt parsing, merchant confirmation | Ingestion Agent |
| **Bank aggregator** (Plaid, etc.) | Direct transaction pull | Ingestion Agent |
| **Browser** | Receipt lookup, merchant identification | Classification Agent |

**Phase 1 (immediate):** No external MCP required. Ship the finance MCP server for Cursor integration.

**Phase 2:** Calendar MCP for cadence validation ("your car insurance renews in October").

**Phase 3:** Email/receipt MCP for description enrichment.

### 7.4 MCP + web app relationship

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Web UI     │────►│  FastAPI         │────►│  Capability     │
│  (SPA)      │     │  /api/agent/*    │     │  Layer (tools)  │
└─────────────┘     └────────┬─────────┘     └────────┬────────┘
                             │                        │
┌─────────────┐     ┌────────▼─────────┐              │
│  Cursor     │────►│  MCP Server      │──────────────┘
│  (external) │     │  (same tools)    │
└─────────────┘     └──────────────────┘
```

The MCP server is a **thin wrapper** over the same Python tool functions the web agents call. One implementation, two transports.

---

## 8. Data model evolution

### 8.1 SQLite-first (retire Excel dependency)

| Current Excel sheet | SQLite target | Status |
|--------------------|---------------|--------|
| MerchantCategories | `merchant_labels` | Exists; pipeline must read it |
| CategoryRules | `category_rules` (new) | **New table** |
| BusinessCategoryRules | `business_category_rules` (new) | **New table** |
| DescriptionLookup | `description_cache` (new) | **New table** |
| CustomRules | `custom_rules` (new) | **New table** |
| ExpenseCadenceRules | `cadence_rules` | Exists |
| Types | Drop or merge into merchant_labels | Low priority |

```sql
CREATE TABLE category_rules (
    rule_id TEXT PRIMARY KEY,
    source_category TEXT NOT NULL,
    ai_category TEXT,
    budget_tier TEXT,
    expense_type TEXT,
    flow_type TEXT,
    enabled INTEGER DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE description_cache (
    source_key TEXT PRIMARY KEY,       -- hash of bank text
    generated_description TEXT NOT NULL,
    source TEXT DEFAULT 'llm',         -- llm | user | heuristic
    updated_at TEXT NOT NULL
);

CREATE TABLE custom_rules (
    rule_id TEXT PRIMARY KEY,
    rule_text TEXT NOT NULL,
    compiled_json TEXT,
    status TEXT NOT NULL,              -- pending | active | error | disabled
    last_error TEXT,
    updated_at TEXT NOT NULL
);
```

### 8.2 Agent memory tables

```sql
CREATE TABLE agent_memory (
    memory_id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    memory_type TEXT NOT NULL,         -- merchant_fact | user_preference | workflow_note
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

-- Example: Cadence Agent remembers "user said InsurerCo is 6-month policy, not 12"
-- { "agent": "cadence", "type": "merchant_fact", "key": "Insurer Co", 
--   "value": { "note": "6-month auto policy per user", "confirmed_at": "..." } }
```

### 8.3 Report layers (from REPORT_LAYERS.md)

Implement `report_layers` as planned — the Analytics Agent and MCP server expose `save_report_layer` / `run_layered_report`.

---

## 9. UI/UX transformation

### 9.1 From tabs to agent workspace

**Current:** 5 tabs (Chat, Confirm, Edit, Import, Settings) — user must know the workflow.  
**Target:** Single **Agent Workspace** with contextual panels.

```text
┌────────────────────────────────────────────────────────────────────┐
│  Agent Workspace                                                    │
├──────────────────────────────┬─────────────────────────────────────┤
│  Conversation                 │  Context Panel (dynamic)            │
│  ┌────────────────────────┐  │  ┌─────────────────────────────┐  │
│  │ User: Process April CSV  │  │  │ Workflow: monthly_close      │  │
│  │                          │  │  │ ████████░░ ENRICH 80%        │  │
│  │ Agent: Ingested 227 rows │  │  │ Step 2/6 · 23 need review    │  │
│  │ Found 8 uncertain...     │  │  └─────────────────────────────┘  │
│  │                          │  │  ┌─────────────────────────────┐  │
│  │ [Table: uncertain merch] │  │  │ Pending Confirmations (8)    │  │
│  │ [Confirm All] [Review]   │  │  │ · Netflix → Entertainment  │  │
│  └────────────────────────┘  │  │ · Apple → Business Expenses  │  │
│  ┌────────────────────────┐  │  │ [Approve] [Edit] [Dismiss]    │  │
│  │ > Ask anything...      │  │  └─────────────────────────────┘  │
│  └────────────────────────┘  │                                     │
├──────────────────────────────┴─────────────────────────────────────┤
│  Artifacts: [Chart] [Table] [Saved Report]                         │
└────────────────────────────────────────────────────────────────────┘
```

### 9.2 Unified confirmation modal

One modal component for all HITL gates:

| Field | Purpose |
|-------|---------|
| `confirmation_type` | merchant_label \| cadence_rule \| custom_rule \| report_layer \| bulk_edit |
| `proposal` | Agent's recommendation with rationale |
| `preview` | Impact preview (rows affected, $ amounts, effective cadence) |
| `actions` | Approve · Edit & Approve · Dismiss · Defer |

Replaces: cadence modal, edit insights modal, confirm categories dialog.

### 9.3 Workflow timeline

Live SSE stream showing agent steps — replaces the current process progress bar with richer semantics:

- Which agent is running
- What tool it called
- How many items need review
- Estimated time remaining

### 9.4 Legacy tab migration

| Current tab | Becomes |
|-------------|---------|
| Chat | Main conversation panel (default) |
| Confirm Categories | "Pending confirmations" in context panel |
| Edit Transactions | Agent tool + inline table editing in artifacts |
| Import & Categorize | "Drop CSV here" in conversation + `monthly_close` workflow |
| Settings | Admin panel (DB clear, model config, MCP status) — keep as secondary |

---

## 10. Tool and capability layer

### 10.1 Tool design rules

| Rule | Rationale |
|------|-----------|
| **Idempotent reads** | Same query → same result; safe to retry |
| **Explicit writes** | Write tools require `confirmation_id` from approved HITL gate |
| **Typed I/O** | Pydantic models for all tool inputs/outputs |
| **Deterministic math** | `effective_amount`, `month_total`, SQL — never LLM |
| **Atomic scope** | One tool = one responsibility; agents compose |

### 10.2 Tool registry

Central registry replacing scattered `run_tool()` and service calls:

```python
# webapp/agents/tools/registry.py (proposed)

@tool(category="read", agent=["orchestrator", "analytics"])
def query_sql(sql: str, max_rows: int = 500) -> QueryResult: ...

@tool(category="write", agent=["review"], requires_confirmation=True)
def confirm_merchant_labels(confirmation_id: str) -> ConfirmResult: ...

@tool(category="pipeline", agent=["classification"])
def classify_uncertain_rows(merchant_keys: list[str] | None = None) -> ClassifyResult: ...
```

### 10.3 Pipeline decomposition

Break `run_pipeline()` into callable tools without deleting the orchestrated function:

| Tool | Wraps |
|------|-------|
| `pipeline_generate_descriptions` | `core.fill_generated_descriptions()` |
| `pipeline_apply_rules` | `core.apply_lookup_rules()` + DB equivalents |
| `pipeline_classify_review` | `core.classify_review_rows()` |
| `pipeline_apply_cadence` | `core.apply_expense_cadence_lookup()` |
| `pipeline_compile_custom_rules` | `core.compile_custom_rules_sheet()` |
| `pipeline_run_all` | `run_pipeline()` — legacy fallback |

The Classification Agent calls atomic tools; `pipeline_run_all` remains for backward compatibility during migration.

---

## 11. Memory and knowledge

### 11.1 Memory tiers

| Tier | Store | Lifetime | Example |
|------|-------|----------|---------|
| **Working** | Workflow checkpoint `state_json` | Duration of workflow | Current uncertain merchant list |
| **Episodic** | `agent_runs` + conversation | 90 days | "Last month user corrected Apple to Business" |
| **Semantic** | `agent_memory` + `merchant_labels` | Permanent | "Insurer Co is 6-month policy" |
| **Procedural** | `custom_rules` + `cadence_rules` | Permanent until disabled | Compiled rules |

### 11.2 Merchant knowledge graph (lightweight)

Not a graph DB — a materialized view combining:

- `merchant_labels` (confirmed categories)
- `cadence_rules` (cadence treatment)
- `custom_rules` matching merchant
- `agent_memory` facts
- Transaction stats (count, months active, top amounts)

Exposed as MCP resource `ti://merchants/{merchant_key}/profile` for agent context.

---

## 12. Human-in-the-loop (HITL)

### 12.1 Confirmation flow

```text
Agent tool returns proposal
        │
        ▼
pending_confirmations row created
        │
        ▼
UI shows in Context Panel (or modal for complex)
        │
   ┌────┴────┐
   ▼         ▼
Approve   Edit & Approve
   │         │
   ▼         ▼
Write tool executes with confirmation_id
        │
        ▼
agent_memory updated (if user added context)
        │
        ▼
Workflow resumes from checkpoint
```

### 12.2 Autonomy levels (per agent)

| Level | Behavior | Agents |
|-------|----------|--------|
| **Autonomous** | No confirmation needed | Ingestion (validate), Analytics (read), Classification (descriptions cache) |
| **Propose** | Creates pending confirmation | Review, Cadence, Rule |
| **Blocked** | Cannot execute | Any write without confirmation_id |

### 12.3 Batch confirmation

For `monthly_close` REVIEW_GATE: user can approve all high-confidence suggestions at once, then individually review low-confidence items. Review Agent ranks by confidence and $ impact.

---

## 13. Observability, safety, and evals

### 13.1 Agent run logging

Populate the existing `agent_runs` table:

```python
# Every workflow and agent delegation writes:
agent_runs(run_type="workflow:monthly_close", status="running", detail=json)
# On each step:
agent_runs(run_type="agent:classification", status="completed", detail={
    "tools_called": ["generate_descriptions", "classify_uncertain_rows"],
    "model": "qwen2.5:7b-instruct",
    "tokens": 4200,
    "latency_ms": 12400,
    "rows_affected": 23
})
```

### 13.2 Trace viewer

Web UI panel showing:
- Workflow DAG with step status
- Per-step tool calls and LLM prompts (collapsible)
- Token usage and latency
- Export trace as JSON for debugging

### 13.3 Safety guardrails

| Guardrail | Implementation |
|-----------|----------------|
| SQL injection | Existing `execute_readonly_sql` — SELECT only |
| Write scope | Agents cannot INSERT/UPDATE transactions without confirmation |
| Amount integrity | Layer 0 `amount` is immutable; only labels/cadence change |
| Model output validation | Pydantic parse all LLM JSON; reject malformed |
| Rate limiting | Per-workflow LLM call budget (e.g. max 50 classification calls per run) |
| Audit trail | `pending_confirmations` + `agent_runs` = full history |

### 13.4 Eval framework

Golden test set for agent quality:

```text
tests/evals/
  classification_golden.jsonl   # 50 merchants with expected labels
  cadence_golden.jsonl        # 20 merchants with expected cadence
  analytics_golden.jsonl        # 30 questions with expected SQL results
```

Run via `python -m webapp.agents.evals --agent classification` in CI.

---

## 14. Migration phases

### Phase 0: Foundation (2–3 weeks)

**Goal:** Tool registry + observability + SQLite-first pipeline reads.

| Task | Files |
|------|-------|
| Create tool registry with existing analytics tools | `webapp/agents/tools/` |
| Wire `agent_runs` logging | All agent entry points |
| Add `category_rules`, `description_cache`, `custom_rules` tables | `webapp/db/schema.py` |
| Pipeline reads merchant_labels + new tables before LLM | `pipeline.py`, `core.py` |
| Migrate Excel lookups → SQLite one-time import | `lookups_import.py` |

**Exit criteria:** Pipeline runs without Excel; agent_runs populated on process.

### Phase 1: Workflow engine (3–4 weeks)

**Goal:** `monthly_close` workflow replaces `POST /api/process`.

| Task | Files |
|------|-------|
| Workflow state machine + checkpoints | `webapp/agents/workflows/` |
| `pending_confirmations` table + API | `webapp/main.py` |
| SSE workflow events | Extend `/api/process/stream` |
| Unified confirmation modal | `webapp/static/` |
| Ingestion + Classification agents (thin wrappers) | `webapp/agents/` |

**Exit criteria:** Upload CSV → workflow runs → pauses at review → resumes → digest.

### Phase 2: Specialist agents (3–4 weeks)

**Goal:** Replace tab workflows with agent delegation.

| Task | Files |
|------|-------|
| Review Agent (replaces Confirm tab) | `webapp/agents/review.py` |
| Cadence Agent (unify existing) | `webapp/agents/cadence.py` |
| Rule Agent (unify edit insights + custom rules) | `webapp/agents/rules.py` |
| Orchestrator routes to specialists | `webapp/agents/orchestrator.py` |
| Agent Workspace UI (single panel) | `webapp/static/` |

**Exit criteria:** User completes monthly close entirely via conversation.

### Phase 3: MCP server (2 weeks)

**Goal:** Expose finance tools to Cursor and external agents.

| Task | Files |
|------|-------|
| MCP server package | `mcp_server/` or `webapp/mcp/` |
| Tool descriptors matching registry | Shared with web agents |
| Resources + prompts | Cheatsheet, schema, merchants |
| Cursor MCP config documentation | `docs/MCP_SETUP.md` |

**Exit criteria:** Cursor can query spend, list review queue, start workflow.

### Phase 4: Advanced workflows + external MCP (4+ weeks)

**Goal:** Multi-month audit, report layers, external context.

| Task | Notes |
|------|-------|
| `spending_audit` workflow | Cross-month outlier triage |
| `report_layers` + `run_layered_report` | Per REPORT_LAYERS.md |
| MCP client for calendar/email | When servers available |
| Eval CI pipeline | Golden sets for classification + cadence |
| Streaming tokens in chat | Optional UX polish |

---

## 15. Technology recommendations

| Component | Recommendation | Alternative |
|-----------|----------------|-------------|
| Workflow engine | **LangGraph** (Python, checkpointing built-in) | Custom state machine (lighter) |
| Agent framework | **LangGraph** multi-agent subgraphs | Raw tool loop (current approach, extended) |
| MCP server | **`mcp` Python SDK** (Anthropic) | FastMCP |
| MCP transport | stdio (local) + SSE HTTP (LAN) | — |
| LLM routing | Keep env-based; add per-agent overrides | LiteLLM router |
| Model selection | Fast 7B for batch classification; 14B for orchestrator; 32B for seed | User-configurable |
| Event bus | In-process async (FastAPI BackgroundTasks) | Redis (overkill for local) |
| UI | Enhance vanilla SPA for Phase 1–2; evaluate React shell at Phase 4 | — |
| Testing | pytest + eval golden sets | — |

### 15.1 LangGraph fit

LangGraph maps naturally to the workflow design:

```python
# Conceptual — not implementation code
graph = StateGraph(MonthlyCloseState)
graph.add_node("ingest", ingestion_agent)
graph.add_node("enrich", classification_agent)
graph.add_node("cadence", cadence_agent)
graph.add_node("review_gate", hitl_interrupt)  # interrupt_before
graph.add_node("persist", persist_node)
graph.add_node("summarize", analytics_agent)
graph.add_edge("ingest", "enrich")
# ...
graph.compile(checkpointer=SqliteCheckpointer(DB_PATH))
```

Built-in **interrupt/resume** handles the REVIEW_GATE pause. Checkpoints persist to SQLite alongside `workflow_checkpoints`.

---

## 16. API evolution

### 16.1 New endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /api/agent/chat` | Orchestrator conversation (replaces `/api/chat` gradually) |
| `POST /api/workflows/{name}/start` | Start named workflow |
| `GET /api/workflows/{run_id}/stream` | SSE workflow events |
| `GET /api/workflows/{run_id}/status` | Poll workflow state |
| `POST /api/workflows/{run_id}/resume` | Resume after HITL |
| `GET /api/confirmations` | List pending confirmations |
| `POST /api/confirmations/{id}/resolve` | Approve/reject/edit |
| `GET /api/agent/runs` | Audit log |
| `GET /api/agent/runs/{id}/trace` | Detailed trace |

### 16.2 Deprecation path

| Current | Fate |
|---------|------|
| `POST /api/process` | Wraps `workflows/monthly_close/start` |
| `POST /api/chat` | Delegates to orchestrator |
| Tab-specific APIs | Remain as tools; UI routes through agents |

---

## 17. Risks and open questions

### 17.1 Risks

| Risk | Mitigation |
|------|------------|
| LangGraph dependency adds complexity | Start with custom state machine; adopt LangGraph when workflows prove stable |
| Local LLM too slow for multi-agent | Model routing: 7B for batch, 14B for orchestration; parallel tool calls |
| User confusion during tab → workspace migration | Keep tabs as "advanced" panel for 2 releases |
| SQLite write contention on parallel agents | Single-writer queue for mutations; parallel reads OK |
| MCP security on LAN | Bind HTTP MCP to localhost; optional API key |

### 17.2 Open questions

| # | Question | Decision needed by |
|---|----------|-------------------|
| Q1 | LangGraph vs custom state machine? | Phase 1 kickoff |
| Q2 | React rewrite or enhance vanilla SPA? | Phase 2 |
| Q3 | Keep regex shortcuts in orchestrator or pure LLM routing? | Phase 2 — recommend: LLM primary, regex as fast-path fallback |
| Q4 | How to handle bulk historical data (9k+ txs) in agent workflows? | Phase 1 — batch checkpoints |
| Q5 | Cloud LLM fallback when local unavailable? | Phase 0 |
| Q6 | Multi-user / auth? | Out of scope (personal local app) |

---

## 18. Success metrics

| Metric | Current | Target |
|--------|---------|--------|
| Time from CSV upload to review-ready | ~5 min (pipeline) + manual tab switch | Same pipeline time, zero tab switches |
| User actions for monthly close | ~5+ (upload, process, confirm tab, edit tab, chat) | 1 conversation + batch confirm |
| Agent steps logged | 0% | 100% of workflows |
| Label accuracy (eval set) | Unmeasured | ≥90% on golden merchants |
| External tool access | Web UI only | Web + MCP (Cursor) |

---

## 19. File structure (proposed)

```text
webapp/
  agents/
    __init__.py
    orchestrator.py          # Main conversational agent
    ingestion.py
    classification.py
    review.py
    cadence.py
    rules.py
    analytics.py
    tools/
      registry.py            # Tool definitions + routing
      data.py                # query_sql, list_transactions, …
      pipeline.py            # Decomposed pipeline tools
      rules.py               # compile, apply, similarity
      confirmations.py       # HITL write gate
    workflows/
      engine.py              # State machine / LangGraph
      monthly_close.py
      merchant_deep_dive.py
      spending_audit.py
      report_build.py
    memory.py                # agent_memory read/write
    evals/                   # Golden test runners
  mcp/
    server.py                # MCP server entry
    tools.py                 # MCP tool descriptors
    resources.py
  # existing: main.py, services/, static/, db/
```

---

## 20. Summary

Transaction Insight has strong bones: a proven pipeline, a working chat agent with tools, and the right product principles (AI proposes, user confirms, math stays deterministic). The agentic transformation does not throw this away — it **restructures** it:

1. **Monolithic pipeline → specialist agents** with atomic tools
2. **Siloed tabs → unified agent workspace** with workflow timeline
3. **Bolt-on chat → orchestrator** that drives the whole product
4. **Implicit state → workflow checkpoints** with resume
5. **Isolated propose/confirm → unified HITL layer**
6. **Web-only → MCP server** for Cursor and automations
7. **Excel lookups → SQLite sole truth**

The migration is four phases over ~12–16 weeks, each delivering user-visible value. Phase 0 (SQLite-first + observability) and Phase 1 (`monthly_close` workflow) are the highest-leverage starting points.

---

*Related docs: [AI_SESSION_CONTEXT.md](./AI_SESSION_CONTEXT.md) · [ROADMAP.md](./ROADMAP.md) · [REPORT_LAYERS.md](./REPORT_LAYERS.md) · [EXPENSE_CADENCE_PHASE_D.md](./EXPENSE_CADENCE_PHASE_D.md)*
