#!/usr/bin/env python3
"""
Split a combined bank export CSV into one file per calendar month.

Example:
  python scripts/split_export_by_month.py
  python scripts/split_export_by_month.py --output-dir input
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from transaction_insight.core import parse_transaction_dates

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

DEFAULT_INPUT = _ROOT / "original-data" / "ExportData-all-data-until-May-2026.csv"
DEFAULT_OUTPUT_DIR = _ROOT / "input"

_MONTH_NAME_TO_NUM = {name.lower(): num for num, name in enumerate(MONTH_NAMES, start=1)}
_EXPORT_NAME_RE = re.compile(r"^ExportData-(.+)-(\d{4})\.csv$", re.IGNORECASE)


def month_filename(year: int, month: int) -> str:
    return f"ExportData-{MONTH_NAMES[month - 1]}-{year}.csv"


def parse_monthly_export_name(filename: str) -> tuple[int, int] | None:
    """Parse ``ExportData-July-2021.csv`` → ``(2021, 7)``; ``None`` if not matched."""
    match = _EXPORT_NAME_RE.match(filename)
    if not match:
        return None
    month_num = _MONTH_NAME_TO_NUM.get(match.group(1).strip().lower())
    if month_num is None:
        return None
    return int(match.group(2)), month_num


def sort_monthly_export_paths(paths: list[Path]) -> list[Path]:
    """Sort ``ExportData-<Month>-<Year>.csv`` paths in calendar order."""

    def sort_key(path: Path) -> tuple[int, int, str]:
        parsed = parse_monthly_export_name(path.name)
        if parsed is None:
            return (9999, 99, path.name)
        year, month = parsed
        return (year, month, path.name)

    return sorted(paths, key=sort_key)


def list_sorted_exports(directory: Path) -> list[Path]:
    paths = [
        path
        for path in directory.glob("ExportData-*.csv")
        if parse_monthly_export_name(path.name) is not None
    ]
    return sort_monthly_export_paths(paths)


def split_export(
    input_path: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
) -> list[tuple[str, int]]:
    df = pd.read_csv(
        input_path,
        dtype=str,
        encoding="utf-8-sig",
        engine="python",
        on_bad_lines="skip",
    )
    df.columns = [c.strip() for c in df.columns]
    if "Date" not in df.columns:
        raise ValueError(f"Missing 'Date' column in {input_path}")

    dates = parse_transaction_dates(df["Date"])
    bad = dates.isna()
    if bad.any():
        n_bad = int(bad.sum())
        examples = df.loc[bad, "Date"].astype(str).head(5).tolist()
        raise ValueError(
            f"{n_bad} row(s) have unparseable dates (examples: {examples})"
        )

    df = df.assign(_txn_date=dates)
    df["_year"] = df["_txn_date"].dt.year
    df["_month"] = df["_txn_date"].dt.month

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, int]] = []

    for (year, month), group in df.groupby(["_year", "_month"], sort=True):
        out_name = month_filename(int(year), int(month))
        out_path = output_dir / out_name
        out_df = group.drop(columns=["_txn_date", "_year", "_month"])
        row_count = len(out_df)
        if dry_run:
            print(f"  {out_name}: {row_count} row(s)")
        else:
            out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"  {out_name}: {row_count} row(s) -> {out_path}")
        written.append((out_name, row_count))

    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Split a combined export CSV into monthly ExportData-<Month>-<Year>.csv files."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Combined CSV (default: {DEFAULT_INPUT.relative_to(_ROOT)})",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output folder (default: {DEFAULT_OUTPUT_DIR.relative_to(_ROOT)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned files without writing",
    )
    parser.add_argument(
        "--list-sorted",
        type=Path,
        metavar="DIR",
        help="Print ExportData-*.csv paths in chronological order (one per line)",
    )
    args = parser.parse_args()

    if args.list_sorted is not None:
        directory = args.list_sorted.resolve()
        if not directory.is_dir():
            print(f"Not a directory: {directory}", file=sys.stderr)
            return 1
        paths = list_sorted_exports(directory)
        if not paths:
            print(f"No ExportData-*.csv files in {directory}", file=sys.stderr)
            return 1
        for path in paths:
            print(path)
        return 0

    input_path = args.input.resolve()
    if not input_path.is_file():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 1

    print(f"Input:  {input_path}")
    print(f"Output: {args.output_dir.resolve()}")
    if args.dry_run:
        print("Dry run — no files written:")
    else:
        print("Writing:")

    written = split_export(input_path, args.output_dir, dry_run=args.dry_run)
    total_rows = sum(n for _, n in written)
    print(
        f"\n{'Would write' if args.dry_run else 'Wrote'} "
        f"{len(written)} file(s), {total_rows} transaction row(s) total."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
