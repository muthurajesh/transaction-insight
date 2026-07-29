# Architecture overview

**Audience:** Newcomers and blog readers.  
**Detail:** Feature docs → [INDEX.md](../INDEX.md). Product direction → [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md).

## How data flows

```text
Bank CSV
   │
   ▼
input/          ← upload or drop file (inbox)
   │
   ▼
Pipeline        ← parse → merchant labels / lookups → local LLM propose → rules → cadence
   │
   ▼
SQLite          ← data/finance.db (transactions, merchant_labels, rules, chat memory)
   │
   ├── Review / Confirm Categories
   ├── Chat (text or voice)
   └── Custom Rules / Cadence UI
   │
   ▼
processed/      ← original CSV archived after a successful run
```

AI **proposes** labels; you **confirm**. Confirmed labels improve the next import.

## Main packages

| Path | Role |
|------|------|
| `webapp/main.py` | FastAPI routes |
| `webapp/pipeline/` | Orchestrates one CSV through enrichment |
| `webapp/processing/` | Parse, flow type, lookups, cadence math |
| `webapp/services/` | Review, chat helpers, ingest upload, cadence APIs |
| `webapp/agent/` | Chat loop, SQL tools, learning analyst |
| `webapp/static/` | Browser UI |
| `config/` | Env presets (never commit real `.env`) |

## What is not shipped yet

Labeled in [ROADMAP.md](ROADMAP.md) and INDEX:

- Report layers / Settings custom-reports UI
- Hybrid ML classifier (`TRANSACTION_INTELLIGENCE_ARCHITECTURE.md`)
- Full agent orchestrator (`AGENTIC_AI_DESIGN.md` — design only)

Do not treat those docs as current product behavior.
