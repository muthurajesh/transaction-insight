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

SQLite database (`data/finance.db`) populated by **Run processing** in the web app.

| Table | Purpose |
|-------|---------|
| `transactions` | All processed rows from CSV exports |
| `merchant_labels` | Confirmed labels per merchant (from processing + Confirm Categories) |
| `description_lookup`, `category_rules`, `pipeline_custom_rules`, `cadence_rules` | Pipeline lookups (default source with `LOOKUP_SOURCE=db`) |
| `ingested_files` | Scan-inbox history (hash per CSV); optional |
| `chat_messages` | Chat history |

Optional seed/backup: **`scripts/transaction-lookups.xlsx`** — imported when DB lookup tables are empty; not queried directly by chat tools.

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

**Not in DB:** legacy Excel-only columns (`Income Attribution Month`, `Payroll Spillover`, `Include in Spend?`) — spillover is already applied into `budget_month` during Run processing.

## Payroll spillover (already baked in)

Paychecks in the **last 7 days** of a month (`PAYROLL_SPILLOVER_DAYS`) are attributed to the **next** `budget_month` during Run processing.

- Chat income for April = rows where `flow_type = Income` AND `budget_month = 2026-04`
- Do not try to re-apply spillover in chat

## Expense totals (tool semantics)

Expense tools sum **negative outflows only** (`flow_type = Expense` AND `amount < 0`), matching pipeline spend rules. Internal transfers and CC payments are excluded from spend via pipeline classification.

## Full vs partial months

| Type | Rule | Example |
|------|------|---------|
| **Full month** | ≥ 20 transactions in `budget_month` | 2026-04 (200+ tx) |
| **Partial month** | Few rows, often payroll spillover only | 2026-05 (1 tx) |

For “what months do I have?” or monthly tables, prefer **`full_months`** from `available_months`. Mention partial months only if the user asks.

## Chat tools (query_sql-first)

Chat uses **`query_sql`** for almost all analysis. The LLM writes read-only SELECT; results are validated (e.g. compare questions must not merge months).

| Tool | Use when |
|------|----------|
| **`query_sql`** | Spending, categories, merchants, comparisons, averages, trends, lists |
| `list_custom_reports` / `run_custom_report` | Saved reports from Settings |
| `propose_cadence_rule` | User explains annual/recurring charge treatment (UI confirm) |

Legacy helper tools (`month_total`, `top_categories`, …) exist for non-chat code paths — **not exposed in Chat**.

### Examples → query_sql

| User question | Approach |
|---------------|----------|
| “Compare April and May 2026 by category” | Pivot with `CASE WHEN budget_month=…` or `GROUP BY ai_category, budget_month` |
| “How much did I spend in March?” | `WHERE budget_month='2026-03' AND flow_type='Expense' AND amount<0` |
| “Show each month income” | `GROUP BY budget_month` with `flow_type='Income'` |
| “Top categories in April” | `GROUP BY ai_category` for one `budget_month` |
| “List Insurance transactions in April” | `SELECT … WHERE budget_month=… AND ai_category='Insurance'` |

### Compare months (do not combine)

```sql
SELECT ai_category,
  ROUND(SUM(CASE WHEN budget_month='2026-04' THEN -amount ELSE 0 END), 2) AS apr_2026,
  ROUND(SUM(CASE WHEN budget_month='2026-05' THEN -amount ELSE 0 END), 2) AS may_2026
FROM transactions
WHERE flow_type='Expense' AND amount<0 AND budget_month IN ('2026-04','2026-05')
GROUP BY ai_category
```

Wrong for compare: `GROUP BY ai_category` only with `budget_month IN (...)` — merges months.

## Non-chat analytics helpers

These remain available to the app (not Chat):

| Tool | Use when |
|------|----------|
| `flow_totals_by_month` | Monthly income/expense totals across all months |
| `month_total` | One month total (optional cadence `expense_view`) |
| `top_categories` | Top categories for one month |
| `available_months` | What months exist in DB |

## Expense cadence views (`expense_view`)

| View | Meaning |
|------|---------|
| `cash` | Raw bank outflows (`SUM(-amount)`) — default |
| `core` | Run-rate only — excludes annual/lump/one-time per cadence rules |
| `normalized` | Monthly equivalent — spreads yearly/semi-annual charges |

Use on `month_total`, `top_categories`, `flow_totals_by_month` when `flow=Expense`.
Raw `query_sql` cannot apply per-row cadence — use helper tools instead.

## `custom_reports` table (AI may write here)

| Column | Meaning |
|--------|---------|
| `report_id` | Stable id |
| `name` | Display name (unique enough to find by name) |
| `sql_template` | Read-only `SELECT` with `:month`, `:months`, `:limit`, `:category` (SQL sums are **cash** only; `:expense_view` is stored for metadata — use helper tools for normalized/core) |
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

**Scan inbox** = legacy raw import only (no AI). Use **Import & Categorize** → choose CSV, which uploads and runs processing.

## Answer style

- Use tools first; format results as markdown tables for multi-month data
- State which months are included (full exports vs partial)
- If data is missing, say “run processing on the CSV for that month” — do not guess
- Large one-off income (transfers, bonuses) may appear in totals; flag if unusually high vs ~$6.6k payroll pattern
