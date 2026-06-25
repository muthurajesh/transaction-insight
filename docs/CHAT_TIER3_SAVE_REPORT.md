# Chat Tier 3 — Save as report (table replies)

**Status:** Partially implemented (save/tweak/manage in Chat — see [CHAT_CUSTOM_REPORTS.md](./CHAT_CUSTOM_REPORTS.md))  
**Detail parent:** [CHAT_RICH_UI.md](./CHAT_RICH_UI.md) § Tier 3

## User intent (from project chat)

> “Give me last month top 10 expenses — I want the AI to **save** that query to a table. Later: show all custom reports, run for a different month.”

Backend for this **already exists** via chat tools + `custom_reports` table. Tier 3 adds a **UI button** on table/chart messages so users need not ask the model to save.

## What exists today

| Piece | Location |
|-------|----------|
| `custom_reports` table | `webapp/db/schema.py` |
| `save_custom_report`, `list_custom_reports`, `run_custom_report`, `delete_custom_report` | `webapp/services/custom_reports.py` |
| Chat tools | `webapp/agent/tools.py` |
| Allowed SQL params | `:month`, `:months`, `:limit`, `:category` |
| Table display + CSV export | `mountTableDisplay()` in `app.js` |

## UX requirements

### Save button placement

On assistant messages that have `display.type === "table"` (and optionally `"chart"`):

- Add **“Save as report”** next to existing **“Download CSV”** in `.chat-display-toolbar`
- Only show when `tool_trace` contains a saveable query (see below)

### Save flow

1. User clicks **Save as report**
2. Modal or inline prompt:
   - **Name** (required, e.g. “Top 10 expenses by category”)
   - **Description** (optional)
   - **Parameters** — checkboxes for which params to expose: `month`, `months`, `limit`, `category`
3. POST to new REST endpoint **or** reuse chat-less API:

```http
POST /api/custom-reports
Content-Type: application/json

{
  "name": "Top 10 expense categories",
  "sql_template": "SELECT ... WHERE budget_month = :month ... LIMIT :limit",
  "description": "From chat table 2026-06-04",
  "parameters": ["month", "limit"]
}
```

4. Success toast: “Saved. Ask chat: run report Top 10 expense categories for 2026-03”

### SQL source priority (implementer picks one)

| Priority | Source | Notes |
|----------|--------|-------|
| 1 | Last `tool_trace` entry where `tool === "query_sql"` | Use `args.sql` |
| 2 | Last `run_custom_report` | Clone existing template + new name |
| 3 | Synthesize from `list_transactions` / `top_categories` args | Map to parameterized template (harder) |

**Recommended for v1:** Only enable Save when trace includes `query_sql` or `run_custom_report`. Otherwise show: “Ask chat to save this query” with prefilled `save_custom_report` prompt.

### Pre-built templates (fallback)

For `top_categories` / `list_transactions` traces without SQL, offer canned templates:

```sql
-- top_categories template
SELECT ai_category AS category, COUNT(*) AS transaction_count, SUM(-amount) AS spend
FROM transactions
WHERE budget_month = :month AND flow_type = 'Expense' AND amount < 0
GROUP BY ai_category ORDER BY spend DESC LIMIT :limit
```

Store `original_question` from last user message in thread.

## API additions

| Endpoint | Purpose |
|----------|---------|
| `POST /api/custom-reports` | Body mirrors `save_custom_report` fields; calls `custom_reports.save_custom_report` |
| `GET /api/custom-reports` | List (for Settings UI later) |
| `DELETE /api/custom-reports/{id}` | Optional in Tier 3 |

Chat tools remain; REST is for UI button.

## Files to change

| File | Change |
|------|--------|
| `webapp/main.py` | `POST/GET/DELETE /api/custom-reports` if not present |
| `webapp/static/app.js` | Save button in `mountTableDisplay`; modal; `saveReportFromMessage(msg)` |
| `webapp/static/index.html` | Modal markup (or dynamic) |
| `webapp/static/styles.css` | Modal + toolbar button |
| `docs/CHAT_RICH_UI.md` | Mark Tier 3 save-as-report done |

## Multiline composer (same tier, separate item)

See CHAT_RICH_UI § Tier 3:

- Replace `#chat-input` `<input>` with `<textarea>`
- Enter → send; Shift+Enter → newline
- No backend changes

## Verification

1. Chat: run a `query_sql` question → table appears
2. Click **Save as report** → name → saved
3. Chat: “list my custom reports” → new report listed
4. Chat: “run Top 10 … for 2026-03” → works

## Out of scope

- Streaming tokens (defer)
- Normalized `expense_view` in saved SQL until Phase B helper exists
