from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from transaction_insight.core import (
    CADENCE_MONTHLY,
    CADENCE_ONETIME,
    CADENCE_UNPLANNED,
    CADENCE_UNKNOWN,
    CADENCE_YEARLY,
)

CADENCE_KIND_RECURRING = "recurring"
CADENCE_KIND_LUMP = "lump"
CADENCE_KIND_ONE_TIME = "one_time"
CADENCE_KIND_EXCLUDE = "exclude"
CADENCE_KIND_UNKNOWN = "unknown"

CADENCE_KINDS = frozenset(
    {
        CADENCE_KIND_RECURRING,
        CADENCE_KIND_LUMP,
        CADENCE_KIND_ONE_TIME,
        CADENCE_KIND_EXCLUDE,
        CADENCE_KIND_UNKNOWN,
    }
)

PERIOD_UNITS = frozenset({"months", "weeks", "days"})

EXPENSE_VIEWS = frozenset({"cash", "core", "normalized"})

_DEFAULT_INCLUDE_IN_RUN_RATE: dict[str, bool] = {
    CADENCE_KIND_RECURRING: True,
    CADENCE_KIND_LUMP: False,
    CADENCE_KIND_ONE_TIME: False,
    CADENCE_KIND_EXCLUDE: False,
    CADENCE_KIND_UNKNOWN: True,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_kind(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "monthly": CADENCE_KIND_RECURRING,
        "recurring": CADENCE_KIND_RECURRING,
        "yearly": CADENCE_KIND_LUMP,
        "annual": CADENCE_KIND_LUMP,
        "lump": CADENCE_KIND_LUMP,
        "one_time": CADENCE_KIND_ONE_TIME,
        "onetime": CADENCE_KIND_ONE_TIME,
        "ignore": CADENCE_KIND_EXCLUDE,
        "exclude": CADENCE_KIND_EXCLUDE,
        "unknown": CADENCE_KIND_UNKNOWN,
    }
    return aliases.get(text, text if text in CADENCE_KINDS else CADENCE_KIND_UNKNOWN)


def _parse_run_rate_flag(value: Any) -> bool | None:
    text = str(value or "").strip().upper()
    if text in ("Y", "YES", "TRUE", "1"):
        return True
    if text in ("N", "NO", "FALSE", "0"):
        return False
    return None


def _normalize_pipeline_cadence_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return CADENCE_UNKNOWN
    lower = text.lower()
    if lower in ("monthly", "month"):
        return CADENCE_MONTHLY
    if lower in ("yearly", "annual", "year"):
        return CADENCE_YEARLY
    if lower in ("one-time", "one time", "onetime"):
        return CADENCE_ONETIME
    if lower in ("unplanned", "unexpected"):
        return CADENCE_UNPLANNED
    return text


def parse_flexible_cadence_text(text: str) -> tuple[str, int | None, str | None]:
    """
    Parse free-form cadence hints (Excel notes, chat rules).
    Returns (kind, period_count, period_unit).
    """
    raw = (text or "").strip()
    if not raw:
        return CADENCE_KIND_UNKNOWN, None, None
    lower = raw.lower()

    if any(w in lower for w in ("ignore", "exclude", "do not include")):
        return CADENCE_KIND_EXCLUDE, None, None
    if any(w in lower for w in ("one-time", "one time", "onetime")):
        return CADENCE_KIND_ONE_TIME, None, None

    week_match = re.search(r"every\s+(\d+)\s+week", lower)
    if week_match:
        return CADENCE_KIND_RECURRING, int(week_match.group(1)), "weeks"
    if "bi-weekly" in lower or "biweekly" in lower or "every 2 weeks" in lower:
        return CADENCE_KIND_RECURRING, 2, "weeks"
    if "weekly" in lower and "bi" not in lower:
        return CADENCE_KIND_RECURRING, 1, "weeks"

    month_match = re.search(r"every\s+(\d+)\s+month", lower)
    if month_match:
        count = int(month_match.group(1))
        if count == 1:
            return CADENCE_KIND_RECURRING, 1, "months"
        return CADENCE_KIND_LUMP, count, "months"
    if "semi-annual" in lower or "semi annual" in lower or "every 6 months" in lower:
        return CADENCE_KIND_LUMP, 6, "months"
    if "quarterly" in lower or "every 3 months" in lower:
        return CADENCE_KIND_LUMP, 3, "months"
    if any(w in lower for w in ("yearly", "annual", "every 12 months", "once a year")):
        return CADENCE_KIND_LUMP, 12, "months"
    if any(w in lower for w in ("monthly", "every month")):
        return CADENCE_KIND_RECURRING, 1, "months"

    return CADENCE_KIND_UNKNOWN, None, None


def cadence_fields_from_pipeline_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Map pipeline Excel columns to DB cadence fields."""
    pipeline_cadence = _normalize_pipeline_cadence_label(row.get("Expense Cadence"))
    run_rate = _parse_run_rate_flag(row.get("In Monthly Run-Rate?"))
    note = str(row.get("Cadence Note", "") or "").strip()
    source = str(row.get("Cadence Source", "") or "").strip() or "pipeline"

    if pipeline_cadence == CADENCE_MONTHLY:
        kind = CADENCE_KIND_RECURRING
        period_count, period_unit = 1, "months"
    elif pipeline_cadence == CADENCE_YEARLY:
        kind = CADENCE_KIND_LUMP
        period_count, period_unit = 12, "months"
    elif pipeline_cadence in (CADENCE_ONETIME, CADENCE_UNPLANNED):
        kind = CADENCE_KIND_ONE_TIME
        period_count, period_unit = None, None
    elif pipeline_cadence == CADENCE_UNKNOWN:
        flex_kind, flex_count, flex_unit = parse_flexible_cadence_text(note)
        if flex_kind != CADENCE_KIND_UNKNOWN:
            kind, period_count, period_unit = flex_kind, flex_count, flex_unit
        else:
            kind = CADENCE_KIND_UNKNOWN
            period_count, period_unit = 1, "months"
    else:
        flex_kind, flex_count, flex_unit = parse_flexible_cadence_text(pipeline_cadence)
        if flex_kind != CADENCE_KIND_UNKNOWN:
            kind, period_count, period_unit = flex_kind, flex_count, flex_unit
        else:
            kind = CADENCE_KIND_UNKNOWN
            period_count, period_unit = 1, "months"

    return {
        "cadence_kind": kind,
        "period_count": period_count,
        "period_unit": period_unit,
        "include_in_run_rate": run_rate,
        "cadence_source": source,
        "cadence_note": note,
    }


def cadence_fields_from_lookup_rule(
    *,
    merchant_key: str,
    cadence: str = "",
    in_run_rate: str = "",
    notes: str = "",
) -> dict[str, Any]:
    pipeline = cadence_fields_from_pipeline_row(
        {
            "Expense Cadence": cadence,
            "In Monthly Run-Rate?": in_run_rate,
            "Cadence Note": notes,
            "Cadence Source": "lookup",
        }
    )
    flex_kind, flex_count, flex_unit = parse_flexible_cadence_text(
        " ".join(x for x in (cadence, notes) if x)
    )
    if flex_kind != CADENCE_KIND_UNKNOWN:
        pipeline["cadence_kind"] = flex_kind
        pipeline["period_count"] = flex_count
        pipeline["period_unit"] = flex_unit
    pipeline["cadence_source"] = "lookup"
    pipeline["cadence_note"] = notes or pipeline.get("cadence_note") or ""
    return pipeline


def include_in_run_rate_value(
    kind: str,
    explicit: bool | None,
) -> bool:
    if explicit is not None:
        return explicit
    return _DEFAULT_INCLUDE_IN_RUN_RATE.get(kind, True)


def normalize_monthly_amount(
    amount: float,
    *,
    kind: str,
    period_count: int | None = None,
    period_unit: str | None = None,
) -> float:
    """Spread or scale a charge into an equivalent monthly amount."""
    abs_amt = abs(float(amount or 0))
    if abs_amt == 0:
        return 0.0

    k = _normalize_kind(kind)
    if k in (CADENCE_KIND_ONE_TIME, CADENCE_KIND_EXCLUDE):
        return 0.0

    count = period_count
    unit = (period_unit or "").strip().lower() or None

    if k == CADENCE_KIND_RECURRING:
        if count and unit == "weeks" and count > 0:
            return abs_amt * (52 / count) / 12
        if count and unit == "months" and count > 1:
            return abs_amt / count
        if count and unit == "days" and count > 0:
            return abs_amt * (365 / count) / 12
        return abs_amt

    if k == CADENCE_KIND_LUMP:
        if not count or not unit:
            count, unit = 12, "months"
        if unit == "months" and count > 0:
            return abs_amt / count
        if unit == "weeks" and count > 0:
            return abs_amt * (52 / count) / 12
        if unit == "days" and count > 0:
            return abs_amt * (365 / count) / 12
        return abs_amt

    if k == CADENCE_KIND_UNKNOWN:
        return abs_amt

    return abs_amt


def effective_amount(
    amount: float,
    *,
    view: str,
    kind: str,
    period_count: int | None = None,
    period_unit: str | None = None,
    include_in_run_rate: bool | None = None,
) -> float:
    """Compute spend for cash / core / normalized views."""
    v = (view or "cash").strip().lower()
    if v not in EXPENSE_VIEWS:
        raise ValueError(f"view must be one of: {', '.join(sorted(EXPENSE_VIEWS))}")

    k = _normalize_kind(kind)
    run_rate = include_in_run_rate_value(k, include_in_run_rate)

    if v == "cash":
        return round(abs(float(amount or 0)), 2)
    if v == "core":
        return round(abs(float(amount or 0)), 2) if run_rate else 0.0
    if v == "normalized":
        return round(
            normalize_monthly_amount(
                amount,
                kind=k,
                period_count=period_count,
                period_unit=period_unit,
            ),
            2,
        )
    return round(abs(float(amount or 0)), 2)


def _row_to_cadence_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    explicit = data.get("include_in_run_rate")
    if explicit is None or explicit == "":
        include_flag: bool | None = None
    else:
        include_flag = bool(int(explicit))
    return {
        "cadence_kind": data.get("cadence_kind") or CADENCE_KIND_UNKNOWN,
        "period_count": data.get("period_count"),
        "period_unit": data.get("period_unit"),
        "include_in_run_rate": include_flag,
        "cadence_source": data.get("cadence_source") or "",
        "cadence_note": data.get("cadence_note") or "",
    }


def resolve_effective_cadence(
    conn: sqlite3.Connection,
    *,
    transaction_id: str | None = None,
    merchant_key: str | None = None,
    tx_row: dict[str, Any] | sqlite3.Row | None = None,
) -> dict[str, Any]:
    """
    Layer order: transaction columns override merchant cadence_rules.
    """
    tx = tx_row
    if tx is None and transaction_id:
        tx = conn.execute(
            """
            SELECT transaction_id, merchant_key,
                   cadence_kind, period_count, period_unit,
                   include_in_run_rate, cadence_source, cadence_note
            FROM transactions
            WHERE transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()

    tx_cadence = _row_to_cadence_dict(tx)
    mk = (merchant_key or (dict(tx).get("merchant_key") if tx else "") or "").strip()

    rule = None
    if mk:
        rule = conn.execute(
            """
            SELECT cadence_kind, period_count, period_unit,
                   include_in_run_rate, source, notes
            FROM cadence_rules
            WHERE merchant_key = ? AND enabled = 1
            """,
            (mk,),
        ).fetchone()

    rule_cadence = None
    if rule:
        explicit = rule["include_in_run_rate"]
        include_flag = None if explicit is None else bool(int(explicit))
        rule_cadence = {
            "cadence_kind": rule["cadence_kind"],
            "period_count": rule["period_count"],
            "period_unit": rule["period_unit"],
            "include_in_run_rate": include_flag,
            "cadence_source": rule["source"] or "rule",
            "cadence_note": rule["notes"] or "",
        }

    if tx_cadence and tx_cadence.get("cadence_kind") not in (None, "", CADENCE_KIND_UNKNOWN):
        effective = {**tx_cadence, "layer": "transaction"}
    elif rule_cadence:
        effective = {**rule_cadence, "layer": "merchant_rule"}
    elif tx_cadence:
        effective = {**tx_cadence, "layer": "transaction"}
    else:
        effective = {
            "cadence_kind": CADENCE_KIND_UNKNOWN,
            "period_count": 1,
            "period_unit": "months",
            "include_in_run_rate": True,
            "cadence_source": "default",
            "cadence_note": "",
            "layer": "default",
        }
    return effective


def upsert_cadence_rule(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    cadence_kind: str,
    period_count: int | None = None,
    period_unit: str | None = None,
    include_in_run_rate: bool | None = None,
    notes: str = "",
    source: str = "user",
    enabled: bool = True,
    rule_id: str | None = None,
) -> dict[str, Any]:
    mk = (merchant_key or "").strip()
    if not mk:
        raise ValueError("merchant_key is required")
    kind = _normalize_kind(cadence_kind)
    if kind not in CADENCE_KINDS:
        raise ValueError(f"cadence_kind must be one of: {', '.join(sorted(CADENCE_KINDS))}")

    unit = (period_unit or "").strip().lower() or None
    if unit and unit not in PERIOD_UNITS:
        raise ValueError(f"period_unit must be one of: {', '.join(sorted(PERIOD_UNITS))}")

    if period_count is not None and int(period_count) < 1:
        raise ValueError("period_count must be >= 1")

    now = _utc_now()
    rid = rule_id or str(uuid.uuid4())
    include_val = None if include_in_run_rate is None else (1 if include_in_run_rate else 0)

    conn.execute(
        """
        INSERT INTO cadence_rules (
            rule_id, merchant_key, cadence_kind, period_count, period_unit,
            include_in_run_rate, notes, source, enabled, priority,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 100, ?, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            cadence_kind = excluded.cadence_kind,
            period_count = excluded.period_count,
            period_unit = excluded.period_unit,
            include_in_run_rate = excluded.include_in_run_rate,
            notes = excluded.notes,
            source = excluded.source,
            enabled = excluded.enabled,
            updated_at = excluded.updated_at
        """,
        (
            rid,
            mk,
            kind,
            period_count,
            unit,
            include_val,
            (notes or "").strip(),
            (source or "user").strip(),
            1 if enabled else 0,
            now,
            now,
        ),
    )
    return get_cadence_rule(conn, mk)


def get_cadence_rule(conn: sqlite3.Connection, merchant_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT rule_id, merchant_key, cadence_kind, period_count, period_unit,
               include_in_run_rate, notes, source, enabled, priority,
               created_at, updated_at
        FROM cadence_rules
        WHERE merchant_key = ?
        """,
        (merchant_key,),
    ).fetchone()
    return dict(row) if row else None


def list_cadence_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT rule_id, merchant_key, cadence_kind, period_count, period_unit,
               include_in_run_rate, notes, source, enabled, priority,
               created_at, updated_at
        FROM cadence_rules
        ORDER BY merchant_key COLLATE NOCASE
        """
    ).fetchall()
    return [dict(r) for r in rows]


def sync_cadence_rules_from_lookup_records(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
) -> int:
    """Import ExpenseCadenceRules sheet rows into cadence_rules."""
    synced = 0
    for rec in records:
        merchant = str(rec.get("Generated Description", "") or "").strip()
        if not merchant:
            continue
        fields = cadence_fields_from_lookup_rule(
            merchant_key=merchant,
            cadence=str(rec.get("Cadence", "") or ""),
            in_run_rate=str(rec.get("In Monthly Run-Rate?", "") or ""),
            notes=str(rec.get("Notes", "") or ""),
        )
        upsert_cadence_rule(
            conn,
            merchant_key=merchant,
            cadence_kind=fields["cadence_kind"],
            period_count=fields.get("period_count"),
            period_unit=fields.get("period_unit"),
            include_in_run_rate=fields.get("include_in_run_rate"),
            notes=fields.get("cadence_note") or "",
            source="lookup",
        )
        synced += 1
    return synced


EXPENSE_VIEW_LABELS = {
    "cash": "Cash",
    "core": "Core (run-rate)",
    "normalized": "Normalized monthly",
}

CADENCE_KIND_LABELS = {
    CADENCE_KIND_RECURRING: "Recurring",
    CADENCE_KIND_LUMP: "Lump sum",
    CADENCE_KIND_ONE_TIME: "One-time",
    CADENCE_KIND_EXCLUDE: "Exclude",
    CADENCE_KIND_UNKNOWN: "Unknown",
}

CADENCE_PERIOD_PRESETS: list[tuple[str, str]] = [
    ("unset", "Unset / unknown"),
    ("1:months", "Every 1 month"),
    ("12:months", "Every 12 months"),
    ("6:months", "Every 6 months"),
    ("2:weeks", "Every 2 weeks"),
]

RUN_RATE_FILTER_OPTIONS: list[tuple[str, str]] = [
    ("yes", "Included in core"),
    ("no", "Excluded from core"),
]


def cadence_kind_label(kind: Any) -> str:
    k = _normalize_kind(kind)
    return CADENCE_KIND_LABELS.get(k, str(kind or CADENCE_KIND_UNKNOWN).title())


def include_in_run_rate_label(
    kind: Any,
    explicit: bool | int | None = None,
) -> str:
    """Display label for whether a row counts in the core (run-rate) view."""
    flag: bool | None
    if explicit is None or explicit == "":
        flag = None
    else:
        flag = bool(explicit)
    return "Yes" if include_in_run_rate_value(_normalize_kind(kind), flag) else "No"


def expense_cadence_period_label(
    kind: Any,
    period_count: int | None = None,
    period_unit: str | None = None,
) -> str:
    """Human-readable expense cadence period (every N units)."""
    k = _normalize_kind(kind)
    if k in (CADENCE_KIND_UNKNOWN, CADENCE_KIND_ONE_TIME, CADENCE_KIND_EXCLUDE):
        return "—"
    if period_count is None or not period_unit:
        return "—"
    unit = str(period_unit).strip().lower()
    count = int(period_count)
    if unit == "months":
        noun = "month" if count == 1 else "months"
    elif unit == "weeks":
        noun = "week" if count == 1 else "weeks"
    elif unit == "days":
        noun = "day" if count == 1 else "days"
    else:
        noun = unit
    return f"Every {count} {noun}"


def validate_expense_view(view: str | None) -> str:
    v = (view or "cash").strip().lower()
    if v not in EXPENSE_VIEWS:
        raise ValueError(f"expense_view must be one of: {', '.join(sorted(EXPENSE_VIEWS))}")
    return v


def expense_view_label(view: str | None) -> str:
    v = validate_expense_view(view)
    return EXPENSE_VIEW_LABELS.get(v, v.title())


def _load_cadence_rules_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT merchant_key, cadence_kind, period_count, period_unit,
               include_in_run_rate, source, notes
        FROM cadence_rules
        WHERE enabled = 1
        """
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        mk = str(row["merchant_key"] or "").strip()
        if mk:
            out[mk] = dict(row)
    return out


def _resolve_cadence_for_expense_row(
    row: sqlite3.Row | dict[str, Any],
    rules_by_merchant: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    data = dict(row)
    tx_cadence = _row_to_cadence_dict(data)
    mk = str(data.get("merchant_key") or "").strip()
    rule = rules_by_merchant.get(mk)
    rule_cadence = None
    if rule:
        explicit = rule.get("include_in_run_rate")
        include_flag = None if explicit is None else bool(int(explicit))
        rule_cadence = {
            "cadence_kind": rule["cadence_kind"],
            "period_count": rule["period_count"],
            "period_unit": rule["period_unit"],
            "include_in_run_rate": include_flag,
            "cadence_source": rule.get("source") or "rule",
            "cadence_note": rule.get("notes") or "",
        }

    if tx_cadence and tx_cadence.get("cadence_kind") not in (None, "", CADENCE_KIND_UNKNOWN):
        return tx_cadence
    if rule_cadence:
        return rule_cadence
    if tx_cadence:
        return tx_cadence
    return {
        "cadence_kind": CADENCE_KIND_UNKNOWN,
        "period_count": 1,
        "period_unit": "months",
        "include_in_run_rate": True,
        "cadence_source": "default",
        "cadence_note": "",
    }


def _iter_expense_rows(
    conn: sqlite3.Connection,
    *,
    budget_month: str | None = None,
    budget_months: list[str] | None = None,
    category: str | None = None,
) -> list[sqlite3.Row]:
    clauses = ["flow_type = 'Expense'", "amount < 0"]
    params: list[Any] = []

    if budget_month:
        clauses.append("budget_month = ?")
        params.append(budget_month)
    elif budget_months:
        placeholders = ",".join("?" * len(budget_months))
        clauses.append(f"budget_month IN ({placeholders})")
        params.extend(budget_months)

    if category:
        clauses.append("ai_category = ?")
        params.append(category)

    sql = f"""
        SELECT transaction_id, amount, merchant_key, ai_category, budget_month,
               cadence_kind, period_count, period_unit, include_in_run_rate
        FROM transactions
        WHERE {' AND '.join(clauses)}
    """
    return conn.execute(sql, params).fetchall()


def sum_expenses_for_view(
    conn: sqlite3.Connection,
    *,
    view: str = "cash",
    budget_month: str | None = None,
    budget_months: list[str] | None = None,
    category: str | None = None,
) -> tuple[float, int]:
    """Sum effective expense amounts for a cadence view."""
    v = validate_expense_view(view)
    rules = _load_cadence_rules_map(conn)
    rows = _iter_expense_rows(
        conn,
        budget_month=budget_month,
        budget_months=budget_months,
        category=category,
    )
    total = 0.0
    for row in rows:
        cadence = _resolve_cadence_for_expense_row(row, rules)
        total += effective_amount(
            float(row["amount"] or 0),
            view=v,
            kind=cadence["cadence_kind"],
            period_count=cadence.get("period_count"),
            period_unit=cadence.get("period_unit"),
            include_in_run_rate=cadence.get("include_in_run_rate"),
        )
    return round(total, 2), len(rows)


def top_categories_for_view(
    conn: sqlite3.Connection,
    month: str,
    *,
    limit: int = 10,
    view: str = "cash",
) -> list[dict[str, Any]]:
    v = validate_expense_view(view)
    rules = _load_cadence_rules_map(conn)
    rows = _iter_expense_rows(conn, budget_month=month)
    by_cat: dict[str, dict[str, Any]] = {}

    for row in rows:
        cat = str(row["ai_category"] or "").strip()
        if not cat:
            continue
        cadence = _resolve_cadence_for_expense_row(row, rules)
        spend = effective_amount(
            float(row["amount"] or 0),
            view=v,
            kind=cadence["cadence_kind"],
            period_count=cadence.get("period_count"),
            period_unit=cadence.get("period_unit"),
            include_in_run_rate=cadence.get("include_in_run_rate"),
        )
        bucket = by_cat.setdefault(cat, {"category": cat, "transaction_count": 0, "spend": 0.0})
        bucket["transaction_count"] += 1
        bucket["spend"] += spend

    ranked = sorted(by_cat.values(), key=lambda x: x["spend"], reverse=True)[:limit]
    return [
        {
            "category": item["category"],
            "transaction_count": int(item["transaction_count"]),
            "spend": round(float(item["spend"]), 2),
        }
        for item in ranked
    ]


def flow_totals_expense_by_view(
    conn: sqlite3.Connection,
    *,
    view: str = "cash",
    full_months_only: bool = True,
) -> dict[str, Any]:
    from transaction_insight.analytics import available_months

    v = validate_expense_view(view)
    overview = available_months(conn)
    months_list = overview.get("months") or []
    if full_months_only:
        full_set = set(overview.get("full_months") or [])
        month_keys = [str(m["month"]) for m in months_list if str(m["month"]) in full_set]
    else:
        month_keys = [str(m["month"]) for m in months_list]

    if not month_keys:
        return {
            "flow_type": "Expense",
            "expense_view": v,
            "full_months_only": full_months_only,
            "months": [],
            "grand_total": 0.0,
        }

    rules = _load_cadence_rules_map(conn)
    rows = _iter_expense_rows(conn, budget_months=month_keys)
    by_month: dict[str, dict[str, Any]] = {
        m: {"month": m, "transaction_count": 0, "total": 0.0} for m in month_keys
    }

    for row in rows:
        month = str(row["budget_month"] or "")
        if month not in by_month:
            continue
        cadence = _resolve_cadence_for_expense_row(row, rules)
        amt = effective_amount(
            float(row["amount"] or 0),
            view=v,
            kind=cadence["cadence_kind"],
            period_count=cadence.get("period_count"),
            period_unit=cadence.get("period_unit"),
            include_in_run_rate=cadence.get("include_in_run_rate"),
        )
        by_month[month]["transaction_count"] += 1
        by_month[month]["total"] += amt

    months_out = []
    grand_total = 0.0
    for month in sorted(by_month.keys()):
        entry = by_month[month]
        total = round(float(entry["total"]), 2)
        months_out.append(
            {
                "month": month,
                "transaction_count": int(entry["transaction_count"]),
                "total": total,
            }
        )
        grand_total += total

    return {
        "flow_type": "Expense",
        "expense_view": v,
        "full_months_only": full_months_only,
        "months": months_out,
        "grand_total": round(grand_total, 2),
    }


def sync_cadence_rules_from_lookup_snapshot(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT payload_json FROM lookup_snapshots WHERE sheet_name = ?",
        ("ExpenseCadenceRules",),
    ).fetchone()
    if not row:
        return 0
    try:
        records = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return 0
    if not isinstance(records, list):
        return 0
    return sync_cadence_rules_from_lookup_records(conn, records)
