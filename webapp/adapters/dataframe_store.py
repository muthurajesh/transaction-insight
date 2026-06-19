from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import pandas as pd

import webapp.processing as core

from webapp.services.expense_cadence import (
    cadence_fields_from_pipeline_row,
    upsert_cadence_rule,
)


def save_processed_dataframe(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    *,
    source_file: str,
    clear_existing: bool = False,
    replace_source_file: bool = True,
) -> dict[str, int]:
    """
    Persist pipeline output to web SQLite. Uses Transaction ID from the shared core.
    """
    if clear_existing:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM merchant_labels")
        conn.execute("DELETE FROM ingested_files")

    if replace_source_file and source_file:
        conn.execute("DELETE FROM transactions WHERE source_file = ?", (source_file,))

    now = datetime.now(timezone.utc).isoformat()
    inserted = updated = 0

    for _, row in df.iterrows():
        tid = str(row.get("Transaction ID", "") or "").strip()
        if not tid:
            continue

        dt = row.get("Transaction Date")
        if pd.isna(dt):
            continue
        date_str = pd.Timestamp(dt).strftime("%Y-%m-%d")
        bm = str(row.get("Budget Month", "") or pd.Timestamp(dt).strftime("%Y-%m"))
        amt = float(row.get("Amount_Numeric", core.parse_amount(row.get("Amount", 0))))

        gd = str(row.get("Generated Description", "") or "").strip()
        mk_row = row.copy()
        mk_row["Generated Description"] = gd or core.heuristic_generated_description(row)
        mk = core.merchant_key(mk_row)

        flow = str(row.get("Flow Type", "") or "Expense")
        ai_cat = str(row.get("AI Category", "") or "")
        ai_sub = str(row.get("AI Sub-Category", "") or "")
        exp_type = str(row.get("Type", "") or "Variable")
        tier = str(row.get("Budget Tier", "") or "")
        if tier == "Review":
            label_status = "needs_review"
            confidence = 0.7
        else:
            label_status = "confirmed"
            confidence = 1.0

        existing = conn.execute(
            "SELECT id FROM transactions WHERE transaction_id = ?",
            (tid,),
        ).fetchone()

        cadence = cadence_fields_from_pipeline_row(row.to_dict())
        include_val = cadence.get("include_in_run_rate")
        include_db = None if include_val is None else (1 if include_val else 0)

        values = (
            source_file,
            date_str,
            bm,
            amt,
            str(row.get("Category", "") or ""),
            str(row.get("User Description", "") or ""),
            str(row.get("Simple Description", "") or ""),
            str(row.get("Original Description", "") or ""),
            mk,
            str(row.get("Account Name", "") or ""),
            str(row.get("Classification", "") or ""),
            flow,
            ai_cat,
            ai_sub,
            exp_type,
            confidence,
            label_status,
            "pipeline",
            now,
            cadence["cadence_kind"],
            cadence.get("period_count"),
            cadence.get("period_unit"),
            include_db,
            cadence.get("cadence_source") or "pipeline",
            cadence.get("cadence_note") or "",
        )

        if existing:
            conn.execute(
                """
                UPDATE transactions SET
                    source_file=?, date=?, budget_month=?, amount=?,
                    source_category=?, user_description=?, simple_description=?,
                    original_description=?, merchant_key=?, account_name=?,
                    classification=?, flow_type=?, ai_category=?, ai_sub_category=?,
                    expense_type=?, confidence=?, label_status=?, rationale=?, imported_at=?,
                    cadence_kind=?, period_count=?, period_unit=?,
                    include_in_run_rate=?, cadence_source=?, cadence_note=?
                WHERE transaction_id=?
                """,
                (*values, tid),
            )
            updated += 1
        else:
            conn.execute(
                """
                INSERT INTO transactions (
                    transaction_id, source_file, date, budget_month, amount,
                    source_category, user_description, simple_description,
                    original_description, merchant_key, account_name,
                    classification, flow_type, ai_category, ai_sub_category,
                    expense_type, confidence, label_status, rationale, imported_at,
                    cadence_kind, period_count, period_unit,
                    include_in_run_rate, cadence_source, cadence_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tid, *values),
            )
            inserted += 1

        if mk and cadence.get("cadence_kind") not in (None, "", "unknown"):
            upsert_cadence_rule(
                conn,
                merchant_key=mk,
                cadence_kind=cadence["cadence_kind"],
                period_count=cadence.get("period_count"),
                period_unit=cadence.get("period_unit"),
                include_in_run_rate=cadence.get("include_in_run_rate"),
                notes=cadence.get("cadence_note") or "",
                source=cadence.get("cadence_source") or "pipeline",
            )

        if mk and ai_cat:
            conn.execute(
                """
                INSERT INTO merchant_labels (
                    merchant_key, ai_category, ai_sub_category, expense_type,
                    confidence, label_status, rationale, sample_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pipeline', 0, ?)
                ON CONFLICT(merchant_key) DO UPDATE SET
                    ai_category=excluded.ai_category,
                    ai_sub_category=excluded.ai_sub_category,
                    expense_type=excluded.expense_type,
                    confidence=excluded.confidence,
                    label_status=excluded.label_status,
                    updated_at=excluded.updated_at
                """,
                (mk, ai_cat, ai_sub, exp_type, confidence, label_status, now),
            )

    conn.commit()
    return {"inserted": inserted, "updated": updated}
