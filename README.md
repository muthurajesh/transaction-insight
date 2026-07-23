# Transaction Insight

Local, privacy-first finance workspace: import bank CSVs into SQLite, let a local LLM propose categories and labels, then **review and chat with your expense data** in the browser (text or voice). AI proposes — you confirm — memory in SQLite improves the next run.

**Audience:** Anyone who can run a few terminal commands. Feature and design docs → [docs/INDEX.md](docs/INDEX.md).

**You only need one local model.** Beginners: follow Quick start below. Experts (LM Studio, multiple models, tuning) → [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md).

## Prerequisites

| Need | Notes |
|------|--------|
| **Python** | 3.10+ (3.11+ recommended) |
| **Ollama** | Free local LLM runtime — [ollama.com](https://ollama.com) (macOS / Windows / Linux) |
| **Git** | To clone this repository |
| **RAM** | See [Which model?](#which-model) — one pull is enough |

A bank CSV with at least **Date**, **Amount**, **Category**, and a **description** column. No CSV yet? Use [`samples/sample_transactions.csv`](samples/sample_transactions.csv).

## Which model?

Pull **one** model. You do **not** need separate models for chat, audit, or learning.

| Your machine | Pull this | Approx. size |
|--------------|-----------|--------------|
| **16GB+ RAM** (recommended) | `qwen2.5:14b` | ~9GB download |
| **8–16GB RAM** | `qwen2.5:7b` | ~4.7GB download |

If you pick `7b`, set `OLLAMA_MODEL`, `PIPELINE_MODEL`, and `CHAT_MODEL` to `qwen2.5:7b` in `config/.env` after copying the beginner preset (or see [LLM_SETUP.md](docs/setup/LLM_SETUP.md)).

## Quick start (beginner)

### 1. Install Ollama and pull a model

1. Download and install [Ollama](https://ollama.com), then open it so it is running.
2. In a terminal:

```bash
ollama pull qwen2.5:14b
```

3. Check it responds:

```bash
curl http://127.0.0.1:11434/v1/models
```

You should see JSON listing your model. If the command fails, start the Ollama app and try again.

### 2. Install the app

```bash
git clone https://github.com/muthurajesh/transaction-insight.git
cd transaction-insight

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/.env.ollama.beginner config/.env
```

That copy is enough — **you do not need to edit** `config/.env` for a first run.

### 3. Run the app

```bash
source .venv/bin/activate
./start.sh
```

Or: `uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000`

Open **http://127.0.0.1:8000**

### 4. Try a sample import

1. Sidebar → **Import**
2. Upload [`samples/sample_transactions.csv`](samples/sample_transactions.csv) (or your bank export)
3. Wait for processing, then open **Review** / **Chat**

## Next steps

1. **Import** — upload your own bank CSV and run processing.
2. **Review** — approve or reject AI proposals (labels, quality flags, insights).
3. **Chat** — ask questions about your spend; optional **Mic** in Chrome/Edge (3s silence or 30s max, then auto-send). Toolbar shows conversation context usage; **Clear screen** starts a fresh LLM thread.

Tab layout, inbox types, and monthly rhythm → [docs/product/AGENT_WORKSPACE.md](docs/product/AGENT_WORKSPACE.md).

## Expert setup

Skip this if the beginner path works.

| Goal | Where |
|------|--------|
| LM Studio or OpenAI cloud | [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md) |
| Faster/stronger role-specific models | Same doc → **Expert: role-specific models** |
| Full env reference | [config/.env.example](config/.env.example) |
| Ollama with tuning flags on | `cp config/.env.ollama config/.env` |

Common UI toggles (edit `config/.env`, then restart the server):

| Variable | Default (beginner) | When to change |
|----------|--------------------|----------------|
| `UI_AGENT_WORKSPACE` | `1` | Set `0` for legacy tabs |
| `UI_SHOW_CADENCE` | `0` | Set `1` to show Cadence |
| `LEARNING_AGENT_ENABLED` | `0` | Set `1` for scheduled pattern analysis |
| `CLASSIFICATION_AUDIT_ENABLED` | `0` | Set `1` for post-import quality spot-checks |
| `LLM_PROVIDER` | `ollama` | `lmstudio` or `openai` |

## About this project

Personal finance apps export flat CSVs. Useful analysis — income vs spending, categories, fixed vs variable costs — needs structure the export does not provide. This project:

- **Preserves source data** in SQLite for auditability
- **Enriches rows** with AI categories, fixed/variable type, and flow type (you confirm uncertain labels)
- **Lets you talk to your data** — natural-language chat with SQL-backed analytics and saved reports
- **Remembers your decisions** — merchant labels, rules, and description cache speed up the next import

Vision, AI-first design, and every LLM touchpoint → [docs/product/PRODUCT_CHARTER.md](docs/product/PRODUCT_CHARTER.md).

## Project layout

| Path | Role |
|------|------|
| `webapp/` | FastAPI app, UI, pipeline, agent |
| `webapp/pipeline/` | `run_pipeline()` orchestration |
| `webapp/processing/` | Parse, classify, rules, cadence |
| `config/` | `.env` presets (beginner + expert) |
| `samples/` | Example CSV for first-run Import |
| `input/` | CSV inbox |
| `processed/` | Archived CSVs after a successful run |
| `data/finance.db` | SQLite store (gitignored) |
| `docs/INDEX.md` | All design and feature docs |

## Documentation

| I want to… | Read |
|------------|------|
| Install LLM (beginner + expert) | [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md) |
| Understand product vision | [docs/product/PRODUCT_CHARTER.md](docs/product/PRODUCT_CHARTER.md) |
| See shipped vs planned | [docs/product/ROADMAP.md](docs/product/ROADMAP.md) |
| Workspace UI & pending inbox | [docs/product/AGENT_WORKSPACE.md](docs/product/AGENT_WORKSPACE.md) |
| Chat, voice, custom reports | [docs/chat/CHAT_RICH_UI.md](docs/chat/CHAT_RICH_UI.md) |
| Custom Rules | [docs/rules/CUSTOM_RULES.md](docs/rules/CUSTOM_RULES.md) |
| CSV import & enriched columns | [docs/pipeline/IMPORT_PROCESS_UPLOAD.md](docs/pipeline/IMPORT_PROCESS_UPLOAD.md) |
| Pipeline & SQLite lookups | [docs/pipeline/PIPELINE_DB_LOOKUPS.md](docs/pipeline/PIPELINE_DB_LOOKUPS.md) |
| Everything else | [docs/INDEX.md](docs/INDEX.md) |

## License

[MIT](LICENSE)

## Contributing / AI sessions

Bootstrap context for coding agents → [docs/product/AI_SESSION_CONTEXT.md](docs/product/AI_SESSION_CONTEXT.md)
