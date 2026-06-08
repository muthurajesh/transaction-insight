from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from webapp.services.ingest import ingest_csv

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def sanitize_csv_filename(name: str) -> str:
    base = Path(name or "").name
    if not base or base in (".", ".."):
        raise ValueError("Invalid filename")
    if not base.lower().endswith(".csv"):
        raise ValueError("Only .csv files are allowed")
    safe = re.sub(r"[^\w.\- ]+", "_", base).strip(" .")
    if not safe or not safe.lower().endswith(".csv"):
        raise ValueError("Invalid filename")
    return safe


def unique_inbox_path(inbox_dir: Path, filename: str) -> Path:
    dest = inbox_dir / filename
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    n = 1
    while True:
        candidate = inbox_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def save_upload_to_inbox(
    inbox_dir: Path,
    *,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    if not content.strip():
        raise ValueError("File is empty")

    inbox_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_csv_filename(filename)
    dest = unique_inbox_path(inbox_dir, safe_name)
    dest.write_bytes(content)
    return {
        "original_name": filename,
        "saved_as": dest.name,
        "path": str(dest),
        "bytes": len(content),
    }


def scan_uploaded_files(
    conn: sqlite3.Connection,
    paths: list[Path],
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in paths:
        try:
            stats = ingest_csv(conn, path, force=force)
            results.append({"file": path.name, "ok": True, **stats})
        except Exception as exc:
            results.append({"file": path.name, "ok": False, "error": str(exc)})
    return results
