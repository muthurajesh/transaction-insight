from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CONFIG_DIR = PROJECT_ROOT / "config"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"

load_dotenv(CONFIG_DIR / ".env")

LOOKUP_FILENAME = os.getenv("LOOKUP_FILE", "transaction-lookups.xlsx")
HISTORY_FILENAME = os.getenv("HISTORY_FILE", "transaction-history.xlsx")


def default_lookup_path() -> Path:
    name = Path(LOOKUP_FILENAME)
    if name.is_absolute() or len(name.parts) > 1:
        return (PROJECT_ROOT / name).resolve()
    return (SCRIPTS_DIR / name.name).resolve()


def default_history_path() -> Path:
    name = Path(HISTORY_FILENAME)
    if name.is_absolute() or len(name.parts) > 1:
        return (PROJECT_ROOT / name).resolve()
    return (SCRIPTS_DIR / name.name).resolve()


@dataclass
class PipelineConfig:
    """Options for run_pipeline (shared by CLI and web)."""

    lookup_path: Path | None = None
    history_path: Path | None = None
    skip_lookup_update: bool = False
    rebuild_description_lookup: bool = False
    skip_cadence_detection: bool = False
    provider: str = "auto"
    base_url: str | None = None
    model: str | None = None
    batch_size: int | None = None
    source_file: str = ""
    update_lookup_workbook: bool = True
    update_history: bool = False

    def resolved_lookup_path(self) -> Path:
        return self.lookup_path or default_lookup_path()

    def resolved_history_path(self) -> Path:
        return self.history_path or default_history_path()
