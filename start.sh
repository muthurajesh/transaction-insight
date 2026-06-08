#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .venv/bin/activate ]]; then
  echo "Missing .venv — run: python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate
exec uvicorn webapp.main:app --reload --host 127.0.0.1 --port 8000