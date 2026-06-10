from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator

from transaction_insight.config import PipelineConfig, default_lookup_path
from transaction_insight.core import load_csv
from transaction_insight.inbox_archive import move_csv_to_processed
from transaction_insight.pipeline import run_pipeline
from webapp.adapters.dataframe_store import save_processed_dataframe
from webapp.config import INBOX_DIR, PROCESSED_DIR
from webapp.services.data_store import clear_data_store

ProgressCallback = Callable[[dict[str, Any]], None]


def list_inbox_csv_paths(
    inbox_dir: Path,
    *,
    filename: str | None = None,
) -> list[Path]:
    """Sorted inbox CSV paths; optional single-file filter."""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    if filename:
        path = inbox_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Not found in inbox: {filename}")
        return [path]
    return sorted(p for p in inbox_dir.glob("*.csv") if p.is_file())


def process_csv_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    skip_lookup_update: bool = False,
    skip_cadence_detection: bool = False,
    update_lookup_workbook: bool = True,
    clear_db_first: bool = False,
    on_progress: ProgressCallback | None = None,
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


def process_inbox_files(
    conn: sqlite3.Connection,
    paths: list[Path],
    *,
    skip_lookup_update: bool = False,
    skip_cadence_detection: bool = False,
    update_lookup_workbook: bool = True,
    clear_db_first: bool = False,
    on_progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """Run the pipeline on each CSV in order; optional scaled progress callbacks."""
    if not paths:
        return []

    total = len(paths)
    results: list[dict[str, Any]] = []

    for file_idx, path in enumerate(paths):
        if on_progress:
            on_progress(
                {
                    "type": "file_start",
                    "file": path.name,
                    "file_index": file_idx + 1,
                    "file_count": total,
                    "message": f"Processing file {file_idx + 1}/{total}: {path.name}",
                    "percent": round(file_idx / total * 100, 1) if total else 0,
                }
            )

        def file_progress(
            ev: dict[str, Any],
            *,
            _idx: int = file_idx,
            _total: int = total,
            _name: str = path.name,
        ) -> None:
            if not on_progress:
                return
            out = dict(ev)
            pct = out.get("percent")
            if isinstance(pct, (int, float)):
                slice_size = 100 / _total
                out["percent"] = round(_idx * slice_size + (pct / 100) * slice_size, 1)
            msg = out.get("message")
            if msg:
                out["message"] = f"{_name}: {msg}"
            on_progress(out)

        file_result = process_csv_file(
            conn,
            path,
            skip_lookup_update=skip_lookup_update,
            skip_cadence_detection=skip_cadence_detection,
            update_lookup_workbook=update_lookup_workbook,
            clear_db_first=clear_db_first and file_idx == 0,
            on_progress=file_progress if on_progress else None,
        )
        results.append(file_result)

        if on_progress:
            on_progress(
                {
                    "type": "file_done",
                    "file": path.name,
                    "file_index": file_idx + 1,
                    "file_count": total,
                    "result": file_result,
                    "message": (
                        f"Finished {path.name} — {file_result['rows']} row(s)"
                        + (
                            f", archived to {file_result['archived_to']}"
                            if file_result.get("archived_to")
                            else ""
                        )
                    ),
                    "percent": round((file_idx + 1) / total * 100, 1) if total else 100,
                }
            )

    return results


def iter_process_inbox(
    conn: sqlite3.Connection,
    inbox_dir: Path,
    *,
    filename: str | None = None,
    **kwargs: Any,
) -> Iterator[dict[str, Any]]:
    paths = list_inbox_csv_paths(inbox_dir, filename=filename)
    yield from process_inbox_files(conn, paths, **kwargs)
