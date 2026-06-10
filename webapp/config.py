from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from transaction_insight.core import create_client, resolve_provider_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
load_dotenv(CONFIG_DIR / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("FINANCE_DB_PATH", str(DATA_DIR / "finance.db")))
INBOX_DIR = Path(os.getenv("FINANCE_INBOX_DIR", str(ROOT / "input")))
PROCESSED_DIR = Path(os.getenv("FINANCE_PROCESSED_DIR", str(ROOT / "processed")))
LOOKUP_FILE = Path(
    os.getenv("LOOKUP_FILE", str(ROOT / "scripts" / "transaction-lookups.xlsx"))
)
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Same resolver as CLI / run_pipeline; chat may use a different loaded model.
LLM_PROVIDER, LLM_BASE_URL, PIPELINE_MODEL = resolve_provider_config(
    "auto",
    base_url_arg=None,
    model_arg=None,
    role="pipeline",
)
_, _, CHAT_MODEL = resolve_provider_config(
    "auto",
    base_url_arg=None,
    model_arg=None,
    role="chat",
)
# Back-compat alias for chat-facing code.
LLM_MODEL = CHAT_MODEL
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", os.getenv("REQUEST_TIMEOUT", "120")))


def get_llm_client():
    return create_client(LLM_PROVIDER, base_url=LLM_BASE_URL)
