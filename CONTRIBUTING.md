# Contributing

Thanks for interest in Transaction Insight. This is a personal-finance workspace: **AI proposes, you confirm**, with data kept local by default.

## Setup

```bash
git clone https://github.com/muthurajesh/transaction-insight.git
cd transaction-insight

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e ".[dev]"     # pytest

cp config/.env.ollama config/.env
```

Run the app: `./start.sh` → http://127.0.0.1:8000  
LLM install details: [docs/setup/LLM_SETUP.md](docs/setup/LLM_SETUP.md)

## Tests

Tests are hermetic (in-memory SQLite, mocked LLM). No Ollama required.

```bash
source .venv/bin/activate
pytest -q
```

## Project docs

| Doc | Use |
|-----|-----|
| [README.md](README.md) | Quick start |
| [docs/INDEX.md](docs/INDEX.md) | Full doc catalog |
| [docs/product/PRODUCT_CHARTER.md](docs/product/PRODUCT_CHARTER.md) | Product direction |
| [docs/product/ARCHITECTURE.md](docs/product/ARCHITECTURE.md) | How pieces fit |
| [docs/product/AI_SESSION_CONTEXT.md](docs/product/AI_SESSION_CONTEXT.md) | Context for coding agents |

## Pull requests

- Keep changes focused; match existing style.
- Do not commit real bank CSVs, `config/.env`, or databases.
- Do not add hardcoded merchant/category taxonomies in Python/JS — use LLM prompts, SQLite, or user rules ([`.cursor/rules/no-data-logic-in-code.mdc`](.cursor/rules/no-data-logic-in-code.mdc)).
- Add or update tests when changing behavior.
- Run `pytest -q` before opening a PR.

## Security

See [SECURITY.md](SECURITY.md).
