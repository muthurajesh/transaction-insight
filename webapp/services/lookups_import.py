from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from webapp.services.categorize import _apply_merchant_label
from webapp.services.expense_cadence import sync_cadence_rules_from_lookup_records


def default_lookup_workbook_path() -> Path:
    from webapp.config import default_lookup_path

    return default_lookup_path()


def _cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _upsert_merchant_label(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str,
    expense_type: str,
    label_status: str = "confirmed",
    confidence: float = 1.0,
    rationale: str,
    now: str,
) -> bool:
    mk = merchant_key.strip()
    if not mk or not ai_category.strip():
        return False

    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            ai_category=excluded.ai_category,
            ai_sub_category=excluded.ai_sub_category,
            expense_type=excluded.expense_type,
            confidence=excluded.confidence,
            label_status=excluded.label_status,
            rationale=excluded.rationale,
            updated_at=excluded.updated_at
        """,
        (
            mk,
            ai_category.strip(),
            ai_sub_category.strip(),
            expense_type or "Variable",
            confidence,
            label_status,
            rationale,
            now,
        ),
    )
    return True


def import_lookup_workbook(
    conn: sqlite3.Connection,
    path: Path,
) -> dict[str, Any]:
    """
    Load merchant labels and category hints from scripts/transaction-lookups.xlsx.

    - BusinessCategoryRules + DescriptionLookup → merchant_labels (by Generated Description)
    - CategoryRules → updates transactions matched by bank source_category
    - CustomRules + ExpenseCadenceRules → stored in lookup_snapshots for later use
    """
    if not path.is_file():
        raise FileNotFoundError(f"Lookup workbook not found: {path}")

    now = datetime.now(timezone.utc).isoformat()
    xl = pd.ExcelFile(path)
    sheets = {name: pd.read_excel(path, sheet_name=name) for name in xl.sheet_names}

    merchant_labels_upserted = 0
    transactions_updated = 0

    business = sheets.get("BusinessCategoryRules")
    if business is not None and not business.empty:
        for _, row in business.iterrows():
            mk = _cell_str(row.get("Generated Description"))
            cat = _cell_str(row.get("AI Category"))
            sub = _cell_str(row.get("AI Sub-Category"))
            exp_type = _cell_str(row.get("Type")) or "Variable"
            if _upsert_merchant_label(
                conn,
                mk,
                ai_category=cat,
                ai_sub_category=sub,
                expense_type=exp_type,
                rationale="import: BusinessCategoryRules",
                now=now,
            ):
                merchant_labels_upserted += 1
                flow = "Expense"
                if _cell_str(row.get("Classification")).lower() == "business":
                    flow = "Expense"
                transactions_updated += _apply_merchant_label(
                    conn, mk, flow, cat, sub, exp_type, 1.0, "confirmed",
                    "import: BusinessCategoryRules",
                )

    desc = sheets.get("DescriptionLookup")
    seen_desc: set[str] = set()
    if desc is not None and not desc.empty:
        for _, row in desc.iterrows():
            mk = _cell_str(row.get("Generated Description"))
            if not mk or mk.lower() in seen_desc:
                continue
            seen_desc.add(mk.lower())
            existing = conn.execute(
                "SELECT merchant_key FROM merchant_labels WHERE merchant_key = ?",
                (mk,),
            ).fetchone()
            if existing:
                continue
            if _upsert_merchant_label(
                conn,
                mk,
                ai_category="Other",
                ai_sub_category=mk[:80],
                expense_type="Variable",
                label_status="auto",
                confidence=0.9,
                rationale="import: DescriptionLookup (description only)",
                now=now,
            ):
                merchant_labels_upserted += 1

    categories = sheets.get("Categories")
    if categories is not None and not categories.empty:
        for _, row in categories.iterrows():
            sub = _cell_str(row.get("AI Sub-Category"))
            cat = _cell_str(row.get("AI Category"))
            exp_type = _cell_str(row.get("Type")) or "Variable"
            if not sub:
                continue
            existing = conn.execute(
                "SELECT merchant_key FROM merchant_labels WHERE LOWER(merchant_key) = LOWER(?)",
                (sub,),
            ).fetchone()
            if existing:
                if _upsert_merchant_label(
                    conn,
                    str(existing["merchant_key"]),
                    ai_category=cat or "Other",
                    ai_sub_category=sub,
                    expense_type=exp_type,
                    rationale="import: Categories",
                    now=now,
                ):
                    merchant_labels_upserted += 1
            elif _upsert_merchant_label(
                conn,
                sub,
                ai_category=cat or "Other",
                ai_sub_category=sub,
                expense_type=exp_type,
                rationale="import: Categories (sub-category as merchant key)",
                now=now,
            ):
                merchant_labels_upserted += 1
                transactions_updated += _apply_merchant_label(
                    conn, sub, "Expense", cat or "Other", sub, exp_type, 1.0, "confirmed",
                    "import: Categories",
                )

    category_rules = sheets.get("CategoryRules")
    rules_applied = 0
    if category_rules is not None and not category_rules.empty:
        for _, row in category_rules.iterrows():
            src = _cell_str(row.get("Source Category"))
            cat = _cell_str(row.get("AI Category"))
            exp_type = _cell_str(row.get("Type")) or "Variable"
            if not src or not cat:
                continue
            cur = conn.execute(
                """
                UPDATE transactions SET
                    ai_category = ?,
                    ai_sub_category = COALESCE(NULLIF(ai_sub_category, ''), ?),
                    expense_type = ?,
                    label_status = 'confirmed',
                    confidence = 1.0,
                    rationale = 'import: CategoryRules'
                WHERE source_category = ?
                """,
                (cat, src, exp_type, src),
            )
            rules_applied += cur.rowcount
        transactions_updated += rules_applied

    snapshot_rows = 0
    cadence_rules_synced = 0
    for sheet_name in ("CustomRules", "ExpenseCadenceRules"):
        frame = sheets.get(sheet_name)
        if frame is None or frame.empty:
            continue
        payload = frame.fillna("").astype(str).to_dict(orient="records")
        conn.execute(
            """
            INSERT INTO lookup_snapshots (sheet_name, payload_json, imported_at)
            VALUES (?, ?, ?)
            ON CONFLICT(sheet_name) DO UPDATE SET
                payload_json = excluded.payload_json,
                imported_at = excluded.imported_at
            """,
            (sheet_name, json.dumps(payload), now),
        )
        snapshot_rows += len(payload)
        if sheet_name == "ExpenseCadenceRules":
            cadence_rules_synced = sync_cadence_rules_from_lookup_records(conn, payload)

    labels = conn.execute(
        "SELECT merchant_key, ai_category, ai_sub_category, expense_type, label_status, confidence, rationale FROM merchant_labels"
    ).fetchall()
    for row in labels:
        mk = row["merchant_key"]
        transactions_updated += _apply_merchant_label(
            conn,
            mk,
            "Expense",
            row["ai_category"],
            row["ai_sub_category"] or "",
            row["expense_type"] or "Variable",
            float(row["confidence"] or 1.0),
            row["label_status"] or "confirmed",
            row["rationale"] or "import: lookup sync",
        )

    conn.commit()
    return {
        "file": path.name,
        "sheets_found": list(xl.sheet_names),
        "merchant_labels_upserted": merchant_labels_upserted,
        "transactions_updated": transactions_updated,
        "category_rules_rows_applied": rules_applied,
        "snapshot_rows": snapshot_rows,
        "cadence_rules_synced": cadence_rules_synced,
        "message": (
            f"Imported from {path.name}: {merchant_labels_upserted} merchant label(s), "
            f"{transactions_updated} transaction row(s) touched."
        ),
    }
