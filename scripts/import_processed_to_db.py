#!/usr/bin/env python3
"""
Load already-processed transactions into data/finance.db without re-running the LLM pipeline.

Sources:
  history  — scripts/transaction-history.xlsx (Income + Expenses + Adjustments)
  output   — output/*-Insights.xlsx (Raw Data sheet per monthly file)

Examples:
  python scripts/import_processed_to_db.py --dry-run
  python scripts/import_processed_to_db.py
  python scripts/import_processed_to_db.py --source output --output-dir output
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from transaction_insight.config import default_history_path
from transaction_insight.core import parse_amount, parse_transaction_dates
from webapp.adapters.dataframe_store import save_processed_dataframe
from webapp.config import DB_PATH
from webapp.db.schema import get_connection, init_db
from webapp.services.data_store import clear_data_store

from scripts.split_export_by_month import parse_monthly_export_name


def _drop_unnamed_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = [c for c in df.columns if not str(c).startswith("Unnamed")]
    return df.loc[:, keep]


def _normalize_ledger(df: pd.DataFrame) -> pd.DataFrame:
    out = _drop_unnamed_columns(df.copy())
    if "Transaction Date" in out.columns:
        out["Transaction Date"] = parse_transaction_dates(out["Transaction Date"])
    if "Amount" in out.columns:
        parsed_amount = out["Amount"].apply(parse_amount)
        if "Amount_Numeric" not in out.columns:
            out["Amount_Numeric"] = parsed_amount
        else:
            out["Amount_Numeric"] = out["Amount_Numeric"].where(
                out["Amount_Numeric"].notna(), parsed_amount
            )
    if "Transaction ID" in out.columns:
        out = out.dropna(subset=["Transaction ID"])
        out["Transaction ID"] = out["Transaction ID"].astype(str).str.strip()
        out = out[out["Transaction ID"] != ""]
    return out


def load_history_ledger(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)

    frames: list[pd.DataFrame] = []
    for sheet in ("Income", "Expenses", "Adjustments"):
        try:
            frame = pd.read_excel(path, sheet_name=sheet)
        except ValueError:
            continue
        if frame.empty:
            continue
        frames.append(_normalize_ledger(frame))

    if not frames:
        raise ValueError(f"No transaction rows found in {path}")

    combined = pd.concat(frames, ignore_index=True)
    if "Transaction ID" not in combined.columns:
        raise ValueError(f"{path} is missing Transaction ID column")

    if "Processed At" in combined.columns:
        combined = combined.sort_values("Processed At", kind="stable")
    combined = combined.drop_duplicates(subset=["Transaction ID"], keep="last")
    return combined.reset_index(drop=True)


def _sort_insights_paths(paths: list[Path]) -> list[Path]:
    def sort_key(path: Path) -> tuple[int, int, str]:
        fake_csv = path.stem.replace("-Insights", "") + ".csv"
        parsed = parse_monthly_export_name(fake_csv)
        if parsed is None:
            return (9999, 99, path.name)
        year, month = parsed
        return (year, month, path.name)

    return sorted(paths, key=sort_key)


def load_output_ledger(output_dir: Path) -> pd.DataFrame:
    paths = _sort_insights_paths(
        [p for p in output_dir.glob("*-Insights.xlsx") if p.is_file()]
    )
    if not paths:
        raise FileNotFoundError(f"No *-Insights.xlsx files in {output_dir}")

    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_excel(path, sheet_name="Raw Data")
        frame = _normalize_ledger(frame)
        source = str(frame["Source File"].iloc[0]).strip() if "Source File" in frame.columns else path.stem.replace("-Insights", "") + ".csv"
        if "Source File" not in frame.columns:
            frame["Source File"] = source
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["Transaction ID"], keep="last")
    return combined.reset_index(drop=True)


def _source_groups(df: pd.DataFrame, *, default_source: str) -> list[tuple[str, pd.DataFrame]]:
    if "Source File" in df.columns:
        groups: list[tuple[str, pd.DataFrame]] = []
        for source, group in df.groupby("Source File", sort=False):
            name = str(source or "").strip() or default_source
            groups.append((name, group.reset_index(drop=True)))
        return groups
    return [(default_source, df)]


def import_ledger(
    df: pd.DataFrame,
    *,
    db_path: Path,
    clear_all: bool = False,
    dry_run: bool = False,
    default_source: str = "imported-from-excel",
) -> dict[str, int]:
    if df.empty:
        return {"inserted": 0, "updated": 0, "rows": 0, "source_files": 0}

    if dry_run:
        groups = _source_groups(df, default_source=default_source)
        return {
            "inserted": 0,
            "updated": 0,
            "rows": len(df),
            "source_files": len(groups),
            "unique_transaction_ids": int(df["Transaction ID"].nunique()),
        }

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        if clear_all:
            clear_data_store(conn)

        inserted = updated = 0
        groups = _source_groups(df, default_source=default_source)
        for source_file, group in groups:
            stats = save_processed_dataframe(
                conn,
                group,
                source_file=source_file,
                clear_existing=False,
                replace_source_file=True,
            )
            inserted += stats["inserted"]
            updated += stats["updated"]

        return {
            "inserted": inserted,
            "updated": updated,
            "rows": len(df),
            "source_files": len(groups),
            "unique_transaction_ids": int(df["Transaction ID"].nunique()),
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import processed Excel transactions into the web app SQLite database."
    )
    parser.add_argument(
        "--source",
        choices=("history", "output"),
        default="history",
        help="history = transaction-history.xlsx; output = monthly *-Insights.xlsx files",
    )
    parser.add_argument(
        "--history-file",
        type=Path,
        default=None,
        help=f"History workbook (default: {default_history_path().relative_to(_ROOT)})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_ROOT / "output",
        help="Folder with *-Insights.xlsx when --source output",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"SQLite database path (default: {DB_PATH.relative_to(_ROOT)})",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear all web app tables before import (transactions, labels, chat, etc.)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts only; do not write to the database",
    )
    args = parser.parse_args()

    if args.source == "history":
        history_path = (args.history_file or default_history_path()).resolve()
        print(f"Loading history: {history_path}")
        ledger = load_history_ledger(history_path)
        default_source = history_path.name
    else:
        output_dir = args.output_dir.resolve()
        print(f"Loading monthly outputs: {output_dir}")
        ledger = load_output_ledger(output_dir)
        default_source = "imported-from-output.xlsx"

    print(f"  Rows: {len(ledger)}")
    print(f"  Unique Transaction IDs: {ledger['Transaction ID'].nunique()}")
    if "Flow Type" in ledger.columns:
        print(
            "  Flow types:",
            ledger["Flow Type"].value_counts().to_dict(),
        )

    stats = import_ledger(
        ledger,
        db_path=args.db.resolve(),
        clear_all=args.clear,
        dry_run=args.dry_run,
        default_source=default_source,
    )

    if args.dry_run:
        print(
            f"\nDry run — would import {stats['rows']} row(s) "
            f"from {stats['source_files']} source file group(s)."
        )
    else:
        print(
            f"\nImported {stats['rows']} row(s) "
            f"({stats['inserted']} inserted, {stats['updated']} updated) "
            f"into {args.db.resolve()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
