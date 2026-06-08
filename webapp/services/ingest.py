from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from webapp.parsing import (
    account_name_from_row,
    budget_month_label,
    flow_type_from_amount,
    heuristic_generated_description,
    load_csv,
    merchant_key,
    parse_amount,
    parse_transaction_dates,
    transaction_id_from_row,
)


def file_content_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ingested_file_record(
    conn: sqlite3.Connection, filename: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT content_sha256, row_count FROM ingested_files WHERE filename = ?",
        (filename,),
    ).fetchone()


def _upsert_ingested_file(
    conn: sqlite3.Connection,
    filename: str,
    content_hash: str,
    row_count: int,
    *,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ingested_files (filename, content_sha256, row_count, last_ingested_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(filename) DO UPDATE SET
            content_sha256 = excluded.content_sha256,
            row_count = excluded.row_count,
            last_ingested_at = excluded.last_ingested_at
        """,
        (filename, content_hash, row_count, now),
    )


def _find_existing_transaction_id(
    conn: sqlite3.Connection,
    transaction_id: str,
    *,
    date_str: str,
    amount: float,
    account: str,
    original_description: str,
) -> str:
    """Resolve ID for upsert; matches legacy rows stored under an older hash scheme."""
    row = conn.execute(
        "SELECT transaction_id FROM transactions WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    if row:
        return str(row["transaction_id"])

    legacy = conn.execute(
        """
        SELECT transaction_id FROM transactions
        WHERE date = ? AND amount = ? AND account_name = ? AND original_description = ?
        LIMIT 1
        """,
        (date_str, amount, account, original_description),
    ).fetchone()
    if legacy:
        return str(legacy["transaction_id"])
    return transaction_id


def _row_snapshot(
    *,
    source_file: str,
    date_str: str,
    budget_month: str,
    amount: float,
    source_category: str,
    user_description: str,
    simple_description: str,
    original_description: str,
    merchant_key_val: str,
    account: str,
    classification: str,
    flow_type: str,
) -> tuple:
    return (
        source_file,
        date_str,
        budget_month,
        round(float(amount), 2),
        source_category,
        user_description,
        simple_description,
        original_description,
        merchant_key_val,
        account,
        classification,
        flow_type,
    )


def ingest_csv(
    conn: sqlite3.Connection,
    path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Import CSV rows into transactions. Re-importing the same file (unchanged bytes)
    is a no-op. Re-importing after edits upserts rows by stable transaction_id.
    """
    if not path.is_file():
        raise FileNotFoundError(path)

    content_hash = file_content_sha256(path)
    prior = _ingested_file_record(conn, path.name)
    if (
        not force
        and prior is not None
        and prior["content_sha256"] == content_hash
    ):
        return {
            "inserted": 0,
            "updated": 0,
            "skipped": int(prior["row_count"] or 0),
            "file_unchanged": True,
            "message": f"Skipped {path.name}: file unchanged since last import.",
        }

    df = load_csv(path)
    if "Date" not in df.columns:
        raise ValueError(f"CSV missing Date column: {path.name}")

    df["Transaction Date"] = parse_transaction_dates(df["Date"])
    df = df.loc[df["Transaction Date"].notna()].copy()
    if df.empty:
        now = datetime.now(timezone.utc).isoformat()
        _upsert_ingested_file(conn, path.name, content_hash, 0, now=now)
        conn.commit()
        return {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "file_unchanged": False,
            "message": "No rows with valid dates.",
        }

    df["_transaction_id"] = df.apply(
        lambda r: transaction_id_from_row(r, parsed_date=r["Transaction Date"]),
        axis=1,
    )
    before = len(df)
    df = df.drop_duplicates(subset=["_transaction_id"], keep="first")
    duplicates_in_file = before - len(df)

    now = datetime.now(timezone.utc).isoformat()
    inserted = updated = skipped = 0

    for _, row in df.iterrows():
        amt = round(float(row.get("Amount_Numeric", parse_amount(row.get("Amount", 0)))), 2)
        mk_row = row.copy()
        mk_row["Generated Description"] = heuristic_generated_description(row)
        mk = merchant_key(mk_row)
        dt = row["Transaction Date"]
        bm = budget_month_label(dt)
        date_str = dt.strftime("%Y-%m-%d")
        account = account_name_from_row(row)
        original = str(row.get("Original Description", "") or "")

        tid = _find_existing_transaction_id(
            conn,
            str(row["_transaction_id"]),
            date_str=date_str,
            amount=amt,
            account=account,
            original_description=original,
        )

        snapshot = _row_snapshot(
            source_file=path.name,
            date_str=date_str,
            budget_month=bm,
            amount=amt,
            source_category=str(row.get("Category", "") or ""),
            user_description=str(row.get("User Description", "") or ""),
            simple_description=str(row.get("Simple Description", "") or ""),
            original_description=original,
            merchant_key_val=mk,
            account=account,
            classification=str(row.get("Classification", "") or ""),
            flow_type=flow_type_from_amount(amt),
        )

        existing = conn.execute(
            """
            SELECT source_file, date, budget_month, amount, source_category,
                   user_description, simple_description, original_description,
                   merchant_key, account_name, classification, flow_type
            FROM transactions WHERE transaction_id = ?
            """,
            (tid,),
        ).fetchone()

        if existing:
            existing_snapshot = _row_snapshot(
                source_file=str(existing["source_file"] or ""),
                date_str=str(existing["date"] or ""),
                budget_month=str(existing["budget_month"] or ""),
                amount=float(existing["amount"] or 0),
                source_category=str(existing["source_category"] or ""),
                user_description=str(existing["user_description"] or ""),
                simple_description=str(existing["simple_description"] or ""),
                original_description=str(existing["original_description"] or ""),
                merchant_key_val=str(existing["merchant_key"] or ""),
                account=str(existing["account_name"] or ""),
                classification=str(existing["classification"] or ""),
                flow_type=str(existing["flow_type"] or ""),
            )
            if existing_snapshot == snapshot:
                skipped += 1
                continue

            conn.execute(
                """
                UPDATE transactions SET
                    source_file=?, date=?, budget_month=?, amount=?,
                    source_category=?, user_description=?, simple_description=?,
                    original_description=?, merchant_key=?, account_name=?,
                    classification=?, flow_type=?, imported_at=?
                WHERE transaction_id=?
                """,
                (*snapshot, now, tid),
            )
            updated += 1
        else:
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, source_file, date, budget_month, amount,
                    source_category, user_description, simple_description,
                    original_description, merchant_key, account_name,
                    classification, flow_type, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tid, *snapshot, now),
            )
            inserted += 1

    _upsert_ingested_file(conn, path.name, content_hash, len(df), now=now)
    conn.commit()

    msg_parts = [
        f"{inserted} new",
        f"{updated} updated",
        f"{skipped} unchanged",
    ]
    if duplicates_in_file:
        msg_parts.append(f"{duplicates_in_file} duplicate row(s) in file ignored")
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "duplicates_in_file": duplicates_in_file,
        "file_unchanged": False,
        "message": "; ".join(msg_parts),
    }


def scan_inbox(
    conn: sqlite3.Connection,
    inbox_dir: Path,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for path in sorted(inbox_dir.glob("*.csv")):
        try:
            stats = ingest_csv(conn, path, force=force)
            results.append({"file": path.name, "ok": True, **stats})
        except Exception as exc:
            results.append({"file": path.name, "ok": False, "error": str(exc)})
    return results
