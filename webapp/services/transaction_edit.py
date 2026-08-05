from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from webapp.services.expense_cadence import (
    CADENCE_KINDS,
    CADENCE_KIND_EXCLUDE,
    CADENCE_KIND_LUMP,
    CADENCE_KIND_ONE_TIME,
    CADENCE_KIND_RECURRING,
    CADENCE_KIND_UNKNOWN,
    cadence_kind_label,
    expense_cadence_period_label,
    include_in_run_rate_label,
    upsert_cadence_rule,
)

MATCH_SCOPES = frozenset({"single", "merchant_amount", "merchant"})
CADENCE_SCOPES = frozenset({"transaction", "merchant"})
CLASSIFICATION_OPTIONS = ("Personal", "Business")
FLOW_TYPE_OPTIONS = ("Expense", "Income", "Transfer", "Adjustment")
SORT_COLUMNS = {
    "date": "t.date",
    "amount": "t.amount",
    "budget_month": "t.budget_month",
    "merchant": "t.merchant_key",
    "merchant_key": "t.merchant_key",
    "simple_description": "t.simple_description",
    "user_description": "t.user_description",
    "original_description": "t.original_description",
    "source_category": "t.source_category",
    "account_name": "t.account_name",
    "flow_type": "t.flow_type",
    "ai_category": "t.ai_category",
    "ai_sub_category": "t.ai_sub_category",
    "expense_type": "t.expense_type",
    "classification": "t.classification",
    "label_status": "t.label_status",
    "confidence": "t.confidence",
    "imported_at": "t.imported_at",
    "cadence_kind": "effective_cadence_kind",
    "include_in_run_rate": "effective_in_run_rate",
    "transaction_id": "t.transaction_id",
    "source_file": "t.source_file",
}

# Display metadata for Edit Transactions result columns (keys match row dict fields).
RESULT_COLUMNS: list[dict[str, Any]] = [
    {"key": "date", "label": "Date", "sortable": True, "default_visible": True},
    {"key": "amount", "label": "Amount", "sortable": True, "default_visible": True},
    {"key": "simple_description", "label": "Simple description", "sortable": True, "default_visible": True},
    {"key": "merchant_key", "label": "Merchant", "sortable": True, "sort_key": "merchant", "default_visible": True},
    {"key": "ai_category", "label": "Category", "sortable": True, "default_visible": True},
    {"key": "ai_sub_category", "label": "Sub-category", "sortable": True, "default_visible": True},
    {"key": "expense_type", "label": "Type", "sortable": True, "default_visible": True},
    {"key": "classification", "label": "Class", "sortable": True, "default_visible": True},
    {"key": "label_status", "label": "Status", "sortable": True, "default_visible": True},
    {"key": "transaction_id", "label": "Transaction ID", "sortable": True, "default_visible": False},
    {"key": "budget_month", "label": "Budget month", "sortable": True, "default_visible": False},
    {"key": "flow_type", "label": "Flow type", "sortable": True, "default_visible": False},
    {"key": "source_category", "label": "Bank category", "sortable": True, "default_visible": False},
    {"key": "user_description", "label": "User description", "sortable": True, "default_visible": False},
    {"key": "original_description", "label": "Original description", "sortable": True, "default_visible": False},
    {"key": "description", "label": "Description", "sortable": False, "default_visible": False},
    {"key": "account_name", "label": "Account", "sortable": True, "default_visible": False},
    {"key": "source_file", "label": "Source file", "sortable": True, "default_visible": False},
    {"key": "confidence", "label": "Confidence", "sortable": True, "default_visible": False},
    {"key": "rationale", "label": "Rationale", "sortable": False, "default_visible": False},
    {"key": "imported_at", "label": "Imported at", "sortable": True, "default_visible": False},
    {"key": "cadence_kind_label", "label": "Cadence kind", "sortable": True, "sort_key": "cadence_kind", "default_visible": False},
    {"key": "expense_cadence", "label": "Cadence period", "sortable": False, "default_visible": False},
    {"key": "include_in_run_rate_label", "label": "In run rate", "sortable": True, "sort_key": "include_in_run_rate", "default_visible": False},
    {"key": "cadence_note", "label": "Cadence note", "sortable": False, "default_visible": False},
    {"key": "cadence_source", "label": "Cadence source", "sortable": False, "default_visible": False},
]


def list_result_columns() -> list[dict[str, Any]]:
    """Column metadata for Edit Transactions (master list from transactions table + computed labels)."""
    return [dict(c) for c in RESULT_COLUMNS]

_RUN_RATE_FILTER_VALUES = frozenset({"yes", "no"})
_LABEL_STATUS_FILTER_VALUES = frozenset({"needs_attention", "needs_review", "pending", "confirmed"})

_EFFECTIVE_KIND_EXPR = """
CASE
  WHEN t.cadence_kind IS NOT NULL AND TRIM(t.cadence_kind) != '' AND LOWER(t.cadence_kind) != 'unknown'
    THEN LOWER(t.cadence_kind)
  WHEN cr.cadence_kind IS NOT NULL AND TRIM(cr.cadence_kind) != ''
    THEN LOWER(cr.cadence_kind)
  WHEN t.cadence_kind IS NOT NULL AND TRIM(t.cadence_kind) != ''
    THEN LOWER(t.cadence_kind)
  ELSE 'unknown'
END
"""

_EFFECTIVE_PERIOD_COUNT_EXPR = """
CASE
  WHEN t.cadence_kind IS NOT NULL AND TRIM(t.cadence_kind) != '' AND LOWER(t.cadence_kind) != 'unknown'
    THEN t.period_count
  WHEN cr.cadence_kind IS NOT NULL AND TRIM(cr.cadence_kind) != ''
    THEN cr.period_count
  ELSE t.period_count
END
"""

_EFFECTIVE_PERIOD_UNIT_EXPR = """
CASE
  WHEN t.cadence_kind IS NOT NULL AND TRIM(t.cadence_kind) != '' AND LOWER(t.cadence_kind) != 'unknown'
    THEN LOWER(t.period_unit)
  WHEN cr.cadence_kind IS NOT NULL AND TRIM(cr.cadence_kind) != ''
    THEN LOWER(cr.period_unit)
  ELSE LOWER(t.period_unit)
END
"""

_EFFECTIVE_INCLUDE_EXPLICIT_EXPR = """
CASE
  WHEN t.cadence_kind IS NOT NULL AND TRIM(t.cadence_kind) != '' AND LOWER(t.cadence_kind) != 'unknown'
    THEN t.include_in_run_rate
  WHEN cr.cadence_kind IS NOT NULL AND TRIM(cr.cadence_kind) != ''
    THEN cr.include_in_run_rate
  ELSE t.include_in_run_rate
END
"""

_EFFECTIVE_RUN_RATE_EXPR = f"""
CASE
  WHEN ({_EFFECTIVE_INCLUDE_EXPLICIT_EXPR}) IS NOT NULL
    THEN ({_EFFECTIVE_INCLUDE_EXPLICIT_EXPR}) != 0
  WHEN ({_EFFECTIVE_KIND_EXPR}) = '{CADENCE_KIND_RECURRING}' THEN 1
  WHEN ({_EFFECTIVE_KIND_EXPR}) = '{CADENCE_KIND_LUMP}' THEN 0
  WHEN ({_EFFECTIVE_KIND_EXPR}) = '{CADENCE_KIND_ONE_TIME}' THEN 0
  WHEN ({_EFFECTIVE_KIND_EXPR}) = '{CADENCE_KIND_EXCLUDE}' THEN 0
  ELSE 1
END
"""

_SEARCH_FROM = """
FROM transactions t
LEFT JOIN cadence_rules cr
  ON cr.merchant_key = t.merchant_key AND cr.enabled = 1
"""


def _order_clause(sort_by: str, sort_dir: str) -> str:
    col = SORT_COLUMNS.get((sort_by or "").strip().lower(), "t.date")
    direction = "ASC" if str(sort_dir or "").lower() == "asc" else "DESC"
    if col == "t.date":
        return f"t.date {direction}, t.id {direction}"
    return f"{col} {direction}, t.date DESC, t.id DESC"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    kind = data.get("effective_cadence_kind") or CADENCE_KIND_UNKNOWN
    count = data.get("effective_period_count")
    unit = data.get("effective_period_unit")
    explicit_include = data.get("effective_include_in_run_rate")
    data["expense_cadence"] = expense_cadence_period_label(kind, count, unit)
    data["cadence_kind_label"] = cadence_kind_label(kind)
    data["include_in_run_rate_label"] = include_in_run_rate_label(kind, explicit_include)
    data["effective_in_run_rate"] = data["include_in_run_rate_label"] == "Yes"
    return data


def _parse_cadence_period_filter(value: str) -> tuple[int, str] | None:
    text = (value or "").strip().lower()
    if not text or text == "unset":
        return None
    if ":" not in text:
        raise ValueError("cadence_period must be like 12:months or unset")
    count_raw, unit = text.split(":", 1)
    count = int(count_raw)
    unit = unit.strip().lower()
    if count < 1:
        raise ValueError("cadence_period count must be >= 1")
    if unit not in ("months", "weeks", "days"):
        raise ValueError("cadence_period unit must be months, weeks, or days")
    return count, unit


def _search_where(
    *,
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    flow_type: str = "",
    classification: str = "",
    label_status: str = "",
    cadence_kind: str = "",
    cadence_period: str = "",
    include_in_run_rate: str = "",
) -> tuple[str, list[Any]]:
    clauses = ["1=1"]
    params: list[Any] = []

    text = (q or "").strip()
    if text:
        like = f"%{text}%"
        clauses.append(
            """(
                t.merchant_key LIKE ? COLLATE NOCASE
                OR COALESCE(t.simple_description, '') LIKE ? COLLATE NOCASE
                OR COALESCE(t.user_description, '') LIKE ? COLLATE NOCASE
                OR COALESCE(t.original_description, '') LIKE ? COLLATE NOCASE
            )"""
        )
        params.extend([like, like, like, like])

    month = (month or "").strip()
    if month:
        clauses.append("t.budget_month = ?")
        params.append(month)

    category = (category or "").strip()
    if category:
        clauses.append("t.ai_category = ?")
        params.append(category)

    sub_category = (sub_category or "").strip()
    if sub_category:
        clauses.append("t.ai_sub_category = ?")
        params.append(sub_category)

    expense_type = (expense_type or "").strip()
    if expense_type:
        clauses.append("t.expense_type = ?")
        params.append(expense_type)

    flow_type = (flow_type or "").strip()
    if flow_type:
        if flow_type not in FLOW_TYPE_OPTIONS:
            raise ValueError(f"flow_type must be one of: {', '.join(FLOW_TYPE_OPTIONS)}")
        clauses.append("t.flow_type = ?")
        params.append(flow_type)

    classification = (classification or "").strip()
    if classification:
        clauses.append("t.classification = ?")
        params.append(classification)

    label_status_filter = (label_status or "").strip().lower()
    if label_status_filter:
        if label_status_filter not in _LABEL_STATUS_FILTER_VALUES:
            allowed = ", ".join(sorted(_LABEL_STATUS_FILTER_VALUES))
            raise ValueError(f"label_status must be one of: {allowed}")
        if label_status_filter == "needs_attention":
            clauses.append("t.label_status IN ('needs_review', 'pending')")
        else:
            clauses.append("t.label_status = ?")
            params.append(label_status_filter)

    cadence_kind = (cadence_kind or "").strip().lower()
    if cadence_kind:
        if cadence_kind not in CADENCE_KINDS:
            raise ValueError(f"cadence_kind must be one of: {', '.join(sorted(CADENCE_KINDS))}")
        clauses.append(f"({_EFFECTIVE_KIND_EXPR}) = ?")
        params.append(cadence_kind)

    cadence_period = (cadence_period or "").strip().lower()
    if cadence_period:
        if cadence_period == "unset":
            clauses.append(f"({_EFFECTIVE_KIND_EXPR}) = 'unknown'")
        else:
            parsed = _parse_cadence_period_filter(cadence_period)
            if parsed:
                count, unit = parsed
                clauses.append(
                    f"({_EFFECTIVE_PERIOD_COUNT_EXPR}) = ? AND ({_EFFECTIVE_PERIOD_UNIT_EXPR}) = ?"
                )
                params.extend([count, unit])

    run_rate_filter = (include_in_run_rate or "").strip().lower()
    if run_rate_filter:
        if run_rate_filter not in _RUN_RATE_FILTER_VALUES:
            raise ValueError("include_in_run_rate must be yes or no")
        want = 1 if run_rate_filter == "yes" else 0
        clauses.append(f"({_EFFECTIVE_RUN_RATE_EXPR}) = ?")
        params.append(want)

    return " AND ".join(clauses), params


def search_transactions(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    flow_type: str = "",
    classification: str = "",
    label_status: str = "",
    cadence_kind: str = "",
    cadence_period: str = "",
    include_in_run_rate: str = "",
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    where, params = _search_where(
        q=q,
        month=month,
        category=category,
        sub_category=sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
        label_status=label_status,
        cadence_kind=cadence_kind,
        cadence_period=cadence_period,
        include_in_run_rate=include_in_run_rate,
    )
    total = conn.execute(
        f"SELECT COUNT(*) AS c {_SEARCH_FROM} WHERE {where}",
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT t.transaction_id,
               t.source_file,
               t.date,
               t.budget_month,
               t.amount,
               t.source_category,
               t.merchant_key,
               t.account_name,
               t.simple_description,
               t.user_description,
               t.original_description,
               COALESCE(NULLIF(t.simple_description, ''), NULLIF(t.user_description, ''),
                        NULLIF(t.original_description, '')) AS description,
               t.flow_type,
               t.ai_category,
               t.ai_sub_category,
               t.expense_type,
               t.classification,
               t.confidence,
               t.label_status,
               t.rationale,
               t.imported_at,
               t.cadence_kind,
               t.period_count,
               t.period_unit,
               t.include_in_run_rate,
               t.cadence_source,
               t.cadence_note,
               ({_EFFECTIVE_KIND_EXPR}) AS effective_cadence_kind,
               ({_EFFECTIVE_PERIOD_COUNT_EXPR}) AS effective_period_count,
               ({_EFFECTIVE_PERIOD_UNIT_EXPR}) AS effective_period_unit,
               ({_EFFECTIVE_INCLUDE_EXPLICIT_EXPR}) AS effective_include_in_run_rate,
               ({_EFFECTIVE_RUN_RATE_EXPR}) AS effective_in_run_rate
        {_SEARCH_FROM}
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
        "group_by": "",
    }


def search_merchant_groups(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    flow_type: str = "",
    classification: str = "",
    label_status: str = "",
    cadence_kind: str = "",
    cadence_period: str = "",
    include_in_run_rate: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Group matching transactions by merchant_key for merchant-centric review."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    where, params = _search_where(
        q=q,
        month=month,
        category=category,
        sub_category=sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
        label_status=label_status,
        cadence_kind=cadence_kind,
        cadence_period=cadence_period,
        include_in_run_rate=include_in_run_rate,
    )
    total = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM (
            SELECT t.merchant_key
            {_SEARCH_FROM}
            WHERE {where}
            GROUP BY t.merchant_key
        )
        """,
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT t.merchant_key,
               COUNT(*) AS tx_count,
               ROUND(SUM(CASE WHEN t.flow_type = 'Expense' AND t.amount < 0
                              THEN -t.amount ELSE 0 END), 2) AS expense_spend,
               MAX(t.ai_category) AS ai_category,
               MAX(t.ai_sub_category) AS ai_sub_category,
               MAX(t.expense_type) AS expense_type,
               MAX(t.classification) AS classification,
               MAX(t.flow_type) AS flow_type,
               MAX(t.label_status) AS label_status,
               MIN(t.transaction_id) AS sample_transaction_id,
               MAX(COALESCE(NULLIF(t.simple_description, ''),
                            NULLIF(t.user_description, ''),
                            NULLIF(t.original_description, ''), '')) AS sample_description
        {_SEARCH_FROM}
        WHERE {where}
        GROUP BY t.merchant_key
        ORDER BY expense_spend DESC, tx_count DESC, t.merchant_key ASC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    merchants = []
    for r in rows:
        merchants.append(
            {
                "merchant_key": r["merchant_key"],
                "tx_count": int(r["tx_count"] or 0),
                "expense_spend": float(r["expense_spend"] or 0),
                "ai_category": r["ai_category"] or "",
                "ai_sub_category": r["ai_sub_category"] or "",
                "expense_type": r["expense_type"] or "",
                "classification": r["classification"] or "",
                "flow_type": r["flow_type"] or "",
                "label_status": r["label_status"] or "",
                "sample_transaction_id": r["sample_transaction_id"] or "",
                "sample_description": r["sample_description"] or "",
            }
        )

    return {
        "merchants": merchants,
        "transactions": [],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "group_by": "merchant",
    }


def get_transaction(conn: sqlite3.Connection, transaction_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT transaction_id,
               date,
               budget_month,
               amount,
               merchant_key,
               simple_description,
               user_description,
               original_description,
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
                   simple_description,
                   user_description,
                   original_description,
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
               simple_description,
               user_description,
               original_description,
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


def _normalize_cadence_payload(cadence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not cadence:
        return None
    kind = str(cadence.get("cadence_kind") or "").strip().lower()
    if not kind or kind == CADENCE_KIND_UNKNOWN:
        return None

    if kind not in CADENCE_KINDS:
        raise ValueError(f"cadence_kind must be one of: {', '.join(sorted(CADENCE_KINDS))}")

    period_count = cadence.get("period_count")
    if period_count is not None:
        period_count = int(period_count)
        if period_count < 1:
            raise ValueError("period_count must be >= 1")

    period_unit = cadence.get("period_unit")
    if period_unit is not None:
        period_unit = str(period_unit).strip().lower() or None
        if period_unit and period_unit not in ("months", "weeks", "days"):
            raise ValueError("period_unit must be months, weeks, or days")

    include_raw = cadence.get("include_in_run_rate")
    include_val: int | None
    if include_raw is None or include_raw == "":
        include_val = None
    else:
        include_val = 1 if bool(include_raw) else 0

    return {
        "cadence_kind": kind,
        "period_count": period_count,
        "period_unit": period_unit,
        "include_in_run_rate": include_val,
        "cadence_note": str(cadence.get("cadence_note") or "").strip(),
    }


def bulk_update_labels(
    conn: sqlite3.Connection,
    *,
    transaction_ids: list[str],
    ai_category: str,
    ai_sub_category: str = "",
    flow_type: str | None = None,
    expense_type: str | None = None,
    classification: str | None = None,
    new_merchant_key: str | None = None,
    update_merchant_label: bool = False,
    merchant_key: str | None = None,
    cadence: dict[str, Any] | None = None,
    cadence_scope: str = "transaction",
) -> dict[str, Any]:
    category = (ai_category or "").strip()
    if not category:
        raise ValueError("ai_category is required")

    ids = [str(t).strip() for t in transaction_ids if str(t).strip()]
    if not ids:
        raise ValueError("At least one transaction_id is required")

    before_row = conn.execute(
        """
        SELECT ai_category, ai_sub_category, expense_type, classification, flow_type
        FROM transactions WHERE transaction_id = ?
        """,
        (ids[0],),
    ).fetchone()
    before_labels = (
        {
            "ai_category": str(before_row["ai_category"] or ""),
            "ai_sub_category": str(before_row["ai_sub_category"] or ""),
            "expense_type": str(before_row["expense_type"] or ""),
            "classification": str(before_row["classification"] or ""),
            "flow_type": str(before_row["flow_type"] or ""),
        }
        if before_row
        else {}
    )

    sub = (ai_sub_category or "").strip()
    placeholders = ",".join("?" * len(ids))

    label_merchant = (new_merchant_key or merchant_key or "").strip()[:120]
    if not label_merchant:
        raise ValueError("merchant label is required")

    if update_merchant_label:
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
                label_merchant,
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

    set_parts.append("merchant_key = ?")
    values.append(label_merchant)

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

    cadence_norm = _normalize_cadence_payload(cadence)
    cadence_scope = (cadence_scope or "transaction").strip().lower()
    if cadence_scope not in CADENCE_SCOPES:
        raise ValueError(f"cadence_scope must be one of: {', '.join(sorted(CADENCE_SCOPES))}")

    if cadence_norm:
        set_parts.extend(
            [
                "cadence_kind = ?",
                "period_count = ?",
                "period_unit = ?",
                "include_in_run_rate = ?",
                "cadence_source = 'user'",
                "cadence_note = ?",
            ]
        )
        values.extend(
            [
                cadence_norm["cadence_kind"],
                cadence_norm["period_count"],
                cadence_norm["period_unit"],
                cadence_norm["include_in_run_rate"],
                cadence_norm["cadence_note"],
            ]
        )

    values.extend(ids)
    cur = conn.execute(
        f"""
        UPDATE transactions
        SET {", ".join(set_parts)}
        WHERE transaction_id IN ({placeholders})
        """,
        values,
    )

    cadence_rule_saved = False
    if cadence_norm and cadence_scope == "merchant":
        mk = label_merchant
        if not mk:
            raise ValueError("merchant label is required when cadence_scope is merchant")
        include_bool = (
            None
            if cadence_norm["include_in_run_rate"] is None
            else bool(cadence_norm["include_in_run_rate"])
        )
        upsert_cadence_rule(
            conn,
            merchant_key=mk,
            cadence_kind=cadence_norm["cadence_kind"],
            period_count=cadence_norm["period_count"],
            period_unit=cadence_norm["period_unit"],
            include_in_run_rate=include_bool,
            notes=cadence_norm["cadence_note"],
            source="user",
        )
        cadence_rule_saved = True

    conn.commit()
    after_labels = {
        "ai_category": category,
        "ai_sub_category": sub,
        "expense_type": expense_type or before_labels.get("expense_type") or "Variable",
        "classification": classification or before_labels.get("classification") or "Personal",
        "flow_type": flow_type or before_labels.get("flow_type") or "Expense",
    }
    if before_labels and any(
        str(before_labels.get(k) or "").strip().lower()
        != str(after_labels.get(k) or "").strip().lower()
        for k in ("ai_category", "ai_sub_category", "expense_type", "classification")
    ):
        from webapp.services.decision_events import log_decision_event

        log_decision_event(
            conn,
            source="edit_transactions",
            entity_type="merchant",
            entity_key=label_merchant,
            action="edited",
            ai_proposal=before_labels,
            user_outcome=after_labels,
            context={"transaction_ids": ids, "rows_updated": cur.rowcount},
        )
        conn.commit()
    result: dict[str, Any] = {
        "rows_updated": cur.rowcount,
        "transaction_ids": ids,
    }
    if label_merchant:
        result["merchant_key"] = label_merchant
    if cadence_norm:
        result["cadence_applied"] = True
        result["cadence_scope"] = cadence_scope
        result["cadence_rule_saved"] = cadence_rule_saved
    return result
