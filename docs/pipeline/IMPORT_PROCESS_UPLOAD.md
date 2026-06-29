# Import & Categorize — upload and process

**Status:** Shipped  
**UI:** **Workspace** import strip (default, `UI_AGENT_WORKSPACE=1`) or **Import & Categorize** tab (legacy)

## Flow

1. User clicks **Choose CSV files** (multipart upload → `input/`)
2. Upload completes → **Run processing** starts automatically (SSE progress)
3. Pipeline categorizes rows and saves to `finance.db`; CSV moves to `processed/`

**Run processing** button re-runs the pipeline on any CSV still in `input/` (e.g. manually copied exports).

[`webapp/pipeline/run.py`](../../webapp/pipeline/run.py) orchestrates [`webapp/processing/`](../../webapp/processing/) (parse, LLM, rules, cadence). Lookups load from SQLite before processing and save back after each run — [PIPELINE_DB_LOOKUPS.md](PIPELINE_DB_LOOKUPS.md).

## Run processing output

Each CSV is loaded, enriched by the pipeline, and saved to `finance.db`. Original CSV columns (Date, Amount, Category, descriptions, account, etc.) are kept alongside enriched fields.

### Columns added (Income & Expenses)

| Column | Description |
|--------|-------------|
| Section | `Income`, `Expense`, or `Adjustment` |
| AI Category | Top-level category (LLM- or user-assigned; no fixed list in code) |
| AI Sub-Category | Finer label under the category |
| Type | `Fixed` or `Variable` |
| Sub-Type | Empty for now; reserved for future breakdown |
| Expense Cadence | `Monthly`, `Yearly`, `One-time`, etc. — see [cadence/EXPENSE_CADENCE.md](../cadence/EXPENSE_CADENCE.md) |

### Fixed vs variable (how AI is guided)

The LLM assigns **Fixed** vs **Variable** from transaction context (recurring obligations vs discretionary spend). There is no hardcoded category→type map in Python.

Credit card payments and internal transfers are treated as expenses (money movement), not income.

**Refunds vs income:** Only **Paychecks/Salary** and **Interest** count as **Income** when the amount is positive. A **positive** amount on a normal spending category goes to **Adjustments**, not Income.

## APIs

| Endpoint | Role |
|----------|------|
| `POST /api/ingest/upload` | Copy CSV(s) to inbox |
| `GET /api/process/stream` | SSE progress for full pipeline |
| `POST /api/process` | Non-streaming batch process |

Legacy (not used by UI):

| Endpoint | Role |
|----------|------|
| `POST /api/ingest/upload-and-scan` | Upload + raw ingest without AI |
| `POST /api/ingest/scan` | Raw ingest for files already in inbox |

## Files

| File | Role |
|------|------|
| `webapp/services/inbox_upload.py` | Save uploads to inbox |
| `webapp/services/process.py` | `run_pipeline` + SQLite save |
| `webapp/static/app.js` | `uploadCsvFiles`, `runCategorizeStream` |
| `webapp/static/index.html` | Import & process panel |
