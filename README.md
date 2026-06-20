# Transaction Insight

Turn raw monthly bank transaction exports into categorized, analyzable data with AI-assisted labels, income/expense separation, and fixed vs. variable cost tagging — all through a local web app backed by SQLite.

## What we are trying to do

Personal finance apps export transactions as flat CSV files. Useful analysis—seeing true income vs. spending, grouping by category, and understanding which costs are **fixed** (recurring obligations) vs. **variable** (discretionary)—requires extra structure that the export does not provide.

This project automates that enrichment:

1. **Preserve the source data** for auditability (stored in SQLite and optional Excel lookups).
2. **Split transactions** into Income, Expenses, and Adjustments.
3. **Add categories and sub-categories** using AI that reads descriptions, amounts, accounts, and existing labels.
4. **Label cost behavior** with **Type** (Fixed or Variable) and **Sub-Type** (reserved for future use). Nuance: mortgage and monthly utilities are fixed; groceries and dining are variable.
5. **Review, confirm, and chat** over your data in the browser.

## Web app workflow

| Step | Where |
|------|--------|
| Upload & process CSV | **Import & Categorize** → choose CSV (upload + run processing) |
| Fix uncertain merchants | **Confirm Categories** |
| Clean duplicate labels | **AI Rules** — merge synonyms (user confirms before apply) |
| Edit rows, cadence, custom rules | **Edit Transactions**, **Cadence**, Settings |
| Ask questions | **Chat** (“How much did I spend in 2026-04?”) |

**Processing core:** [`webapp/pipeline/`](webapp/pipeline/) orchestrates [`webapp/processing/`](webapp/processing/) (LLM, rules, cadence). Lookups default to **SQLite** (`LOOKUP_SOURCE=db`); Excel is optional backup (`EXPORT_LOOKUPS=1`).

Configure Ollama or LM Studio via `config/.env` (`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`). Optional: `FINANCE_DB_PATH`, `FINANCE_INBOX_DIR` (defaults to `input/`).

See [How AI is used](#how-ai-is-used) for every LLM touchpoint and how to extend behavior with rules.

### Quick start

**First time** — create the virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/.env.example config/.env   # then edit LLM settings if needed
```

**Every session** — activate the venv and start the server:

```bash
source .venv/bin/activate
uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000
```

Or use `./start.sh` if present. Open http://127.0.0.1:8000.

See [docs/CONFIRM_CATEGORIES.md](docs/CONFIRM_CATEGORIES.md) for the label queue. Chat dollar amounts use the same spend rules as the pipeline (negative outflows only).

## How AI is used

Transaction Insight mixes **deterministic code** (parsing, dedup, SQL, lookup tables, compiled rules) with **local LLM calls** where judgment is needed. The default posture is **AI proposes → you confirm → then data is saved**. Nothing in the review or taxonomy flows auto-writes without your approval.

### Design principles

| Layer | Role | Examples |
|-------|------|----------|
| **Deterministic** | Exact, repeatable | `transaction_id` hash, amount signs, `flow_type`, payroll spillover, SQL totals |
| **Lookup rules** | Your saved patterns | `category_rules`, `merchant_labels`, `description_lookup`, `cadence_rules` |
| **Compiled rules** | English → JSON, then fixed logic | **Custom Rules** in Settings (if/then on merchant, amount, description) |
| **AI judgment** | Ambiguous text, new merchants, taxonomy cleanup | Descriptions, classification, suggestions, chat, **AI Rules** proposals |
| **You** | Final authority | Confirm Categories, cadence modal, Apply on AI Rules, save Custom Rules |

Models are configured in `config/.env`. You can split **pipeline** vs **chat** models (`PIPELINE_MODEL`, `CHAT_MODEL`) — see [LLM setup](#llm-setup-ollama--common-default) below.

### Where AI runs (by tab / phase)

| When | Where in app | What the LLM does | Persists without you? |
|------|----------------|-------------------|------------------------|
| **Run processing** | Import & Categorize | **Descriptions** — bank text → merchant label. **Classification** — category, sub-category, fixed/variable for unknown merchants. **Business rules** — personal vs business nuance. **Custom Rules** — compiles Pending rules to JSON, then applies. | Yes — pipeline writes to `finance.db` and lookup tables. Re-run improves as cache grows. |
| **Review queue** | Confirm Categories → ✨ Suggest labels | Proposes labels for merchants still `needs_review` (lookup-first, then LLM). | No — you confirm in the modal. |
| **After an edit** | Edit Transactions → Apply → AI insight modal | Explains the pattern; may suggest a **Custom Rule** (plain English). | No — save rule is optional. |
| **Cadence** | Cadence tab → ✨ Suggest cadence (AI); Chat | Proposes recurring vs lump vs one-time from merchant history + your hint. | No — Review & save in modal or cadence queue. |
| **Label cleanup** | **AI Rules** tab | **Analyze** (heuristics only) or **Suggest with AI** — duplicate categories, sub-categories, merchant spellings. | No — you select proposals, preview, then Apply. |
| **Analytics** | Chat | LLM writes read-only **`query_sql`** against your SQLite data; answers are validated (e.g. month comparisons must not merge periods). Cadence keywords open propose flow. | Chat history only; reports save only if you ask. |

**Not AI:** Import upload, inbox archive, Excel optional export, table counts, most Edit Transactions field updates (direct SQLite), and cadence **math** (`effective_amount`, cash/core/normalized views).

### Three ways to improve logic over time

Use the right tool for the scope of the problem:

#### 1. Lookups & confirmed labels (pipeline memory)

**Best for:** “Always categorize Netflix as Entertainment” or “this bank `Category` maps to Utilities.”

| Mechanism | Where you set it | Effect on next Run processing |
|-----------|------------------|-------------------------------|
| Confirm merchant | **Confirm Categories** | `merchant_labels` in SQLite (+ optional Excel row) |
| Category rules | Settings → import Excel or DB | `category_rules` |
| Description cache | Automatic after processing | `description_lookup` — skips description LLM for known text |
| Cadence | **Cadence** tab → save rule | `cadence_rules` |

These are **deterministic at runtime**: the pipeline reads them before calling the LLM.

#### 2. Custom Rules (per-merchant / pattern if-then)

**Best for:** “Apple $9.99 is Business,” “checks for $60 are Music Lessons,” “highest Comcast charge each month is insurance, others are rental.”

| Step | Where |
|------|--------|
| Write rule | Settings → **Custom rules** (plain English), or **Edit insights** → Save as custom rule |
| Compile | LLM turns text into JSON (`assign`, `monthly_split_max`, …) |
| Apply | Runs on every **Run processing** (highest priority after other rules) |

Detail: [docs/EDIT_INSIGHTS.md](docs/EDIT_INSIGHTS.md). These are **programmatic** once compiled — the AI only helps author and compile them.

#### 3. AI Rules (taxonomy — global label vocabulary)

**Best for:** “`ATM`, `ATM Withdrawal`, and `ATM withdrawal` should be one sub-category,” “merge `Charitable Giving` into `Charitable`,” “same merchant spelled three ways.”

| Step | Where |
|------|--------|
| Discover | **AI Rules** → **Analyze labels** (fast duplicates) or **Suggest with AI** (semantic merges + confidence) |
| Review | Each proposal shows type, rationale, confidence, affected row counts |
| Apply | Select → Preview (dry run) → **Apply selected** (explicit confirm) |

This is a **different abstraction** from Custom Rules: it cleans **label dictionaries** across the database, not per-transaction if/then. Nothing applies automatically today; proposals include a confidence score for possible future auto-apply when you trust the pattern.

Detail: [docs/AI_TAXONOMY_RULES.md](docs/AI_TAXONOMY_RULES.md).

### Suggested workflow (monthly)

1. **Import & Categorize** — process new CSV(s); let lookups + LLM handle bulk labeling.  
2. **Confirm Categories** — confirm or ✨ suggest labels for uncertain merchants.  
3. **AI Rules** — periodically merge duplicate categories/subs/merchant names.  
4. **Cadence** — set run-rate rules for irregular merchants (insurance, annual fees).  
5. **Edit Transactions** — fix one-offs; save **Custom Rules** when the same pattern will repeat.  
6. **Chat** — explore spend; use cadence keywords + merchant name to open cadence proposals.

### Further reading

| Doc | Topic |
|-----|--------|
| [docs/AI_SESSION_CONTEXT.md](docs/AI_SESSION_CONTEXT.md) | Product direction, pitfalls, for new AI coding sessions |
| [docs/AI_TAXONOMY_RULES.md](docs/AI_TAXONOMY_RULES.md) | AI Rules tab API and behavior |
| [docs/EDIT_INSIGHTS.md](docs/EDIT_INSIGHTS.md) | Post-edit insights and Custom Rule suggestions |
| [docs/EXPENSE_CADENCE_PHASE_D.md](docs/EXPENSE_CADENCE_PHASE_D.md) | AI cadence propose + confirm |
| [docs/CONFIRM_CATEGORIES.md](docs/CONFIRM_CATEGORIES.md) | Review queue and confirm scopes |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What’s shipped vs planned (e.g. auto-apply taxonomy at confidence threshold) |

## Where files live

| Path | Role |
|------|------|
| `input/*.csv` | Bank exports you process |
| `processed/*.csv` | Archived inbox CSVs after a successful run |
| `data/finance.db` | **Primary store** — transactions, labels, cadence, chat, pipeline lookups |
| `scripts/transaction-lookups.xlsx` | **Optional** — one-time seed if DB lookup tables are empty; merge scratch on save unless you add pure in-memory merge later |

## What Run processing does

Each CSV is loaded, enriched by the pipeline, and saved to `finance.db`. With **`LOOKUP_SOURCE=db`** (default), rules and description cache are read from SQLite; new keys are written back to DB after each run. Set **`EXPORT_LOOKUPS=1`** in `.env` if you also want `transaction-lookups.xlsx` refreshed on save.

### Columns added (Income & Expenses)

| Column           | Description |
|------------------|-------------|
| Section          | `Income`, `Expense`, or `Adjustment` |
| AI Category      | Top-level category (e.g. Groceries, Utilities, Dining) |
| AI Sub-Category  | Finer label (e.g. Electric bill, Fast food) |
| Type             | `Fixed` or `Variable` |
| Sub-Type         | Empty for now; reserved for future breakdown |
| Expense Cadence  | `Monthly`, `Yearly`, `One-time`, etc. (see below) |

Original CSV columns (Date, Amount, Category, descriptions, account, etc.) are kept alongside these fields.

### Fixed vs. variable (how AI is guided)

- **Fixed**: Recurring obligations—mortgage/rent, HOA, utilities, insurance, phone/internet, subscriptions, gym, loan payments.
- **Variable**: Discretionary or fluctuating spend—groceries, restaurants, fuel, shopping, entertainment.

Credit card payments and internal transfers are treated as expenses (money movement), not income.

**Refunds vs income:** Only **Paychecks/Salary** and **Interest** count as **Income** when the amount is positive. A **positive** amount on a normal spending category (e.g. subscription refund) goes to **Adjustments**, not Income.

## LLM setup (Ollama — common default)

1. Install and start Ollama.
2. Pull a model:

```bash
ollama pull qwen2.5:14b
```

3. In `config/.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen2.5:14b
```

Verify: `curl http://127.0.0.1:11434/v1/models`

Good picks: `qwen2.5:14b`, `qwen2.5:7b-instruct` (faster after lookups exist), `llama3.1:8b`.

## LLM setup (LM Studio)

The pipeline uses LM Studio’s **OpenAI-compatible API** on your LAN.

1. Start LM Studio and load a model.
2. Enable **Serve on Local Network** (e.g. port `1234`).
3. Copy `config/.env.example` to `config/.env` and set:

| Setting | Example |
|---------|---------|
| `LLM_PROVIDER` | `lmstudio` |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` |
| `LM_STUDIO_MODEL` | `qwen2.5-14b-instruct-mlx` |

### Which model to use?

| Model | Verdict |
|-------|---------|
| **qwen2.5-coder-32b-instruct** | Best quality for nuanced rules; slower. Good for first-time seeding. |
| **qwen2.5-14b-instruct-mlx** | Best speed/quality balance on Apple Silicon. |
| **qwen2.5:7b-instruct** (Ollama) | Fastest for repeat runs once description cache is warm. |

### Performance tips (Apple Silicon)

| Lever | Recommendation |
|-------|----------------|
| **Model** | `qwen2.5-14b-instruct-mlx` or `qwen2.5:7b` for routine months |
| **Description cache** | Second runs are much faster once `description_lookup` in SQLite is warm |
| **Batch size** | Override with `LOCAL_BATCH_SIZE` / `DESCRIPTION_BATCH_SIZE` in `.env` on large-RAM machines |

### OpenAI cloud (optional)

Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in `config/.env`.

## Pipeline lookups (SQLite + optional Excel)

Default (**`LOOKUP_SOURCE=db`** in `config/.env`):

1. **Load** — `description_lookup`, `category_rules`, `pipeline_custom_rules`, `merchant_labels`, `cadence_rules` from `finance.db`
2. **First run** — if lookup tables are empty, auto-import from `scripts/transaction-lookups.xlsx` when that file exists
3. **Save** — merge new description keys, merchant rows, and rules into SQLite after each run
4. **Optional Excel** — set `EXPORT_LOOKUPS=1` to also refresh `transaction-lookups.xlsx` (off by default)

Legacy mode: `LOOKUP_SOURCE=excel` reads/writes the workbook only (not recommended).

Settings can **import** sheets from Excel into SQLite. **Confirm Categories** still upserts **MerchantCategories** in the workbook when you confirm a merchant (for Excel backup users).

Lookup data (same concepts as the old workbook sheets):

| Store | Contents |
|-------|----------|
| **`description_lookup`** | Cached bank text → generated description |
| **`category_rules`** | Bank `Category` → AI category, type |
| **`merchant_labels`** | Per-merchant category, type, classification |
| **`pipeline_custom_rules`** | Freeform rules → compiled JSON |
| **`cadence_rules`** | Merchant cadence for run-rate views |

See [docs/PIPELINE_DB_LOOKUPS.md](docs/PIPELINE_DB_LOOKUPS.md) for remaining work (pure in-memory merge without Excel scratch).

## Expense cadence

After categories are correct, separate **normal monthly run-rate** from **irregular** charges (e.g. annual insurance vs monthly premium).

**On each expense row**

| Column | Meaning |
|--------|---------|
| **Expense Cadence** | `Monthly`, `Yearly`, `One-time`, `Unplanned`, or `Unknown` |
| **In Monthly Run-Rate?** | `Y` = core monthly budget; `N` = cash spend excluded from run-rate |
| **Cadence Source** | `Lookup`, `Detected`, or `Default` |

Manage cadence in the **Cadence** tab and `cadence_rules` in SQLite. Import **ExpenseCadenceRules** from Excel via Settings. Chat and analytics support **cash**, **core**, and **normalized** views — see [docs/EXPENSE_CADENCE.md](docs/EXPENSE_CADENCE.md).

## Custom Rules (freeform → AI compile → apply)

**Highest priority at pipeline time:** Active custom rules run after category rules, LLM classification, and cadence lookup. The LLM **compiles** your English into JSON once; each run applies that JSON deterministically.

See [How AI is used — Custom Rules](#2-custom-rules-per-merchant--pattern-if-then) for when to use these vs **AI Rules**.

| Column | Purpose |
|--------|---------|
| **Rule** | Plain-English rule you write |
| **Status** | `Pending` → compile; `Active` → apply; `Error` / `Disabled` |
| **Compiled Rule** | JSON filled by the pipeline |

Example: split duplicate monthly charges by amount, or tag Apple $9.99 as Business only.

## Project files

| File / folder | Purpose |
|---------------|---------|
| `webapp/` | FastAPI app: API, UI, pipeline, processing, SQLite |
| `webapp/processing/` | Parse, classify, rules, cadence |
| `webapp/pipeline/` | `run_pipeline()` orchestration |
| `webapp/adapters/` | DataFrame → SQLite, `lookup_store` |
| `scripts/transaction-lookups.xlsx` | Optional seed / backup workbook (not required at runtime with `LOOKUP_SOURCE=db`) |
| `input/` | Bank CSV inbox |
| `processed/` | Archived CSVs after successful processing |
| `data/finance.db` | SQLite database (gitignored) |
| `config/` | `.env` and presets |
| `docs/ROADMAP.md` | Master plan — phases and detail doc links |
| `docs/AI_TAXONOMY_RULES.md` | AI Rules tab — taxonomy merge proposals |
| `docs/AI_SESSION_CONTEXT.md` | Bootstrap context for new AI chats |
| `requirements.txt` | Python dependencies |
