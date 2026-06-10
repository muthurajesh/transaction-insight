# Transaction Insight

Turn raw monthly bank transaction exports into a structured Excel workbook with AI-assisted categorization, income/expense separation, and fixed vs. variable cost labeling.

## What we are trying to do

Personal finance apps export transactions as flat CSV files. Useful analysis—seeing true income vs. spending, grouping by category, and understanding which costs are **fixed** (recurring obligations) vs. **variable** (discretionary)—requires extra structure that the export does not provide.

This project automates that enrichment:

1. **Preserve the source data** in Excel for auditability.
2. **Split transactions** into Income and Expenses on separate tabs.
3. **Add categories and sub-categories** using AI that reads descriptions, amounts, accounts, and existing labels.
4. **Label cost behavior** with **Type** (Fixed or Variable) and **Sub-Type** (reserved for future use). Nuance: mortgage and monthly utilities are fixed; groceries and dining are variable.
5. **Produce one workbook** you can filter, pivot, or chart in Excel.

## Two workflows

| Workflow | Entry point | Output |
|----------|-------------|--------|
| **Excel CLI** (unchanged logic) | `scripts/process_transactions.py` | Per-run workbook in `output/`; state files in `scripts/` |
| **Web app** (AI-first, local) | `webapp/` + browser | SQLite `data/finance.db`, Review Inbox, chat analytics |

### Where files live (CLI)

| Path | Role |
|------|------|
| `input/*.csv` | Bank exports you process |
| `processed/*.csv` | Archived inbox CSVs after a successful run (web app or CLI) |
| `output/<csv-stem>.xlsx` | **Per-run report** for that CSV (Raw Data, Income, Expenses, Summary, …) |
| `scripts/transaction-lookups.xlsx` | **Processing state** — rules, description cache, custom rules; read/updated every run |
| `scripts/transaction-history.xlsx` | **Rolling ledger** — optional; **read** for multi-month cadence detection when the file exists; **written** only with `--update-history` (not the same as a single export in `output/`) |

The CLI and web app are separate: the web app does not import or modify the CLI script. Parsers and math are reimplemented by reference under `webapp/`.

## Web app (local AI-first)

1. Copy a bank CSV into `input/` (same folder the CLI reads from).
2. **First time only** — create the virtualenv and install dependencies (see [Setup](#setup) below), or from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/.env.example config/.env   # then edit LLM settings if needed
```

3. **Every time you use the web app** — activate the venv and start the server (no need to run `pip install` again unless `requirements.txt` changed):

```bash
source .venv/bin/activate
uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000
```

4. Open http://127.0.0.1:8000 — **Import & Categorize** → **Run processing** (same pipeline as the CLI script).
5. Use **Review Inbox** for uncertain merchants (one fix updates all matching rows).
6. Use **Chat** for questions like “How much did I spend in 2026-04?” (dollar amounts use CLI spend rules: negative outflows only).

**Shared core:** [`transaction_insight/`](transaction_insight/) holds the processing logic; [`scripts/process_transactions.py`](scripts/process_transactions.py) is the CLI entry; the web app calls [`transaction_insight/pipeline.py`](transaction_insight/pipeline.py).

Configure the same Ollama/LM Studio API via `config/.env` (`LLM_BASE_URL`, `LLM_MODEL`). Optional: `FINANCE_DB_PATH`, `FINANCE_INBOX_DIR` (defaults to `input/`).

## What the CLI script does

`scripts/process_transactions.py` reads a CSV (e.g. `ExportData-April-2026.csv`) and writes an `.xlsx` file with three sheets:

| Sheet      | Contents |
|-----------|----------|
| **Raw Data** | Original CSV columns unchanged |
| **Income**   | Transactions classified as income, plus AI columns |
| **Expenses** | Transactions classified as expenses, plus AI columns |

### Columns added (Income & Expenses tabs)

| Column           | Description |
|------------------|-------------|
| Section          | `Income` or `Expense` (also used to route rows to the correct tab) |
| AI Category      | Top-level category (e.g. Groceries, Utilities, Dining) |
| AI Sub-Category  | Finer label (e.g. Electric bill, Fast food) |
| Type             | `Fixed` or `Variable` — recurring obligation vs. discretionary/fluctuating spend |
| Sub-Type         | Empty for now; reserved for future breakdown |

Original CSV columns (Date, Amount, Category, descriptions, account, etc.) are kept alongside these fields.

### Fixed vs. variable (how AI is guided)

- **Fixed**: Recurring obligations you expect every month, even if the dollar amount moves slightly—mortgage/rent, HOA, utilities (electric, gas, water), insurance, phone/internet, subscriptions, gym, loan payments.
- **Variable**: Spending that changes with choices and habits—groceries, restaurants, fuel, general shopping, entertainment, one-off purchases.

Credit card payments and internal transfers are treated as expenses (money movement), not income.

**Refunds vs income:** Only **Paychecks/Salary** and **Interest** count as **Income** when the amount is positive. A **positive** amount on a normal spending category (e.g. **Dues and Subscriptions** refund for JetBrains) goes to **Adjustments**, not Income — so you should **not** change CategoryRules for the whole category. Negative charges on the same category stay on **Expenses**.

## LLM setup (LM Studio — recommended)

The script talks to **LM Studio’s OpenAI-compatible API** on your LAN. No cloud API key is required when authentication is off in Server Settings.

1. Start LM Studio and load a model (see recommendations below).
2. Enable **Serve on Local Network** (port `1234` in your setup).
3. Copy `config/.env.example` to `config/.env` (or `cp config/.env.ollama config/.env` / `cp config/.env.lmstudio config/.env`) and adjust if needed:

```bash
cp config/.env.example config/.env
```

Default `config/.env` values match this setup:

| Setting | Example |
|---------|---------|
| `LM_STUDIO_BASE_URL` | `http://192.168.0.7:1234/v1` |
| `LM_STUDIO_MODEL` | `qwen2.5-coder-32b-instruct` |

### Which model to use?

| Model | Verdict |
|-------|---------|
| **qwen2.5-coder-32b-instruct** | **Best quality** for nuanced rules (income vs. credit-card payments, fixed vs. variable). Slower/heavier; good default if RAM allows. |
| **qwen2.5-14b-instruct-mlx** | **Best speed/quality balance** on Apple Silicon (MLX). Slightly less nuanced than 32B; try this if runs feel slow. |
| **qwen2.5:7b-instruct** (Ollama) | **Fastest** for repeat runs; budget totals matched 14B in testing (~227 rows). |
| **openai/gpt-oss-20b** | Usable backup; less consistent JSON than Qwen instruct models. |
| **dolphin-2.8-mistral-7b-v02** | Too small for reliable batch classification. |
| **qwen2.5-vl-7b-instruct-abliterated** | Vision model — **not suitable** for text-only CSV work. |

You do **not** need a “coder” model for this task, but **qwen2.5-coder-32b-instruct** works well because it follows structured JSON instructions reliably.

## LLM setup (Ollama)

Ollama exposes the same **OpenAI-compatible** API the script already uses. No code changes beyond configuration.

1. Install and start Ollama (the app usually runs the server automatically).
2. Pull a model (names use `ollama` tags, not LM Studio folder names):

```bash
ollama pull qwen2.5:14b
ollama list          # copy the exact NAME column value
```

3. In `config/.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen2.5:14b
```

4. Run:

```bash
python scripts/process_transactions.py --provider ollama
```

**CLI only (no `.env` edit):**

```bash
python scripts/process_transactions.py \
  --provider ollama \
  --base-url http://127.0.0.1:11434/v1 \
  --model qwen2.5:14b
```

**Another machine on your LAN** (if Ollama listens on the network):

```bash
OLLAMA_BASE_URL=http://192.168.0.7:11434/v1
```

Verify the server: `curl http://127.0.0.1:11434/v1/models`

| Topic | Notes |
|-------|--------|
| Model names | Use `ollama list` — e.g. `qwen2.5:14b`, `llama3.1:8b`, not LM Studio ids |
| API key | Not required; the script sends a dummy key Ollama ignores |
| JSON mode | Same as LM Studio — plain text JSON in the reply (already handled) |
| Speed | Often faster to start than LM Studio; quality depends on the pulled model |

Good Ollama picks for this task: `qwen2.5:14b`, `qwen2.5:7b` (faster), or `llama3.1:8b`.

### OpenAI cloud (optional)

Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in `config/.env`, or pass `--provider openai`.

## Setup

```bash
cd "Transaction Insight"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/.env.example config/.env
```

## Usage

```bash
# Default: LM Studio on 192.168.0.7:1234, model from config/.env
# Uses input/*.csv and writes output/<stem>.xlsx
python scripts/process_transactions.py

# Explicit paths
python scripts/process_transactions.py --input input/ExportData-April-2026.csv --output output/April-2026-Insights.xlsx

# LM Studio: faster model on Apple Silicon (recommended over 32B for this task)
python scripts/process_transactions.py --provider lmstudio --model qwen2.5-14b-instruct-mlx

# Larger batches (M4 Max / 128GB — or set LOCAL_BATCH_SIZE=50 in .env)
python scripts/process_transactions.py --batch-size 50

# Ollama
python scripts/process_transactions.py --provider ollama --model qwen2.5:7b

# OpenAI cloud
python scripts/process_transactions.py --provider openai --model gpt-4o-mini
```

### Performance (Apple Silicon / M4 Max)

| Lever | Recommendation |
|-------|----------------|
| **Model** | `qwen2.5-14b-instruct-mlx` (LM Studio) or `qwen2.5:7b` (Ollama) — not the 32B coder unless you need max quality |
| **Description cache** | Second runs are much faster (`DescriptionLookup` in `transaction-lookups.xlsx`) |
| **Batch size** | Local LLM defaults to **40** per request (`LOCAL_BATCH_SIZE`); try **50** on 128GB RAM |
| **Dedup** | First-run description LLM dedupes identical bank text (2787 rows → far fewer unique patterns) |
| **LM Studio** | Run server on the same Mac (`127.0.0.1:1234`) if possible; use MLX models |

The script prints `Batches: descriptions=…, classification=…` at startup for local providers. Classification batches default to **25** (smaller JSON responses per row).

## Output example

For `input/ExportData-April-2026.csv`, the script creates `output/ExportData-April-2026.xlsx` with:

- **Raw Data** — 227 rows (full export)
- **Income** — payroll, interest, refunds classified as income, etc.
- **Expenses** — purchases, bills, transfers out, etc.
- **Summary** — monthly expense totals vs average, with **View categories** links to per-month breakdown (sorted by deviation, then spend)
- **Baseline** — category budget suggestions (historical average + buffer)

Transaction tabs omit `Status`, `Split Type`, `Currency`, and `Memo`. They show **Generated Description**. On **Income**, **Expenses**, and **Adjustments**, `Original Description`, `User Description`, and `Simple Description` are **hidden** (still in the file). **Raw Data** keeps all description columns visible.

Every sheet the script writes (export `.xlsx`, `transaction-lookups.xlsx`, and `transaction-history.xlsx`) gets **column autofit by default** on save.

Review AI labels and adjust in Excel as needed; the model uses transaction context but is not a substitute for your judgment on edge cases (e.g. Zelle transfers, credit card payment pairs).

## Shared lookup file (`transaction-lookups.xlsx`)

Every run uses one standard lookup workbook in `scripts/` (override with `LOOKUP_FILE` in `config/.env` or `--lookup-file`).

1. **Before descriptions** — **DescriptionLookup** is loaded so matching bank text skips the description LLM.
2. **Before classification** — category rules and merchant rows are applied (including **Business** rules when `Classification` is `Business`).
3. **After processing** — new description keys, merchants, and source categories are merged in; your existing edits are preserved.

Cumulative transaction rows live in **`transaction-history.xlsx`** (see below), not in the lookup file.

```bash
# Default: read/update transaction-lookups.xlsx
python scripts/process_transactions.py ExportData-April-2026.csv

# Custom path
python scripts/process_transactions.py data.csv --lookup-file /path/to/my-lookups.xlsx

# Skip lookup entirely (one-off run)
python scripts/process_transactions.py data.csv --skip-lookup-update
```

Lookup workbook sheets:

| Sheet | Contents |
|-------|----------|
| **Categories** | `AI Category`, `AI Sub-Category`, `Transaction Count`, `Budget Tier`, `Type`, `Sub-Type`, `Notes` |
| **CategoryRules** | Map bank `Category` → `AI Category`, `Budget Tier`, `Type`, `Sub-Type` |
| **BusinessCategoryRules** | Rules keyed by **Generated Description**. Matching rows are auto-set to `Classification = Business` (even if the CSV says Personal), then AI Category / Budget Tier / Type are applied. |
| **Types** | `Type`, `Sub-Type`, `Transaction Count`, `Notes` |
| **DescriptionLookup** | Cached **Generated Description** keyed by normalized User + Simple + Original text (`Source Key` hash). First run fills via LLM; repeat merchants skip AI. Edit `Generated Description` in Excel to override. |
| **CustomRules** | Freeform rules in column **Rule**; **Status** controls compile/apply (see below). Ollama compiles text → JSON in **Compiled Rule**; script applies on each run. |
| **ExpenseCadenceRules** | Manual **Monthly / Yearly / One-time** tags by **Generated Description** (see below). |

### Expense cadence (monthly vs yearly vs one-time)

Part 2 of the workflow: after categories are correct, separate **normal monthly run-rate** from **irregular** charges (e.g. annual insurance vs monthly premium).

**On each expense row**

| Column | Meaning |
|--------|---------|
| **Expense Cadence** | `Monthly`, `Yearly`, `One-time`, `Unplanned`, or `Unknown` |
| **In Monthly Run-Rate?** | `Y` = counts toward core monthly budget; `N` = cash spend but excluded from run-rate |
| **Cadence Source** | `Lookup` (your sheet), `Detected` (history math), or `Default` |

**ExpenseCadenceRules** (you maintain)

| Generated Description | Cadence | In Monthly Run-Rate? | Notes |
|---------------------|---------|----------------------|-------|
| State Farm Insurance | Yearly | N | Annual premium |

**Auto-detection** uses `transaction-history.xlsx` when it exists (3+ budget months). Irregular months often show up as large **vs Avg %** on the Summary category breakdown.

**Summary** (main spending view):

1. **Monthly overview** — Total Expenses per budget month, **Avg Monthly Expenses** (benchmark across all months in this workbook), **vs Avg** / **vs Avg %**.
2. Click **View categories** (hyperlink) to jump to that month’s breakdown on the same sheet.
3. **Category breakdown** — For each month (under `── YYYY-MM ──`):
   - **Month Spend** — total for that **AI Category** in that month (not the whole month).
   - **Avg Monthly Spend** — that category’s average per month across all months in this workbook.
   - **vs Avg** / **vs Avg %** — how this month’s category spend compares to that average.
   - **% of Month** — that category’s share of the month’s **total** expenses.
   Sorted by **largest |vs Avg %|** first, then **Month Spend** descending.

Use **`--update-history`** and process all CSVs into `transaction-history.xlsx` so multi-month averages and drill-down reflect every month (not just one export file).

**Baseline**: suggested monthly budget per category (avg + buffer). Month-by-month analysis lives on **Summary**.

```bash
# Build history first, then process new months (detection improves with more data)
python scripts/process_transactions.py data.csv --update-history

# Lookup-only cadence (no auto-detection)
python scripts/process_transactions.py data.csv --skip-cadence-detection
```

### CustomRules (freeform → AI → apply)

**Highest priority:** Active custom rules run in a **final pass** after CategoryRules, Categories, BusinessCategoryRules, LLM review, and expense cadence lookup. Any field they set (Category, Classification, AI Category, etc.) wins for matching rows.

| Column | Purpose |
|--------|---------|
| **Rule** | Plain-English rule you write (e.g. split Ahs charges by highest amount per month). |
| **Status** | `Pending` = compile on next run; `Active` = apply; `Error` = fix rule and set `Pending`; `Disabled` = ignore. |
| **Compiled Rule** | Filled by script (JSON). Do not edit unless you know the format. |
| **Last Error** | Set if compilation fails. |
| **Updated At** | Last compile time. |

Example **Rule** text:

`If Generated Description is "Ahs Ahs.Com" and there are multiple entries in a month, the highest amount is Insurance / Utilities / Appliance Insurance; the others are Business Expenses with Classification Business, AI Category Rental, AI Sub-Category Appliance Insurance.`

Set **Status** to `Pending`, run the processor; when **Status** becomes `Active`, re-run (or same run continues) to apply.

For merchant **and** dollar amount (e.g. Apple warranty at $9.99 only), include both in **Rule**:

`If Generated Description is "Apple" and Amount is 9.99, Category = Business Expenses, ...`

Only rows matching **both** are updated (amount compares absolute value, so -9.99 expenses match).

Description lookup order per row: **cache hit** → non-empty **User Description** (saved to cache) → **LLM** (saved to cache) → heuristic fallback if the model fails.

```bash
# Re-run description LLM for this file and overwrite matching cache entries
python scripts/process_transactions.py data.csv --rebuild-description-lookup
```

Cannot be combined with `--skip-lookup-update`.

## Cumulative history (`scripts/transaction-history.xlsx`)

Separate from per-run files in `output/` and from `transaction-lookups.xlsx`. This workbook is **not** your monthly export; it is a rolling **Income**, **Expenses**, **Adjustments**, **Summary**, and **Baseline** across all CSV runs you merge in.

- **Read on every run** (when the file exists): expense cadence auto-detection uses prior months from history plus the current CSV.
- **Written only with `--update-history`**: off by default so accidental runs do not change history.

Keep it in `scripts/` with the lookup file (processing state), not in `output/` (human-facing reports).

- Each transaction gets a stable **`Transaction ID`** (hash of date, amount, account, original description).
- Re-processing the **same CSV** updates existing rows instead of duplicating them.
- Processing a **new** CSV appends new IDs.

```bash
# History is NOT updated unless you opt in (avoids accidental overwrites)
python scripts/process_transactions.py data.csv --update-history

# Custom history path (with --update-history)
python scripts/process_transactions.py data.csv --update-history --history-file /path/to/history.xlsx
```

To migrate from an older per-export `*-lookups.xlsx`, copy or rename it to `scripts/transaction-lookups.xlsx`. If you had **ProcessedTransactions** in that file, it is no longer used — run once to populate `scripts/transaction-history.xlsx` instead.

## Project files

| File / folder | Purpose |
|---------------|---------|
| `scripts/process_transactions.py` | Main Excel processor |
| `scripts/transaction-lookups.xlsx` | Shared lookup workbook (created on first run) |
| `scripts/transaction-history.xlsx` | Cumulative history (`--update-history`) |
| `input/` | Bank CSV exports (CLI default input; web app scan folder) |
| `processed/` | Archived CSVs after successful Run processing (web app or CLI) |
| `output/` | Generated Excel workbooks from CLI |
| `config/` | `.env` and presets (`.env.example`, `.env.ollama`, `.env.lmstudio`) — used by CLI and web app |
| `webapp/` | Local FastAPI app: ingest, AI merchant labels, review UI, chat |
| `data/finance.db` | Web app SQLite database (gitignored) |
| `requirements.txt` | Python dependencies |
| `README.md` | This document |
| `docs/ROADMAP.md` | Master plan — phases, checkboxes, links to detail docs |
