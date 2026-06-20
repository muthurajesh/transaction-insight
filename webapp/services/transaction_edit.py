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
SORT_COLUMNS = {
    "date": "t.date",
    "amount": "t.amount",
    "merchant": "t.merchant_key",
    "ai_category": "t.ai_category",
    "classification": "t.classification",
    "cadence_kind": "effective_cadence_kind",
    "include_in_run_rate": "effective_in_run_rate",
}

_RUN_RATE_FILTER_VALUES = frozenset({"yes", "no"})

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


def search_transactions(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    month: str = "",
    category: str = "",
    sub_category: str = "",
    expense_type: str = "",
    classification: str = "",
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

    classification = (classification or "").strip()
    if classification:
        clauses.append("t.classification = ?")
        params.append(classification)

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

    where = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) AS c {_SEARCH_FROM} WHERE {where}",
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT t.transaction_id,
               t.date,
               t.budget_month,
               t.amount,
               t.merchant_key,
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
               t.label_status,
               t.cadence_kind,
               t.period_count,
               t.period_unit,
               t.include_in_run_rate,
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
