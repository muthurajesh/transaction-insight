# Transaction Insight

Load your bank CSV. Ask questions about your spending — like ChatGPT, by text or voice.

That is the goal of this project. Export transactions from your bank, drop them in, let the app clean them up, then ask things like:

- How much did I spend last month?
- What were my top 3 spending categories?
- Why was last month higher than usual?

It is not finished. Chat and import work today; better answers and anomaly-style “why” questions are still evolving. Everything runs on your machine by default (local LLM via [Ollama](https://ollama.com)).

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) (for the local model)
- A bank CSV with at least **Date**, **Amount**, **Category**, and a description column  
  (or use [`samples/sample_transactions.csv`](samples/sample_transactions.csv))

## Setup

### 1. Pull a model

```bash
ollama pull qwen2.5:14b
```

On a smaller machine (~8–16GB RAM), use `qwen2.5:7b` instead, then set `OLLAMA_MODEL`, `PIPELINE_MODEL`, and `CHAT_MODEL` to that name in `config/.env` after step 2.

Check Ollama is up:

```bash
curl http://127.0.0.1:11434/v1/models
```

### 2. Install the app

```bash
git clone https://github.com/muthurajesh/transaction-insight.git
cd transaction-insight

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/.env.ollama.beginner config/.env
```

### 3. Run it

```bash
./start.sh
```

Open http://127.0.0.1:8000

### 4. Try it

1. **Import** — upload the sample CSV (or your bank export) and wait for processing  
2. **Check labels** — approve payees that need a look (combine duplicate names when shown)  
3. **Ask** — questions about your spend (Mic works in Chrome/Edge)

Use **Settings → Display mode → Expert** for Find & edit and Automate (Custom Rules).

## More setup

LM Studio, OpenAI, or multiple models → [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md)

## Docs

| | |
|--|--|
| How the pieces fit | [docs/product/ARCHITECTURE.md](docs/product/ARCHITECTURE.md) |
| Design notes | [docs/INDEX.md](docs/INDEX.md) |
| Contributing / tests | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Security | [SECURITY.md](SECURITY.md) |

## License

[MIT](LICENSE)
