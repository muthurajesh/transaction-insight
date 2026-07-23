# Transaction Insight — Documentation index

**Not the project README** — that is [../README.md](../README.md) (prerequisites, quick start, next steps).  
This file is the **catalog of all docs** under `docs/`.

Start here to find the right doc. **Product decisions** always defer to the charter.

---

## Setup (clone & run)

| Doc | Purpose | Status |
|-----|---------|--------|
| [../README.md](../README.md) | Prerequisites, quick start, essential config | Active |
| [setup/LLM_SETUP.md](./setup/LLM_SETUP.md) | Ollama, LM Studio, model roles, performance | Active |

---

## Product (direction & planning)

| Doc | Purpose | Status |
|-----|---------|--------|
| [product/PRODUCT_CHARTER.md](./product/PRODUCT_CHARTER.md) | **Master reference** — vision, core design, AI capabilities, decision gate | Active |
| [product/ROADMAP.md](./product/ROADMAP.md) | Shipped vs planned work; checkboxes and links to detail docs | Active |
| [product/AI_SESSION_CONTEXT.md](./product/AI_SESSION_CONTEXT.md) | Bootstrap for new AI coding sessions (stack, pitfalls, file map) | Active |
| [product/DECISION_MEMORY.md](./product/DECISION_MEMORY.md) | Decision event log + Learning Agent | Shipped |
| [product/AGENT_WORKSPACE.md](./product/AGENT_WORKSPACE.md) | Unified Workspace UI + pending inbox | Shipped |
| [product/AGENTIC_AI_DESIGN.md](./product/AGENTIC_AI_DESIGN.md) | Future agent-native architecture proposal | Design only — not committed scope |
| [product/TRANSACTION_INTELLIGENCE_ARCHITECTURE.md](./product/TRANSACTION_INTELLIGENCE_ARCHITECTURE.md) | Hybrid classifier — base model, user overlay, HPLLM teacher, phased plan | Agreed — Phase 0 not started |

---

## Pipeline (ingest & processing)

| Doc | Purpose | Status |
|-----|---------|--------|
| [pipeline/IMPORT_PROCESS_UPLOAD.md](./pipeline/IMPORT_PROCESS_UPLOAD.md) | CSV upload, Run processing, output columns | Shipped |
| [pipeline/PIPELINE_DB_LOOKUPS.md](./pipeline/PIPELINE_DB_LOOKUPS.md) | SQLite pipeline lookups | Shipped |
| [pipeline/PIPELINE_MERCHANT_LABELS.md](./pipeline/PIPELINE_MERCHANT_LABELS.md) | Confirmed user labels before LLM on re-import | Shipped |

---

## Classification (labels & taxonomy)

| Doc | Purpose | Status |
|-----|---------|--------|
| [classification/CONFIRM_CATEGORIES.md](./classification/CONFIRM_CATEGORIES.md) | Confirm Categories review queue | Shipped |
| [classification/CLASSIFICATION_TAXONOMY.md](./classification/CLASSIFICATION_TAXONOMY.md) | Vocabulary hint + normalize from DB | Shipped; auto-merge future |
| [classification/CLASSIFICATION_AUDIT.md](./classification/CLASSIFICATION_AUDIT.md) | Sampled post-import quality audit | Shipped |
| [classification/AI_TAXONOMY_RULES.md](./classification/AI_TAXONOMY_RULES.md) | AI Rules tab — taxonomy merge proposals | Shipped |

---

## Chat (interactive analytics)

| Doc | Purpose | Status |
|-----|---------|--------|
| [chat/CHAT_RICH_UI.md](./chat/CHAT_RICH_UI.md) | Chat UI tiers 1–3, voice, display API | Active |
| [chat/CHAT_ROUTING.md](./chat/CHAT_ROUTING.md) | Top-categories vs list-transactions routing fix | Shipped |
| [chat/CHAT_CUSTOM_REPORTS.md](./chat/CHAT_CUSTOM_REPORTS.md) | Save/tweak/version custom reports in Chat | Shipped |
| [chat/CHAT_TIER3_SAVE_REPORT.md](./chat/CHAT_TIER3_SAVE_REPORT.md) | Tier 3 save-as-report spec (subset of custom reports) | Mostly shipped — see CHAT_CUSTOM_REPORTS |
| [chat/CUSTOM_REPORTS_UI.md](./chat/CUSTOM_REPORTS_UI.md) | Settings tab for saved reports (non-chat) | Not started |

---

## Cadence (recurring & lump expenses)

| Doc | Purpose | Status |
|-----|---------|--------|
| [cadence/EXPENSE_CADENCE.md](./cadence/EXPENSE_CADENCE.md) | Phase A — schema, normalization, APIs | Shipped |
| [cadence/EXPENSE_CADENCE_PHASE_B.md](./cadence/EXPENSE_CADENCE_PHASE_B.md) | Analytics + chat `expense_view` | Shipped |
| [cadence/EXPENSE_CADENCE_PHASE_C.md](./cadence/EXPENSE_CADENCE_PHASE_C.md) | Edit UI for cadence | Shipped |
| [cadence/EXPENSE_CADENCE_PHASE_D.md](./cadence/EXPENSE_CADENCE_PHASE_D.md) | AI cadence propose + confirm | Shipped (D1) |

---

## Rules (user-authored logic)

| Doc | Purpose | Status |
|-----|---------|--------|
| [rules/CUSTOM_RULES.md](./rules/CUSTOM_RULES.md) | Custom Rules tab — compile, preview, apply | Shipped |
| [rules/EDIT_INSIGHTS.md](./rules/EDIT_INSIGHTS.md) | Post-edit AI insight + rule suggestions | Shipped |

---

## Reporting (layers & future analytics)

| Doc | Purpose | Status |
|-----|---------|--------|
| [reporting/REPORT_LAYERS.md](./reporting/REPORT_LAYERS.md) | Phase D2–E — report layers, bake, cache | Not started |

---

## Archive (historical — do not use for new work)

| Doc | Purpose | Status |
|-----|---------|--------|
| [archive/REQUIREMENTS_PRE_WEBAPP.md](./archive/REQUIREMENTS_PRE_WEBAPP.md) | Pre–web-app script analysis (Apr 2026 sample CSV, keyword rules) | **Superseded** by [PRODUCT_CHARTER.md](./product/PRODUCT_CHARTER.md) |
| [archive/PIPELINE_EXCEL_SYNC.md](./archive/PIPELINE_EXCEL_SYNC.md) | Cancelled Excel export/sync idea | **Superseded** by SQLite-only lookups |

---

## Quick paths

| I want to… | Read |
|------------|------|
| Decide if a feature fits the product | [product/PRODUCT_CHARTER.md](./product/PRODUCT_CHARTER.md) |
| See what’s done / next | [product/ROADMAP.md](./product/ROADMAP.md) |
| Start a new AI coding session | `@docs/product/AI_SESSION_CONTEXT.md` + `@docs/product/PRODUCT_CHARTER.md` |
| Install and run locally | Root [README.md](../README.md) → [setup/LLM_SETUP.md](./setup/LLM_SETUP.md) |
| Understand where AI runs | [product/PRODUCT_CHARTER.md](./product/PRODUCT_CHARTER.md) §5.5 |
| CSV import & output columns | [pipeline/IMPORT_PROCESS_UPLOAD.md](./pipeline/IMPORT_PROCESS_UPLOAD.md) |
| Onboard as a human (full doc map) | [INDEX.md](./INDEX.md) |

---

## Relevance review (Jun 2026)

All docs above remain **relevant** except:

- **archive/REQUIREMENTS_PRE_WEBAPP.md** — historical; keyword-rule examples; do not copy into code.
- **archive/PIPELINE_EXCEL_SYNC.md** — cancelled; lookups are SQLite-only.
- **product/AGENTIC_AI_DESIGN.md** — aspirational; useful for long-term direction, not current sprint scope. References `docs/MCP_SETUP.md` which was never written.
- **chat/CHAT_TIER3_SAVE_REPORT.md** — largely absorbed by CHAT_CUSTOM_REPORTS; kept as Tier 3 checklist detail until merged or deleted in a future cleanup.

When adding a doc, place it in the folder that matches its primary area and add one row to this index.
