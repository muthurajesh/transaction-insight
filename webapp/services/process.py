from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator

from webapp.adapters.dataframe_store import save_processed_dataframe
from webapp.config import INBOX_DIR, PROCESSED_DIR, PipelineConfig, default_lookup_path
from webapp.inbox import move_csv_to_processed
from webapp.pipeline import run_pipeline
from webapp.processing import load_csv
from webapp.processing.timer import PhaseTimer
from webapp.services.data_store import clear_data_store

ProgressCallback = Callable[[dict[str, Any]], None]

# Rough LLM pipeline seconds per transaction row (local models; override in .env).
_SECONDS_PER_ROW = float(os.getenv("PIPELINE_SECONDS_PER_ROW", "1.2"))


def _estimate_total_seconds(row_count: int) -> float:
    """Heuristic total duration from row count (~1 month ≈ 30–150 rows)."""
    if row_count <= 0:
        return 45.0
    # Base overhead + per-row LLM work (descriptions + classification batches).
    return max(45.0, 25.0 + row_count * _SECONDS_PER_ROW)


def _enrich_progress_event(
    ev: dict[str, Any],
    *,
    started: float,
    row_count: int,
    estimate_total_s: float,
) -> dict[str, Any]:
    out = dict(ev)
    elapsed = time.perf_counter() - started
    out["elapsed_s"] = round(elapsed, 1)
    out["row_count"] = row_count
    out["estimate_total_s"] = round(estimate_total_s, 1)

    pct = out.get("percent")
    if isinstance(pct, (int, float)) and pct > 2:
        linear_remaining = max(0.0, elapsed / (pct / 100.0) - elapsed)
        if pct < 20:
            row_remaining = max(0.0, estimate_total_s - elapsed)
            out["eta_s"] = round(0.55 * linear_remaining + 0.45 * row_remaining, 1)
        else:
            out["eta_s"] = round(linear_remaining, 1)
    elif elapsed < 3:
        out["eta_s"] = round(estimate_total_s, 1)

    return out


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
    file_index: int = 1,
    file_count: int = 1,
) -> dict[str, Any]:
    if clear_db_first:
        clear_data_store(conn)

    started = time.perf_counter()
    timer = PhaseTimer()
    df = load_csv(path)
    row_count = len(df)
    estimate_total_s = _estimate_total_seconds(row_count)

    def emit_progress(ev: dict[str, Any]) -> None:
        if on_progress:
            on_progress(
                _enrich_progress_event(
                    ev,
                    started=started,
                    row_count=row_count,
                    estimate_total_s=estimate_total_s,
                )
            )

    if on_progress:
        emit_progress(
            {
                "type": "file_start",
                "file": path.name,
                "file_index": file_index,
                "file_count": file_count,
                "message": (
                    f"Processing {path.name} — {row_count} transaction(s) "
                    f"(~{int(estimate_total_s // 60)}m {int(estimate_total_s % 60)}s estimated)"
                ),
                "percent": 0,
            }
        )

    config = PipelineConfig(
        lookup_path=default_lookup_path(),
        skip_lookup_update=skip_lookup_update,
        skip_cadence_detection=skip_cadence_detection,
        update_lookup_workbook=update_lookup_workbook,
        source_file=path.name,
    )
    result = run_pipeline(
        df,
        config,
        conn=conn,
        on_progress=emit_progress if on_progress else None,
        input_path=path,
        timer=timer,
    )
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
        "row_count": row_count,
        "elapsed_s": round(timer.total_seconds(), 1),
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
            file_index=file_idx + 1,
            file_count=total,
        )
        results.append(file_result)

        if on_progress:
            done_ev: dict[str, Any] = {
                "type": "file_done",
                "file": path.name,
                "file_index": file_idx + 1,
                "file_count": total,
                "result": file_result,
                "message": (
                    f"Finished {path.name} — {file_result['rows']} row(s)"
                    + (
                        f" in {file_result.get('elapsed_s', '?')}s"
                        if file_result.get("elapsed_s") is not None
                        else ""
                    )
                    + (
                        f", archived to {file_result['archived_to']}"
                        if file_result.get("archived_to")
                        else ""
                    )
                ),
                "percent": round((file_idx + 1) / total * 100, 1) if total else 100,
                "elapsed_s": file_result.get("elapsed_s"),
                "row_count": file_result.get("row_count"),
            }
            on_progress(done_ev)

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
