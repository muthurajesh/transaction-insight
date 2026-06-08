"""Web helpers; CSV loading delegates to transaction_insight.core."""

from __future__ import annotations

import hashlib

import pandas as pd

from transaction_insight.core import (
    heuristic_generated_description,
    load_csv,
    merchant_key,
    parse_amount,
    parse_transaction_dates,
    transaction_fingerprint,
)


def account_name_from_row(row: pd.Series) -> str:
    return str(row.get("Account Name", row.get("Account", "")) or "").strip()


def budget_month_label(dt: pd.Timestamp) -> str:
    return pd.Timestamp(dt).strftime("%Y-%m")


def flow_type_from_amount(amount: float) -> str:
    return "Income" if amount > 0 else "Expense"


def transaction_id_from_row(
    row: pd.Series,
    *,
    parsed_date: pd.Timestamp | None = None,
) -> str:
    if parsed_date is not None and pd.notna(parsed_date):
        date_part = pd.Timestamp(parsed_date).strftime("%Y-%m-%d")
    else:
        date_part = str(row.get("Date", "") or "").strip()
    amt = parse_amount(row.get("Amount_Numeric", row.get("Amount", 0)))
    account = account_name_from_row(row)
    original = str(row.get("Original Description", "") or "").strip()[:200]
    payload = f"{date_part}|{amt:.2f}|{account}|{original}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


__all__ = [
    "account_name_from_row",
    "budget_month_label",
    "flow_type_from_amount",
    "heuristic_generated_description",
    "load_csv",
    "merchant_key",
    "parse_amount",
    "parse_transaction_dates",
    "transaction_fingerprint",
    "transaction_id_from_row",
]
