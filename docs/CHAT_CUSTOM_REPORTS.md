# Chat custom reports (v1)

**Status:** Implemented  
**Related:** [CHAT_TIER3_SAVE_REPORT.md](./CHAT_TIER3_SAVE_REPORT.md), [CUSTOM_REPORTS_UI.md](./CUSTOM_REPORTS_UI.md), [REPORT_LAYERS.md](./REPORT_LAYERS.md)

## Goal

Build saved analytics **in app chat** through multi-turn conversation (Chain of Thought), then persist to SQLite for fast reruns, tweaks, rename, delete, and versioning.

## User workflow

1. **Explore** — ask spending questions; assistant uses `query_sql` and shows tables/charts.
2. **Refine** — exclude business, drill into categories, discuss annual vs monthly (cadence confirm when needed).
3. **Save** — click **Save as report** on a table result. The server distills a **report prompt** from chat context + validated SQL.
4. **Run** — “Run my custom report *Name* for 2026-06” or Help → **Run saved report**.
5. **Tweak** — **Tweak in chat** on a report result, or Help → **Tweak saved report** (one-off `query_sql`; does not overwrite unless you save a new version).
6. **Manage** — **Manage** on a report result: rename, delete, **Save as new version**.

## What is stored (`custom_reports`)

| Column | Purpose |
|--------|---------|
| `report_prompt` | Human + LLM-readable spec (filters, view, exclusions, output) |
| `sql_template` | Deterministic fast rerun (`:month`, `:months`, `:limit`, `:category`, `:expense_view`) |
| `report_config_json` | Display options (`show_grand_total`, `chart.enabled`, `expense_view` metadata) |
| `parent_report_id` / `version` | Version lineage when forking |
| `original_question` | Last user message when saved |

**Run modes:**

- **Fast** — `run_custom_report` executes SQL (Settings REST or chat tool).
- **Tweak** — chat loads `report_prompt` + runs report, then ad-hoc SQL.

Normalized/cadence-heavy rules: use chat + `propose_cadence_rule` today; full layered runner remains in [REPORT_LAYERS.md](./REPORT_LAYERS.md).

## REST API

| Method | Path |
|--------|------|
| GET | `/api/custom-reports` |
| GET | `/api/custom-reports/{id}` |
| POST | `/api/custom-reports/finalize` — distill prompt from conversation + SQL |
| POST | `/api/custom-reports` — create |
| PATCH | `/api/custom-reports/{id}` — rename / update |
| POST | `/api/custom-reports/{id}/fork` — new version |
| POST | `/api/custom-reports/{id}/run` |
| DELETE | `/api/custom-reports/{id}` |

## Chat Help

Help panel includes: **Build custom report**, **Run saved report**, **Tweak saved report**, **Last 3 months**, **Unusual categories**, **New patterns**, plus **Saved reports** list.

## Display

- Category rollups: **grand total** in summary line.
- Optional **bar chart** when `report_config.chart.enabled` or heuristic (≤12 category rows).
- **Save as report** / **Tweak** / **Manage** buttons on assistant table messages.

## Out of scope (v1)

- Settings tab list UI ([CUSTOM_REPORTS_UI.md](./CUSTOM_REPORTS_UI.md))
- `report_layers` / `run_layered_report` ([REPORT_LAYERS.md](./REPORT_LAYERS.md))
- Bake / materialized cache

## Files

| Area | Files |
|------|-------|
| Schema | `webapp/db/schema.py` |
| Service | `webapp/services/custom_reports.py` |
| API | `webapp/main.py` |
| Chat | `webapp/agent/chat.py`, `webapp/agent/display.py` |
| UI | `webapp/static/app.js`, `index.html`, `styles.css` |
| Tests | `tests/test_custom_reports.py` |
