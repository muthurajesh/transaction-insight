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
