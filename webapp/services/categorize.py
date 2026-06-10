from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

# Merchants reviewed one transaction at a time (amounts/categories differ per check).
SPLIT_REVIEW_MERCHANT_KEYS: frozenset[str] = frozenset({"Check Payment"})
CLASSIFICATION_OPTIONS = frozenset({"Personal", "Business"})


def _apply_merchant_label(
    conn: sqlite3.Connection,
    merchant_key: str,
    section: str,
    category: str,
    sub: str,
    exp_type: str,
    conf: float,
    status: str,
    rationale: str,
) -> int:
    flow = section if section in ("Income", "Expense") else "Expense"
    cur = conn.execute(
        """
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            confidence = ?,
            label_status = ?,
            rationale = ?
        WHERE merchant_key = ?
        """,
        (flow, category, sub, exp_type, conf, status, rationale, merchant_key),
    )
    return cur.rowcount


def _normalize_classification(value: str) -> str:
    cls = (value or "Personal").strip()
    if cls not in CLASSIFICATION_OPTIONS:
        raise ValueError(f"classification must be one of: {', '.join(sorted(CLASSIFICATION_OPTIONS))}")
    return cls


def confirm_merchant(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
) -> int:
    classification = _normalize_classification(classification)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at
        ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'user confirmed', 0, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            ai_category=excluded.ai_category,
            ai_sub_category=excluded.ai_sub_category,
            expense_type=excluded.expense_type,
            confidence=1.0,
            label_status='confirmed',
            updated_at=excluded.updated_at
        """,
        (merchant_key, ai_category, ai_sub_category, expense_type, now),
    )
    cur = conn.execute(
        """
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            classification = ?,
            confidence = 1.0,
            label_status = 'confirmed',
            rationale = 'user confirmed'
        WHERE merchant_key = ?
          AND label_status IN ('needs_review', 'pending')
        """,
        (flow_type, ai_category, ai_sub_category, expense_type, classification, merchant_key),
    )
    conn.commit()
    return cur.rowcount


def confirm_transaction(
    conn: sqlite3.Connection,
    transaction_id: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
) -> int:
    classification = _normalize_classification(classification)
    cur = conn.execute(
        """
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            classification = ?,
            confidence = 1.0,
            label_status = 'confirmed',
            rationale = 'user confirmed'
        WHERE transaction_id = ?
          AND label_status IN ('needs_review', 'pending')
        """,
        (flow_type, ai_category, ai_sub_category, expense_type, classification, transaction_id),
    )
    conn.commit()
    return cur.rowcount


def list_review_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    split_keys = tuple(SPLIT_REVIEW_MERCHANT_KEYS)
    items: list[dict[str, Any]] = []

    if split_keys:
        placeholders = ",".join("?" * len(split_keys))
        grouped = conn.execute(
            f"""
            SELECT merchant_key,
                   COUNT(*) AS transaction_count,
                   SUM(CASE WHEN amount < 0 THEN -amount ELSE amount END) AS total_spend,
                   MAX(ai_category) AS ai_category,
                   MAX(ai_sub_category) AS ai_sub_category,
                   MAX(flow_type) AS flow_type,
                   MAX(expense_type) AS expense_type,
                   MAX(classification) AS classification,
                   MAX(confidence) AS confidence,
                   MAX(label_status) AS label_status,
                   MIN(simple_description) AS sample_description
            FROM transactions
            WHERE label_status IN ('needs_review', 'pending')
              AND merchant_key NOT IN ({placeholders})
            GROUP BY merchant_key
            ORDER BY transaction_count DESC
            """,
            split_keys,
        ).fetchall()
    else:
        grouped = conn.execute(
            """
            SELECT merchant_key,
                   COUNT(*) AS transaction_count,
                   SUM(CASE WHEN amount < 0 THEN -amount ELSE amount END) AS total_spend,
                   MAX(ai_category) AS ai_category,
                   MAX(ai_sub_category) AS ai_sub_category,
                   MAX(flow_type) AS flow_type,
                   MAX(expense_type) AS expense_type,
                   MAX(classification) AS classification,
                   MAX(confidence) AS confidence,
                   MAX(label_status) AS label_status,
                   MIN(simple_description) AS sample_description
            FROM transactions
            WHERE label_status IN ('needs_review', 'pending')
            GROUP BY merchant_key
            ORDER BY transaction_count DESC
            """
        ).fetchall()

    for row in grouped:
        item = dict(row)
        item["review_mode"] = "merchant"
        items.append(item)

    if split_keys:
        placeholders = ",".join("?" * len(split_keys))
        split_rows = conn.execute(
            f"""
            SELECT transaction_id,
                   merchant_key,
                   date,
                   amount,
                   ai_category,
                   ai_sub_category,
                   flow_type,
                   expense_type,
                   classification,
                   confidence,
                   label_status,
                   COALESCE(NULLIF(simple_description, ''), NULLIF(user_description, ''),
                            NULLIF(original_description, '')) AS sample_description
            FROM transactions
            WHERE label_status IN ('needs_review', 'pending')
              AND merchant_key IN ({placeholders})
            ORDER BY date DESC, id DESC
            """,
            split_keys,
        ).fetchall()
        for row in split_rows:
            item = dict(row)
            item["review_mode"] = "transaction"
            item["transaction_count"] = 1
            items.append(item)

    return items


def list_merchant_transactions(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    review_only: bool = False,
) -> list[dict[str, Any]]:
    status_filter = (
        " AND label_status IN ('needs_review', 'pending')" if review_only else ""
    )
    rows = conn.execute(
        f"""
        SELECT date,
               budget_month,
               amount,
               source_category,
               account_name,
               user_description,
               simple_description,
               original_description,
               classification,
               flow_type,
               ai_category,
               ai_sub_category,
               expense_type,
               label_status,
               source_file
        FROM transactions
        WHERE merchant_key = ?{status_filter}
        ORDER BY date DESC, id DESC
        """,
        (merchant_key,),
    ).fetchall()
    return [dict(r) for r in rows]
