# Custom reports — Settings UI

**Status:** Not implemented (partial backend)  
**Related:** [CHAT_TIER3_SAVE_REPORT.md](CHAT_TIER3_SAVE_REPORT.md), Phase B `:expense_view`

## Problem

Saved reports work **via chat only**:

- `save_custom_report`, `list_custom_reports`, `run_custom_report`, `delete_custom_report`

Users who save from chat Tier 3 need a **non-chat** place to list, run, rename, delete reports.

## Goal

**Settings** tab (or sub-panel): manage `custom_reports` without typing chat commands.

## What exists

| Piece | Location |
|-------|----------|
| `custom_reports` table | `webapp/db/schema.py` |
| CRUD logic | `webapp/services/custom_reports.py` |
| Chat tools | `webapp/agent/tools.py` |
| Allowed params | `:month`, `:months`, `:limit`, `:category` (no `expense_view` yet) |

## UI requirements

### List view

Table columns:

| Column | Source |
|--------|--------|
| Name | `name` |
| Description | `description` |
| Parameters | `parameters` joined |
| Created | `created_at` |
| Actions | Run, Delete |

### Run action

- Modal: parameter inputs based on `parameters` array
  - `month` → month picker `YYYY-MM`
  - `limit` → number
  - `category` → text or dropdown from distinct categories
  - `expense_view` → select (when Phase B adds param)
- **Run** → `POST /api/custom-reports/{id}/run` with body params
- Result: show table in modal or navigate to Chat with prefilled “run report X”

### Delete

- Confirm dialog → `DELETE /api/custom-reports/{id}`

### Create (optional v1)

Defer full SQL editor. v1: **only list/delete/run**; create via chat or Tier 3 Save button.

## API additions

| Method | Path | Body |
|--------|------|------|
| GET | `/api/custom-reports` | — |
| POST | `/api/custom-reports/{id}/run` | `{ "month": "2026-04", "limit": 10 }` |
| DELETE | `/api/custom-reports/{id}` | — |

`POST /api/custom-reports` for Tier 3 save — see CHAT_TIER3_SAVE_REPORT.

## Files

| File | Change |
|------|--------|
| `webapp/main.py` | REST endpoints |
| `webapp/static/index.html` | Settings section markup |
| `webapp/static/app.js` | Load list, run modal, delete |
| `webapp/static/styles.css` | Table + modal |

## Verification

1. Save report via chat (or manual DB seed)
2. Settings → list shows report
3. Run with different month → table matches chat `run_custom_report`
4. Delete → gone from list and chat list

## Context

Roadmap §6 “UI to manage saved reports”; user wanted saved top-10 query runnable for other months without re-asking AI.
