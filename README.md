# Transaction Insight

Local, privacy-first finance workspace: import bank CSVs into SQLite, let a local LLM propose categories and labels, then **review and chat with your expense data** in the browser (text or voice). AI proposes — you confirm — memory in SQLite improves the next run.

**Audience:** Developers self-hosting from GitHub. Feature and design docs → [docs/INDEX.md](docs/INDEX.md).

## Prerequisites

- Python 3.10+ (3.11+ recommended)
- Git clone of this repo
- A local LLM runtime: **Ollama** (default) or **LM Studio** — see [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md)
- A bank CSV export with at least **date**, **amount**, **category**, and one or more **description** columns

## Quick start

### 1. Install

**First time** — create the virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/.env.example config/.env   # or: cp config/.env.ollama config/.env
```

### 2. Configure LLM

Minimum for Ollama (after [installing and starting Ollama](https://ollama.com)):

```bash
ollama pull qwen2.5:14b
```

In `config/.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=qwen2.5:14b
```

Verify: `curl http://127.0.0.1:11434/v1/models`

LM Studio, model picks, role-specific models, and performance tips → [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md).

Edit optional UI flags in `config/.env` (common settings at the **top**, LLM block at the **bottom**). Restart the server after env changes.

### 3. Run the app

**Every session** — activate the venv and start the server:

```bash
source .venv/bin/activate
uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000
```

Or use `./start.sh` if present. Open http://127.0.0.1:8000.

## Next steps

After the app loads:

1. **Import** — upload a CSV and run processing (sidebar → Import).
2. **Review** — approve or reject AI proposals (labels, quality flags, insights).
3. **Chat** — ask questions about your spend; optional **Mic** in Chrome/Edge (3s silence or 30s max, then auto-send). Toolbar shows conversation context usage; **Clear screen** starts a fresh LLM thread.

Tab layout, inbox types, and monthly rhythm → [docs/product/AGENT_WORKSPACE.md](docs/product/AGENT_WORKSPACE.md).

## About this project

Personal finance apps export flat CSVs. Useful analysis — income vs spending, categories, fixed vs variable costs — needs structure the export does not provide. This project:

- **Preserves source data** in SQLite for auditability
- **Enriches rows** with AI categories, fixed/variable type, and flow type (you confirm uncertain labels)
- **Lets you talk to your data** — natural-language chat with SQL-backed analytics and saved reports
- **Remembers your decisions** — merchant labels, rules, and description cache speed up the next import

Vision, AI-first design, and every LLM touchpoint → [docs/product/PRODUCT_CHARTER.md](docs/product/PRODUCT_CHARTER.md).

## Configuration (essentials)

Copy from `config/.env.example` or a preset (`config/.env.ollama`, `config/.env.lmstudio`).

| Variable | Default | When to change |
|----------|---------|----------------|
| `UI_AGENT_WORKSPACE` | `1` | Set `0` for legacy tabs (Import & Categorize, Confirm Categories, …) |
| `UI_SHOW_CADENCE` | `0` | Set `1` to show Cadence tab and cadence proposals in Pending/chat |
| `LEARNING_AGENT_ENABLED` | `0` | Set `1` for scheduled pattern analysis → Workspace inbox |
| `CHAT_HISTORY_MESSAGES` | `20` | Prior chat turns sent to LLM (`0` = single-turn only) |
| `CHAT_CONTEXT_TOKEN_LIMIT` | `32768` | Context meter scale — match your chat model window |
| `LLM_PROVIDER` | see preset | `ollama`, `lmstudio`, or `openai` |

Full variable reference → comments in [config/.env.example](config/.env.example). Restart uvicorn after changes.

## Project layout

| Path | Role |
|------|------|
| `webapp/` | FastAPI app, UI, pipeline, agent |
| `webapp/pipeline/` | `run_pipeline()` orchestration |
| `webapp/processing/` | Parse, classify, rules, cadence |
| `config/` | `.env` and presets |
| `input/` | CSV inbox |
| `processed/` | Archived CSVs after a successful run |
| `data/finance.db` | SQLite store (gitignored) |
| `docs/INDEX.md` | All design and feature docs |

## Documentation

| I want to… | Read |
|------------|------|
| Install LLM (Ollama, LM Studio, models) | [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md) |
| Understand product vision | [docs/product/PRODUCT_CHARTER.md](docs/product/PRODUCT_CHARTER.md) |
| See shipped vs planned | [docs/product/ROADMAP.md](docs/product/ROADMAP.md) |
| Workspace UI & pending inbox | [docs/product/AGENT_WORKSPACE.md](docs/product/AGENT_WORKSPACE.md) |
| Chat, voice, custom reports | [docs/chat/CHAT_RICH_UI.md](docs/chat/CHAT_RICH_UI.md) |
| Custom Rules | [docs/rules/CUSTOM_RULES.md](docs/rules/CUSTOM_RULES.md) |
| CSV import & enriched columns | [docs/pipeline/IMPORT_PROCESS_UPLOAD.md](docs/pipeline/IMPORT_PROCESS_UPLOAD.md) |
| Pipeline & SQLite lookups | [docs/pipeline/PIPELINE_DB_LOOKUPS.md](docs/pipeline/PIPELINE_DB_LOOKUPS.md) |
| Everything else | [docs/INDEX.md](docs/INDEX.md) |

## Contributing / AI sessions

Bootstrap context for coding agents → [docs/product/AI_SESSION_CONTEXT.md](docs/product/AI_SESSION_CONTEXT.md)
