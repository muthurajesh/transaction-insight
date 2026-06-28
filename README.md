# Transaction Insight

Turn raw monthly bank transaction exports into categorized, analyzable data with AI-assisted labels, income/expense separation, and fixed vs. variable cost tagging — all through a local web app backed by SQLite.

**Documentation catalog:** [docs/INDEX.md](docs/INDEX.md) — all design docs by area (charter, roadmap, pipeline, chat, etc.). This file is for **running and onboarding** the app.

## What we are trying to do

Personal finance apps export transactions as flat CSV files. Useful analysis—seeing true income vs. spending, grouping by category, and understanding which costs are **fixed** (recurring obligations) vs. **variable** (discretionary)—requires extra structure that the export does not provide.

This project automates that enrichment:

1. **Preserve the source data** for auditability (stored in SQLite).
2. **Split transactions** into Income, Expenses, and Adjustments.
3. **Add categories and sub-categories** using AI that reads descriptions, amounts, accounts, and existing labels.
4. **Label cost behavior** with **Type** (Fixed or Variable) and **Sub-Type** (reserved for future use). Nuance: mortgage and monthly utilities are fixed; groceries and dining are variable.
5. **Review, confirm, and chat** over your data in the browser.

## Web app workflow

**Default (`UI_AGENT_WORKSPACE=1`):** **Workspace** — import strip, chat/analytics, and a **pending inbox** (review queue, audit flags, Learning Agent insights). Tabs: Workspace, Edit Transactions, Custom Rules, Settings. Legacy **Import & Categorize**, **Confirm Categories**, **AI Rules**, and **Cadence** are hidden (`UI_AGENT_WORKSPACE=0` restores them). See [docs/product/AGENT_WORKSPACE.md](docs/product/AGENT_WORKSPACE.md).

| Step | Where |
|------|--------|
| Upload & process CSV | **Workspace** import strip (or **Import & Categorize** when legacy UI) — progress bar shows elapsed time and ETA |
| Pending confirmations | **Workspace** inbox — merchant labels, quality flags, pattern/cadence insights → unified Approve / Dismiss / Defer |
| Classification quality alerts | Inbox (`quality_flag`) or **Import & Categorize** alerts panel → **View in Edit Transactions** |
| First visit | Guided **quick tour** (once per browser; skipped when data already exists) |
| Fix uncertain merchants | Inbox (`merchant_label`) or **Confirm Categories** (legacy) |
| Clean duplicate labels | **AI Rules** (legacy tab) — merge synonyms (user confirms before apply) |
| Edit rows, custom rules | **Edit Transactions**, **Custom Rules** |
| Ask questions | **Workspace** chat — type or **Mic** (Chrome/Edge); voice stops after 3s silence or 30s max and sends automatically |

**Processing core:** [`webapp/pipeline/`](webapp/pipeline/) orchestrates [`webapp/processing/`](webapp/processing/) (LLM, rules, cadence). Pipeline lookups load and save from **SQLite** (`finance.db`).

Configure Ollama or LM Studio via `config/.env` — copy from `config/.env.example` or a preset (`config/.env.lmstudio`, `config/.env.ollama`). Common settings (paths, UI flags, lookups) sit at the **top** of each file; **LLM provider** settings at the **bottom**. See [Configuration](#configuration).

See [How AI is used](#how-ai-is-used) for every LLM touchpoint and how to extend behavior with rules.

### Quick start

**First time** — create the virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/.env.example config/.env   # or: cp config/.env.ollama config/.env
```

Then edit LLM settings (and optional UI flags) in `config/.env`. Restart the server after env changes.

**Every session** — activate the venv and start the server:

```bash
source .venv/bin/activate
uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000
```

Or use `./start.sh` if present. Open http://127.0.0.1:8000.

See [docs/classification/CONFIRM_CATEGORIES.md](docs/classification/CONFIRM_CATEGORIES.md) for the label queue. Chat dollar amounts use the same spend rules as the pipeline (negative outflows only).

## Configuration

`config/.env` (and the tracked presets / `.env.example`) use one layout:

1. **Common** — web paths, UI flags, pipeline lookups, batch tuning (same regardless of LLM backend).
2. **LLM** — `LLM_PROVIDER` plus provider-specific vars and optional `PIPELINE_MODEL` / `CHAT_MODEL` split.

| Variable | Default | Purpose |
|----------|---------|---------|
| `UI_AGENT_WORKSPACE` | `1` | **Workspace** tab (import + chat + pending inbox). Set `0` for legacy tabs (Chat, Import & Categorize, Confirm Categories, …). |
| `UI_SHOW_CADENCE` | `0` with workspace | Cadence tab, Edit cadence links, Settings cadence card. Set `1` to show (legacy layout defaults cadence on). |
| `LEARNING_AGENT_ENABLED` | `0` | Opt-in scheduled pattern analysis → Workspace inbox. Manual run: Settings → **Run analysis now** or `POST /api/learning-agent/run`. |
| `LLM_LOG_CALLS` | `1` | Log pipeline/chat LLM requests to console and `data/llm.log`. Set `0` to disable. |
| `PIPELINE_SECONDS_PER_ROW` | `1.2` | ETA heuristic for Import & Categorize progress bar. |
| `FINANCE_DB_PATH`, `FINANCE_INBOX_DIR`, `FINANCE_PROCESSED_DIR` | see `.env.example` | Override data paths. |

Restart `./start.sh` (or uvicorn) after changing `.env`.

## How AI is used

Transaction Insight mixes **deterministic code** (parsing, dedup, SQL, lookup tables, compiled rules) with **local LLM calls** where judgment is needed. The default posture is **AI proposes → you confirm → then data is saved**. Nothing in the review or taxonomy flows auto-writes without your approval.

### Design principles

| Layer | Role | Examples |
|-------|------|----------|
| **Deterministic** | Exact, repeatable | `transaction_id` hash, amount signs, `flow_type`, payroll spillover, SQL totals |
| **Lookup rules** | Your saved patterns | `category_rules`, `merchant_labels`, `description_lookup`, `cadence_rules` |
| **Compiled rules** | English → JSON, then fixed logic | **Custom Rules** tab (if/then on merchant, amount, description, flow type) |
| **AI judgment** | Ambiguous text, new merchants, taxonomy cleanup | Descriptions, classification, suggestions, chat, **AI Rules** proposals |
| **You** | Final authority | Confirm Categories, cadence modal, Apply on AI Rules, save Custom Rules |

Models are configured in `config/.env`. You can split **pipeline** vs **chat** models (`PIPELINE_MODEL`, `CHAT_MODEL`) — see [LLM setup](#llm-setup-ollama--common-default) below.

### Where AI runs (by tab / phase)

| When | Where in app | What the LLM does | Persists without you? |
|------|----------------|-------------------|------------------------|
| **Run processing** | Import & Categorize | **Descriptions** — bank text → merchant label. **Classification** — category, sub-category, fixed/variable for unknown merchants. **Business rules** — personal vs business nuance. **Custom Rules** — compiles Pending rules to JSON, then applies. | Yes — pipeline writes to `finance.db` and lookup tables. Re-run improves as cache grows. |
| **Classification audit** | Import & Categorize (alerts) | Sampled re-check: heuristics + stronger audit model vs pipeline labels; shows audit-time vs live DB labels. | No — **View in Edit Transactions** or dismiss; stale alerts auto-clear when labels match. |
| **Review queue** | Confirm Categories → ✨ Suggest labels | Proposes labels for merchants still `needs_review` (lookup-first, then LLM). | No — you confirm in the modal. |
| **After an edit** | Edit Transactions → Apply → AI insight modal | Explains the pattern; may suggest a **Custom Rule** (plain English). | No — save rule is optional. |
| **Cadence** | Cadence tab → ✨ Suggest cadence (AI); Chat | Proposes recurring vs lump vs one-time from merchant history + your hint. | No — Review & save in modal or cadence queue. |
| **Label cleanup** | **AI Rules** tab | **Analyze** (heuristics only) or **Suggest with AI** — duplicate categories, sub-categories, merchant spellings. | No — you select proposals, preview, then Apply. |
| **Analytics** | Workspace (Chat) | LLM writes read-only **`query_sql`**; save multi-turn explorations as **custom reports** (prompt + SQL, rerun/tweak/version). Help panel includes report workflows. | Saved reports in `custom_reports` table; see [docs/chat/CHAT_CUSTOM_REPORTS.md](docs/chat/CHAT_CUSTOM_REPORTS.md). |
| **Learning Agent** | Workspace inbox (opt-in) | Scheduled/heuristic pattern scan over `decision_events` → `ai_insights` proposals (category flips, cadence candidates). | No — accept/reject in inbox; accepted insights feed **review suggest**. |

**Not AI:** Import upload, inbox archive, table counts, most Edit Transactions field updates (direct SQLite), and cadence **math** (`effective_amount`, cash/core/normalized views).

### Three ways to improve logic over time

Use the right tool for the scope of the problem:

#### 1. Lookups & confirmed labels (pipeline memory)

**Best for:** “Always categorize Netflix as Entertainment” or “this bank `Category` maps to Utilities.”

| Mechanism | Where you set it | Effect on next Run processing |
|-----------|------------------|-------------------------------|
| Confirm merchant | **Confirm Categories** | `merchant_labels` in SQLite |
| Category rules | Settings / pipeline | `category_rules` in SQLite |
| Description cache | Automatic after processing | `description_lookup` — validated cache hit or LLM (User/Simple are context only, not copied verbatim) |
| Cadence | **Cadence** tab → save rule | `cadence_rules` |

These are **deterministic at runtime**: the pipeline reads them before calling the LLM.

#### 2. Custom Rules (per-merchant / pattern if-then)

**Best for:** “Apple $9.99 is Business,” “checks for $60 are Music Lessons,” “highest Comcast charge each month is insurance, others are rental.”

| Step | Where |
|------|--------|
| Write rule | **Custom Rules** tab (plain English), or **Edit insights** → Save as custom rule |
| Preview | **Run preview** — matched transactions with current vs proposed labels; read-only compiled JSON |
| Compile | LLM turns text into JSON (`assign`, `monthly_split_max`, …) |
| Apply | **Save & apply** (one rule), **Apply all rules**, or on every **Run processing** (highest priority after other rules) |

Detail: [docs/rules/CUSTOM_RULES.md](docs/rules/CUSTOM_RULES.md) · [docs/rules/EDIT_INSIGHTS.md](docs/rules/EDIT_INSIGHTS.md). These are **programmatic** once compiled — the AI only helps author and compile them.

#### 3. AI Rules (taxonomy — global label vocabulary)

**Best for:** duplicate sub-category spellings, synonym top-level categories, same merchant spelled multiple ways.

| Step | Where |
|------|--------|
| Discover | **AI Rules** → **Analyze labels** (fast duplicates) or **Suggest with AI** (semantic merges + confidence) |
| Review | Each proposal shows type, rationale, confidence, affected row counts |
| Apply | Select → Preview (dry run) → **Apply selected** (explicit confirm) |

This is a **different abstraction** from Custom Rules: it cleans **label dictionaries** across the database, not per-transaction if/then. Nothing applies automatically today; proposals include a confidence score for possible future auto-apply when you trust the pattern.

Detail: [docs/classification/AI_TAXONOMY_RULES.md](docs/classification/AI_TAXONOMY_RULES.md).

### Suggested workflow (monthly)

**Workspace (default):**

1. **Workspace** — upload/process CSV(s); work the **pending inbox** (labels, audit flags, Learning Agent insights).  
2. **Edit Transactions** / **Custom Rules** — fix one-offs; save rules when a pattern repeats.  
3. **Workspace chat** — explore spend; cadence keywords + merchant name still open cadence proposals. **Mic** transcribes (3s silence or 30s cap) and auto-sends.

**Legacy tabs (`UI_AGENT_WORKSPACE=0`):** Import & Categorize → Confirm Categories → AI Rules → Cadence → Edit → Chat (same order as before).

### Further reading

| Doc | Topic |
|-----|--------|
| [docs/INDEX.md](docs/INDEX.md) | **Documentation index** — all docs by area |
| [docs/product/PRODUCT_CHARTER.md](docs/product/PRODUCT_CHARTER.md) | **Master product reference** — vision, AI-first design, decision gate |
| [docs/product/AI_SESSION_CONTEXT.md](docs/product/AI_SESSION_CONTEXT.md) | Product direction, pitfalls, for new AI coding sessions |
| [docs/classification/AI_TAXONOMY_RULES.md](docs/classification/AI_TAXONOMY_RULES.md) | AI Rules tab API and behavior |
| [docs/rules/CUSTOM_RULES.md](docs/rules/CUSTOM_RULES.md) | Custom Rules tab — preview, compile, apply, Flow Type |
| [docs/rules/EDIT_INSIGHTS.md](docs/rules/EDIT_INSIGHTS.md) | Post-edit insights and Custom Rule suggestions |
| [docs/cadence/EXPENSE_CADENCE_PHASE_D.md](docs/cadence/EXPENSE_CADENCE_PHASE_D.md) | AI cadence propose + confirm |
| [docs/classification/CONFIRM_CATEGORIES.md](docs/classification/CONFIRM_CATEGORIES.md) | Review queue and confirm scopes |
| [docs/chat/CHAT_RICH_UI.md](docs/chat/CHAT_RICH_UI.md) | Chat tables, Help panel, voice input |
| [docs/chat/CHAT_CUSTOM_REPORTS.md](docs/chat/CHAT_CUSTOM_REPORTS.md) | Build, save, and rerun custom reports in Chat |
| [docs/classification/CLASSIFICATION_TAXONOMY.md](docs/classification/CLASSIFICATION_TAXONOMY.md) | Classify LLM payload and category vocabulary goals |
| [docs/classification/CLASSIFICATION_AUDIT.md](docs/classification/CLASSIFICATION_AUDIT.md) | Sampled classification quality audit (14b vs audit model) |
| [docs/product/ROADMAP.md](docs/product/ROADMAP.md) | What’s shipped vs planned (e.g. auto-apply taxonomy at confidence threshold) |
| [docs/product/AGENT_WORKSPACE.md](docs/product/AGENT_WORKSPACE.md) | Workspace UI, pending inbox, hidden legacy tabs |
| [docs/product/DECISION_MEMORY.md](docs/product/DECISION_MEMORY.md) | Decision event log, Learning Agent, review-suggest feedback |

## Where files live

| Path | Role |
|------|------|
| `input/*.csv` | Bank exports you process |
| `processed/*.csv` | Archived inbox CSVs after a successful run |
| `data/finance.db` | **Primary store** — transactions, labels, cadence, chat, pipeline lookups |

## What Run processing does

Each CSV is loaded, enriched by the pipeline, and saved to `finance.db`. Rules and description cache are read from SQLite; new keys are written back to DB after each run.

### Columns added (Income & Expenses)

| Column           | Description |
|------------------|-------------|
| Section          | `Income`, `Expense`, or `Adjustment` |
| AI Category      | Top-level category (LLM- or user-assigned; no fixed list in code) |
| AI Sub-Category  | Finer label under the category |
| Type             | `Fixed` or `Variable` |
| Sub-Type         | Empty for now; reserved for future breakdown |
| Expense Cadence  | `Monthly`, `Yearly`, `One-time`, etc. (see below) |

Original CSV columns (Date, Amount, Category, descriptions, account, etc.) are kept alongside these fields.

### Fixed vs. variable (how AI is guided)

The LLM assigns **Fixed** vs **Variable** from transaction context (recurring obligations vs discretionary spend). There is no hardcoded category→type map in Python.

Credit card payments and internal transfers are treated as expenses (money movement), not income.

**Refunds vs income:** Only **Paychecks/Salary** and **Interest** count as **Income** when the amount is positive. A **positive** amount on a normal spending category goes to **Adjustments**, not Income.

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

## Pipeline lookups (SQLite)

1. **Load** — `description_lookup`, `category_rules`, `pipeline_custom_rules`, `merchant_labels`, `cadence_rules` from `finance.db`
2. **Process** — validated description cache, then LLM (User/Simple bank fields are context only); implausible cached labels rejected
3. **Save** — merge new description keys, merchant rows, and rules into SQLite after each run

Lookup tables:

| Store | Contents |
|-------|----------|
| **`description_lookup`** | Cached bank text → generated description |
| **`category_rules`** | Bank `Category` → AI category, type |
| **`merchant_labels`** | Per-merchant category, type, classification |
| **`pipeline_custom_rules`** | Freeform rules → compiled JSON |
| **`cadence_rules`** | Merchant cadence for run-rate views |

See [docs/pipeline/PIPELINE_DB_LOOKUPS.md](docs/pipeline/PIPELINE_DB_LOOKUPS.md) for lookup storage details.

## Expense cadence

After categories are correct, separate **normal monthly run-rate** from **irregular** charges (e.g. annual insurance vs monthly premium).

**On each expense row**

| Column | Meaning |
|--------|---------|
| **Expense Cadence** | `Monthly`, `Yearly`, `One-time`, `Unplanned`, or `Unknown` |
| **In Monthly Run-Rate?** | `Y` = core monthly budget; `N` = cash spend excluded from run-rate |
| **Cadence Source** | `Lookup`, `Detected`, or `Default` |

Manage cadence via **Workspace inbox** proposals (Learning Agent), chat `propose_cadence_rule`, or the **Cadence** tab when `UI_SHOW_CADENCE=1`. Rules live in `cadence_rules` in SQLite. Chat and analytics support **cash**, **core**, and **normalized** views — see [docs/cadence/EXPENSE_CADENCE.md](docs/cadence/EXPENSE_CADENCE.md).

## Custom Rules (freeform → AI compile → apply)

**Dedicated tab:** **Custom Rules** — compose, preview matches (current vs proposed), view compiled JSON, save, apply one or all.

**Highest priority at pipeline time:** Active custom rules run after category rules, LLM classification, and cadence lookup.

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
| `input/` | Bank CSV inbox |
| `processed/` | Archived CSVs after successful processing |
| `data/finance.db` | SQLite database (gitignored) |
| `config/` | `.env` and presets (`.env.example`, `.env.lmstudio`, `.env.ollama`) |
| `.cursor/rules/` | Cursor agent rules (e.g. README sync on significant changes) |
| `docs/INDEX.md` | Documentation index — all docs by area |
| `docs/product/PRODUCT_CHARTER.md` | Master product reference — vision, decision gate |
| `docs/product/ROADMAP.md` | Master plan — phases and detail doc links |
| `docs/classification/AI_TAXONOMY_RULES.md` | AI Rules tab — taxonomy merge proposals |
| `docs/product/AI_SESSION_CONTEXT.md` | Bootstrap context for new AI chats |
| `requirements.txt` | Python dependencies |
