from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from webapp.llm.client import create_client, resolve_provider_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
load_dotenv(CONFIG_DIR / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("FINANCE_DB_PATH", str(DATA_DIR / "finance.db")))
INBOX_DIR = Path(os.getenv("FINANCE_INBOX_DIR", str(ROOT / "input")))
PROCESSED_DIR = Path(os.getenv("FINANCE_PROCESSED_DIR", str(ROOT / "processed")))
STATIC_DIR = Path(__file__).resolve().parent / "static"


def env_bool(name: str, *, default: bool = True) -> bool:
    """Parse UI/feature flags: 1/true/yes/on → True; 0/false/no/off → False."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on")


UI_AGENT_WORKSPACE = env_bool("UI_AGENT_WORKSPACE", default=True)
UI_SHOW_CADENCE = env_bool("UI_SHOW_CADENCE", default=not UI_AGENT_WORKSPACE)

_raw_ui_mode = os.getenv("UI_MODE", "simple").strip().lower()
UI_MODE = _raw_ui_mode if _raw_ui_mode in ("simple", "expert") else "simple"

CLASSIFICATION_AUDIT_ENABLED = env_bool("CLASSIFICATION_AUDIT_ENABLED", default=True)

LEARNING_AGENT_ENABLED = env_bool("LEARNING_AGENT_ENABLED", default=False)
LEARNING_AGENT_INTERVAL_HOURS = max(
    1, int(os.getenv("LEARNING_AGENT_INTERVAL_HOURS", "3"))
)
LEARNING_AGENT_USE_LLM = env_bool("LEARNING_AGENT_USE_LLM", default=True)


@dataclass
class PipelineConfig:
    """Options for run_pipeline."""

    skip_lookup_update: bool = False
    rebuild_description_lookup: bool = False
    skip_cadence_detection: bool = False
    provider: str = "auto"
    base_url: str | None = None
    model: str | None = None
    batch_size: int | None = None
    source_file: str = ""
    save_lookups: bool = False


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
_learning_agent_model_env = os.getenv("LEARNING_AGENT_MODEL", "").strip()
if _learning_agent_model_env:
    _, _, LEARNING_AGENT_MODEL = resolve_provider_config(
        "auto",
        base_url_arg=None,
        model_arg=_learning_agent_model_env,
        role="learning_agent",
    )
else:
    LEARNING_AGENT_MODEL = CHAT_MODEL
_, _, CLASSIFICATION_AUDIT_MODEL = resolve_provider_config(
    "auto",
    base_url_arg=None,
    model_arg=os.getenv("CLASSIFICATION_AUDIT_MODEL", "").strip() or None,
    role="classification_audit",
)
LLM_MODEL = CHAT_MODEL
CHAT_HISTORY_MESSAGES = max(0, min(100, int(os.getenv("CHAT_HISTORY_MESSAGES", "20"))))
CHAT_CONTEXT_TOKEN_LIMIT = max(
    4096, int(os.getenv("CHAT_CONTEXT_TOKEN_LIMIT", "32768"))
)


def get_llm_client():
    return create_client(LLM_PROVIDER, base_url=LLM_BASE_URL)
