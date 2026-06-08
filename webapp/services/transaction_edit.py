from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

MATCH_SCOPES = frozenset({"single", "merchant_amount", "merchant"})
CLASSIFICATION_OPTIONS = ("Personal", "Business")
SORT_COLUMNS = {
    "date": "date",
    "amount": "amount",
    "merchant": "merchant_key",
    "ai_category": "ai_category",
}


def _order_clause(sort_by: str, sort_dir: str) -> str:
    col = SORT_COLUMNS.get((sort_by or "").strip().lower(), "date")
    direction = "ASC" if str(sort_dir or "").lower() == "asc" else "DESC"
    if col == "date":
        return f"date {direction}, id {direction}"
    return f"{col} {direction}, date DESC, id DESC"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def search_transactions(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    classification: str = "",
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses = ["1=1"]
    params: list[Any] = []

    text = (q or "").strip()
    if text:
        like = f"%{text}%"
        clauses.append(
            """(
                merchant_key LIKE ? COLLATE NOCASE
                OR COALESCE(simple_description, '') LIKE ? COLLATE NOCASE
                OR COALESCE(user_description, '') LIKE ? COLLATE NOCASE
                OR COALESCE(original_description, '') LIKE ? COLLATE NOCASE
            )"""
        )
        params.extend([like, like, like, like])

    month = (month or "").strip()
    if month:
        clauses.append("budget_month = ?")
        params.append(month)

    category = (category or "").strip()
    if category:
        clauses.append("ai_category = ?")
        params.append(category)

    sub_category = (sub_category or "").strip()
    if sub_category:
        clauses.append("ai_sub_category = ?")
        params.append(sub_category)

    expense_type = (expense_type or "").strip()
    if expense_type:
        clauses.append("expense_type = ?")
        params.append(expense_type)

    classification = (classification or "").strip()
    if classification:
        clauses.append("classification = ?")
        params.append(classification)

    where = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM transactions WHERE {where}",
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT transaction_id,
               date,
               budget_month,
               amount,
               merchant_key,
               COALESCE(NULLIF(simple_description, ''), NULLIF(user_description, ''),
                        NULLIF(original_description, '')) AS description,
               flow_type,
               ai_category,
               ai_sub_category,
               expense_type,
               classification,
               label_status
        FROM transactions
        WHERE {where}
        ORDER BY {_order_clause(sort_by, sort_dir)}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    return {
        "transactions": [_row_to_dict(r) for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by if sort_by in SORT_COLUMNS else "date",
        "sort_dir": "asc" if str(sort_dir).lower() == "asc" else "desc",
    }


def get_transaction(conn: sqlite3.Connection, transaction_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT transaction_id,
               date,
               budget_month,
               amount,
               merchant_key,
               COALESCE(NULLIF(simple_description, ''), NULLIF(user_description, ''),
                        NULLIF(original_description, '')) AS description,
               flow_type,
               ai_category,
               ai_sub_category,
               expense_type,
               classification,
               label_status,
               cadence_kind,
               period_count,
               period_unit,
               include_in_run_rate,
               cadence_source,
               cadence_note
        FROM transactions
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_matching_transactions(
    conn: sqlite3.Connection,
    transaction_id: str,
    *,
    scope: str = "single",
) -> list[dict[str, Any]]:
    if scope not in MATCH_SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(sorted(MATCH_SCOPES))}")

    source = get_transaction(conn, transaction_id)
    if not source:
        raise ValueError(f"Transaction not found: {transaction_id}")

    if scope == "single":
        return [source]

    merchant_key = source["merchant_key"]
    if scope == "merchant":
        rows = conn.execute(
            """
            SELECT transaction_id,
                   date,
                   budget_month,
                   amount,
                   merchant_key,
                   COALESCE(NULLIF(simple_description, ''), NULLIF(user_description, ''),
                            NULLIF(original_description, '')) AS description,
                   flow_type,
                   ai_category,
                   ai_sub_category,
                   expense_type,
                   classification,
                   label_status
            FROM transactions
            WHERE merchant_key = ?
            ORDER BY date DESC, id DESC
            LIMIT 500
            """,
            (merchant_key,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # merchant_amount — same merchant_key and exact amount
    amount = source["amount"]
    rows = conn.execute(
        """
        SELECT transaction_id,
               date,
               budget_month,
               amount,
               merchant_key,
               COALESCE(NULLIF(simple_description, ''), NULLIF(user_description, ''),
                        NULLIF(original_description, '')) AS description,
               flow_type,
               ai_category,
               ai_sub_category,
               expense_type,
               classification,
               label_status
        FROM transactions
        WHERE merchant_key = ? AND amount = ?
        ORDER BY date DESC, id DESC
        LIMIT 500
        """,
        (merchant_key, amount),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def bulk_update_labels(
    conn: sqlite3.Connection,
    *,
    transaction_ids: list[str],
    ai_category: str,
    ai_sub_category: str = "",
    flow_type: str | None = None,
    expense_type: str | None = None,
    classification: str | None = None,
    update_merchant_label: bool = False,
    merchant_key: str | None = None,
) -> dict[str, Any]:
    category = (ai_category or "").strip()
    if not category:
        raise ValueError("ai_category is required")

    ids = [str(t).strip() for t in transaction_ids if str(t).strip()]
    if not ids:
        raise ValueError("At least one transaction_id is required")

    sub = (ai_sub_category or "").strip()
    placeholders = ",".join("?" * len(ids))

    if update_merchant_label and merchant_key:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO merchant_labels (
                merchant_key, ai_category, ai_sub_category, expense_type,
                confidence, label_status, rationale, sample_count, updated_at
            ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'user edited', 0, ?)
            ON CONFLICT(merchant_key) DO UPDATE SET
                ai_category=excluded.ai_category,
                ai_sub_category=excluded.ai_sub_category,
                expense_type=COALESCE(excluded.expense_type, merchant_labels.expense_type),
                confidence=1.0,
                label_status='confirmed',
                rationale='user edited',
                updated_at=excluded.updated_at
            """,
            (
                merchant_key,
                category,
                sub,
                expense_type or "Variable",
                now,
            ),
        )

    set_parts = [
        "ai_category = ?",
        "ai_sub_category = ?",
        "confidence = 1.0",
        "label_status = 'confirmed'",
        "rationale = 'user edited'",
    ]
    values: list[Any] = [category, sub]

    if flow_type:
        set_parts.append("flow_type = ?")
        values.append(flow_type)
    if expense_type:
        set_parts.append("expense_type = ?")
        values.append(expense_type)
    if classification:
        cls = classification.strip()
        if cls not in CLASSIFICATION_OPTIONS:
            raise ValueError(f"classification must be one of: {', '.join(CLASSIFICATION_OPTIONS)}")
        set_parts.append("classification = ?")
        values.append(cls)

    values.extend(ids)
    cur = conn.execute(
        f"""
        UPDATE transactions
        SET {", ".join(set_parts)}
        WHERE transaction_id IN ({placeholders})
        """,
        values,
    )
    conn.commit()
    return {"rows_updated": cur.rowcount, "transaction_ids": ids}
