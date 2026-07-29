# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] — 2026-07-29

### Added

- SECURITY.md and CONTRIBUTING.md for open-source collaboration
- GitHub Actions CI (pytest, no LLM required)
- Architecture overview (`docs/product/ARCHITECTURE.md`)
- Hermetic smoke tests for parse/flow, pipeline (mocked LLM), and FastAPI upload/status

### Changed

- Anonymized personal finance fingerprints in docs and tests
- Removed unused `output/` directory and legacy raw-ingest API routes
- Removed unused Playwright npm dependency
- Aligned `pyproject.toml` with `requirements.txt` (Python ≥3.11, multipart, pytest)

### Security

- Hardened `.gitignore` for local bank data, databases, and secrets
