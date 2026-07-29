# Security Policy

## Supported versions

Security fixes are accepted on the default branch (`main`) of this repository.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

Email the maintainer via the address on your GitHub profile for [muthurajesh](https://github.com/muthurajesh), or use GitHub’s private vulnerability reporting if enabled on this repo.

Include: what you found, steps to reproduce, and impact.

## What this project stores

Transaction Insight is designed for **local** use:

- Bank CSVs and SQLite live under `data/`, `input/`, and `processed/` on your machine (gitignored).
- Never commit `config/.env`, databases, or real bank exports.
- If you set `LLM_PROVIDER=openai` (or another cloud API), **transaction text may be sent to that provider**. Prefer Ollama/LM Studio for privacy.

## Secrets

- Do not paste API keys, `.env` contents, or account numbers into issues or PRs.
- Rotate any key that may have been exposed.
