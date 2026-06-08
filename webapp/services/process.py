from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from transaction_insight.config import PipelineConfig, default_lookup_path
from transaction_insight.core import load_csv
from transaction_insight.inbox_archive import move_csv_to_processed
from transaction_insight.pipeline import run_pipeline
from webapp.adapters.dataframe_store import save_processed_dataframe
from webapp.config import INBOX_DIR, PROCESSED_DIR
from webapp.services.data_store import clear_data_store


def process_csv_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    skip_lookup_update: bool = False,
    skip_cadence_detection: bool = False,
    update_lookup_workbook: bool = True,
    clear_db_first: bool = False,
    on_progress: Any = None,
) -> dict[str, Any]:
    if clear_db_first:
        clear_data_store(conn)

    df = load_csv(path)
    config = PipelineConfig(
        lookup_path=default_lookup_path(),
        skip_lookup_update=skip_lookup_update,
        skip_cadence_detection=skip_cadence_detection,
        update_lookup_workbook=update_lookup_workbook,
        update_history=False,
        source_file=path.name,
    )
    result = run_pipeline(df, config, on_progress=on_progress, input_path=path)
    save_stats = save_processed_dataframe(
        conn, result.dataframe, source_file=path.name, clear_existing=False
    )
    archived_to = move_csv_to_processed(
        path, inbox_dir=INBOX_DIR, processed_dir=PROCESSED_DIR
    )
    return {
        "file": path.name,
        "pipeline_stats": result.stats,
        "save_stats": save_stats,
        "rows": len(result.dataframe),
        "archived": archived_to is not None,
        "archived_to": str(archived_to) if archived_to else None,
    }


def iter_process_inbox(
    conn: sqlite3.Connection,
    inbox_dir: Path,
    *,
    filename: str | None = None,
    **kwargs: Any,
) -> Iterator[dict[str, Any]]:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(inbox_dir.glob("*.csv"))
    if filename:
        paths = [inbox_dir / filename]
    for path in paths:
        if not path.is_file():
            continue
        yield process_csv_file(conn, path, **kwargs)
