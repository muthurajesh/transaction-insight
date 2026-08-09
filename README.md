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

## Setup (macOS)

Install Transaction Insight with one command. The script clones the repo, creates a Python environment, and asks whether you want a local LLM (Ollama) or OpenAI cloud.

```bash
mkdir -p ~/transaction-insight && cd ~/transaction-insight
curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/main/install.sh | bash
```

When prompted, choose **1 = Local** (Ollama, default `qwen2.5:7b`) or **2 = Cloud** (OpenAI — paste a key, or skip and edit `config/.env` later). Then:

```bash
./start.sh
```

Open http://127.0.0.1:8000

### Where it installs

- Empty folder → installs in that folder
- Already a Transaction Insight clone → updates in place
- Folder has other files → creates a `transaction-insight/` subfolder
- Override the path: `… | INSTALL_DIR=~/my-path bash`

### Advanced options

Pass overrides to `bash` — not to `curl`, which would never see them:

```bash
curl -fsSL https://raw.githubusercontent.com/muthurajesh/transaction-insight/main/install.sh | INSTALL_REF=v0.2.0 bash
```

Other overrides (same pattern): `INSTALL_DIR=…`, `INSTALL_REF=…`, `DEFAULT_REF=…`, `LLM_MODE=local|cloud`, `FORCE_ENV=1`.

Already cloned: `bash scripts/install_macos.sh`

Not on macOS, or want full control → [Manual setup](#manual-setup) · [LLM setup](docs/setup/LLM_SETUP.md)

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
