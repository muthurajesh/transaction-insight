# Transaction Insight

Load your bank CSV. Ask questions about your spending — like ChatGPT, by text or voice.

That is the goal of this project. Export transactions from your bank, drop them in, let the app clean them up, then ask things like:

- How much did I spend last month?
- What were my top 3 spending categories?
- Why was last month higher than usual?

It is not finished. Chat and import work today; better answers and anomaly-style “why” questions are still evolving. Everything runs on your machine by default (local LLM via [Ollama](https://ollama.com)), or you can use OpenAI cloud.

## Requirements

- macOS (recommended one-liner below) or any OS with Python 3.11+ for manual setup
- [Ollama](https://ollama.com) (local model) **or** an OpenAI API key (cloud)
- A bank CSV with at least **Date**, **Amount**, **Category**, and a description column  
  (or use [`samples/sample_transactions.csv`](samples/sample_transactions.csv))

## Setup (macOS — one-liner)

```bash
curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/develop/install.sh | bash
```

This picks the newest `v*` release tag, and falls back to `develop` when that tag predates the installer.

It installs **into the folder you run it from**, so `mkdir transaction-insight && cd transaction-insight` first if you want it somewhere specific. If that folder already has files in it, the repo goes into a `transaction-insight/` subfolder instead; if it is already a clone, it updates in place. Override with `INSTALL_DIR=`.

The installer creates a Python venv, then asks **1 = Local LLM** (Ollama, default `qwen2.5:7b`) or **2 = Cloud LLM** (OpenAI — paste a key, or skip and edit `config/.env` later).

To pin a tag or branch, pass the variable to `bash` — not to `curl`, which would never see it:

```bash
curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/develop/install.sh | INSTALL_REF=v0.2.0 bash
```
OR

```bash
curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/develop/install.sh | INSTALL_REF=main bash
```

Overrides (all passed to `bash` the same way): `INSTALL_DIR=…`, `INSTALL_REF=…`, `DEFAULT_REF=…`, `LLM_MODE=local|cloud`, `FORCE_ENV=1`.

From an existing clone:

```bash
bash scripts/install_macos.sh
```

Then:

```bash
cd ~/transaction-insight   # or your INSTALL_DIR / clone
./start.sh
```

Open http://127.0.0.1:8000

## Manual setup

### 1. Pull a model (local only)

```bash
ollama pull qwen2.5:7b
```

For higher quality on 16GB+ RAM machines, use `qwen2.5:14b` and set `OLLAMA_MODEL`, `PIPELINE_MODEL`, and `CHAT_MODEL` in `config/.env`.

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

cp config/.env.ollama config/.env
# Or cloud: cp config/.env.openai config/.env  # then set OPENAI_API_KEY
# Or LM Studio: cp config/.env.lmstudio config/.env
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
