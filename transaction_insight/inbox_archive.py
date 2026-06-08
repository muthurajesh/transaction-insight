from __future__ import annotations

import os
import shutil
from pathlib import Path


def move_processed_enabled() -> bool:
    return os.getenv("FINANCE_MOVE_PROCESSED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_processed_dir() -> Path:
    custom = os.getenv("FINANCE_PROCESSED_DIR", "").strip()
    if custom:
        return Path(custom)
    return PROJECT_ROOT / "processed"


def should_archive_inbox_csv(path: Path, inbox_dir: Path) -> bool:
    """Archive only top-level CSV files sitting directly in the inbox folder."""
    if not move_processed_enabled():
        return False
    if path.suffix.lower() != ".csv" or not path.is_file():
        return False
    try:
        rel = path.resolve().relative_to(inbox_dir.resolve())
    except ValueError:
        return False
    return len(rel.parts) == 1


def unique_destination(dest_dir: Path, filename: str) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 1
    while True:
        candidate = dest_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def move_csv_to_processed(
    path: Path,
    *,
    inbox_dir: Path,
    processed_dir: Path | None = None,
) -> Path | None:
    """
    Move a successfully processed inbox CSV into the processed folder.
    Returns the new path, or None if the file was not archived.
    """
    if not should_archive_inbox_csv(path, inbox_dir):
        return None

    dest_dir = processed_dir or resolve_processed_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(dest_dir, path.name)
    shutil.move(str(path), str(destination))
    return destination
