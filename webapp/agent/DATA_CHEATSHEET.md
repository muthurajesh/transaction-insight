# Transaction Insight — Web DB cheat sheet (for chat)

Use this when answering questions. **Never invent dollar amounts** — call tools.

## Primary tool: `query_sql`

Write read-only `SELECT` against `transactions`. Example:

```sql
SELECT date, amount, merchant_key, ai_sub_category
FROM transactions
WHERE budget_month = '2026-04'
  AND ai_category = 'Insurance'
  AND flow_type = 'Expense'
  AND amount < 0
ORDER BY date
```

Other tables (`merchant_labels`, `chat_messages`, …) are optional; most user questions are about `transactions`.

## What the web app stores

SQLite database (`data/finance.db`) populated by **Run processing** (same pipeline as CLI).

| Table | Purpose |
|-------|---------|
| `transactions` | All processed rows from CSV exports |
| `merchant_labels` | Confirmed / review labels per merchant (from processing + review inbox) |
| `ingested_files` | Scan-inbox history (hash per CSV); optional |
| `chat_messages` | Chat history |

Lookup rules and description cache live in **`scripts/transaction-lookups.xlsx`** (used during processing, not queried by chat tools).

## `transactions` — key columns

| Column | Meaning | Chat notes |
|--------|---------|------------|
| `date` | Transaction date (`YYYY-MM-DD`) | Calendar date |
| `budget_month` | `YYYY-MM` for monthly rollups | **Use this for month questions** |
| `amount` | Signed: income positive, expenses negative | |
| `flow_type` | `Income`, `Expense`, `Transfer`, `Adjustment` | Filter income/expense |
| `source_category` | Bank export category | Raw from CSV |
| `merchant_key` | Merchant identifier (≈ generated description) | Grouping key |
| `ai_category` | Normalized top-level category | e.g. Groceries, Automotive Expenses |
| `ai_sub_category` | Bill/spend type (not merchant name) | e.g. Towing, Fuel |
| `expense_type` | `Fixed` or `Variable` | |
| `classification` | `Personal` or `Business` | |
| `source_file` | CSV filename that produced the row | |
| `label_status` | `confirmed`, `needs_review`, `pending` | Review queue |
| `rationale` | `pipeline`, `user confirmed`, etc. | |

**Not in DB (CLI Excel only):** `Income Attribution Month`, `Payroll Spillover`, `Include in Spend?` — spillover is already applied into `budget_month` during processing.

## Payroll spillover (already baked in)

Paychecks in the **last 7 days** of a month (`PAYROLL_SPILLOVER_DAYS`) are attributed to the **next** `budget_month` during Run processing.

- Chat income for April = rows where `flow_type = Income` AND `budget_month = 2026-04`
- Do not try to re-apply spillover in chat

## Expense totals (tool semantics)

Expense tools sum **negative outflows only** (`flow_type = Expense` AND `amount < 0`), matching CLI spend rules. Internal transfers and CC payments are excluded from spend via pipeline classification.

## Full vs partial months

| Type | Rule | Example |
|------|------|---------|
| **Full month** | ≥ 20 transactions in `budget_month` | 2026-04 (228 tx) |
| **Partial month** | Few rows, often payroll spillover only | 2026-05 (1 tx) |

For “what months do I have?” or monthly tables, prefer **`full_months`** from `available_months`. Mention partial months only if the user asks.

## Tools — when to use which

| Tool | Use when user asks… |
|------|---------------------|
| **`query_sql`** | **Most questions** — lists, filters, custom totals, ad-hoc analysis |
| `save_custom_report` | User wants to **save** a query for reuse (writes `custom_reports` only) |
| `list_custom_reports` | “Show my saved reports / custom queries” |
| `run_custom_report` | Re-run a saved report with new `month`, `months`, `limit`, etc. |
| `delete_custom_report` | Remove a saved report |
| `flow_totals_by_month` | Each/all/every month income or spending; monthly breakdown |
| `month_total` | One specific month total (`month` optional → latest full month) |
| `available_months` | What months exist, date range of data |
| `top_categories` | Top spending categories for one month |
| `month_vs_avg` | How a month compares to average spending |
| `list_outliers` | Unusual category spend vs history |
| `list_transactions` | **List individual rows** for a month (optional `category` filter) |

### Examples → tool

| User question | Tool + args |
|---------------|-------------|
| “Show each month income” | `flow_totals_by_month` `{flow: Income}` |
| “How much did I spend in March?” | `month_total` `{month: 2026-03, flow: Expense}` |
| “What months are loaded?” | `available_months` `{}` |
| “Top categories last month” | `top_categories` `{month: <latest full>}` |
| “Show all Insurance transactions for April 2026” | `list_transactions` `{month: 2026-04, category: Insurance}` |
| “Save top 10 expenses as a report” | `save_custom_report` with parameterized SQL + name |
| “Run Top 10 Expenses for March 2026” | `run_custom_report` `{report: "Top 10 Expenses", params: {month: "2026-03"}}` |

## `custom_reports` table (AI may write here)

| Column | Meaning |
|--------|---------|
| `report_id` | Stable id |
| `name` | Display name (unique enough to find by name) |
| `sql_template` | Read-only `SELECT` with `:month`, `:months`, `:limit`, `:category` |
| `parameters_json` | e.g. `["month", "limit"]` |
| `original_question` | What the user asked when saving |

Example template — **top N expenses for one month**:

```sql
SELECT date, merchant_key, amount, ai_category, ai_sub_category
FROM transactions
WHERE budget_month = :month
  AND flow_type = 'Expense' AND amount < 0
ORDER BY amount ASC
LIMIT :limit
```

Multi-month: `WHERE budget_month IN (SELECT value FROM json_each(:months))` with `months` = `["2026-03","2026-04"]`.

## Workflow (user-facing)

1. Drop CSV in `input/`
2. **Run processing** (not scan alone) — full AI pipeline + DB save
3. CSV moves to `processed/` after success
4. Chat queries the DB via tools

**Scan inbox** = raw import only, no AI categories. Prefer Run processing.

## Answer style

- Use tools first; format results as markdown tables for multi-month data
- State which months are included (full exports vs partial)
- If data is missing, say “run processing on the CSV for that month” — do not guess
- Large one-off income (transfers, bonuses) may appear in totals; flag if unusually high vs ~$6.6k payroll pattern
