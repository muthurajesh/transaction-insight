from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from webapp.llm.client import create_client, resolve_provider_config

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
SCRIPTS_DIR = ROOT / "scripts"
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
load_dotenv(CONFIG_DIR / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = Path(os.getenv("FINANCE_DB_PATH", str(DATA_DIR / "finance.db")))
INBOX_DIR = Path(os.getenv("FINANCE_INBOX_DIR", str(ROOT / "input")))
PROCESSED_DIR = Path(os.getenv("FINANCE_PROCESSED_DIR", str(ROOT / "processed")))
LOOKUP_FILE = Path(
    os.getenv("LOOKUP_FILE", str(SCRIPTS_DIR / "transaction-lookups.xlsx"))
)
STATIC_DIR = Path(__file__).resolve().parent / "static"

LOOKUP_SOURCE = os.getenv("LOOKUP_SOURCE", "db").strip().lower()
EXPORT_LOOKUPS_TO_EXCEL = os.getenv("EXPORT_LOOKUPS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def env_bool(name: str, *, default: bool = True) -> bool:
    """Parse UI/feature flags: 1/true/yes/on → True; 0/false/no/off → False."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on")


UI_SHOW_CADENCE = env_bool("UI_SHOW_CADENCE", default=True)
UI_SHOW_EXCEL_LOOKUP_IMPORT = env_bool("UI_SHOW_EXCEL_LOOKUP_IMPORT", default=True)


def default_lookup_path() -> Path:
    return LOOKUP_FILE


@dataclass
class PipelineConfig:
    """Options for run_pipeline."""

    lookup_path: Path | None = None
    skip_lookup_update: bool = False
    rebuild_description_lookup: bool = False
    skip_cadence_detection: bool = False
    provider: str = "auto"
    base_url: str | None = None
    model: str | None = None
    batch_size: int | None = None
    source_file: str = ""
    update_lookup_workbook: bool = True
    lookup_source: str = LOOKUP_SOURCE
    export_lookup_excel: bool = EXPORT_LOOKUPS_TO_EXCEL

    def resolved_lookup_path(self) -> Path:
        return self.lookup_path or default_lookup_path()

    def use_db_lookups(self) -> bool:
        return (self.lookup_source or "db").strip().lower() == "db"


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
LLM_MODEL = CHAT_MODEL
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", os.getenv("REQUEST_TIMEOUT", "120")))


def get_llm_client():
    return create_client(LLM_PROVIDER, base_url=LLM_BASE_URL)
