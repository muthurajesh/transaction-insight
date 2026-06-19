from __future__ import annotations

from typing import Any

import pandas as pd

from webapp.processing.constants import (
    CADENCE_DEFAULT_RUNRATE,
    CADENCE_MIN_HISTORY_MONTHS,
    CADENCE_MONTHLY,
    CADENCE_ONETIME,
    CADENCE_SOURCE_DEFAULT,
    CADENCE_SOURCE_DETECTED,
    CADENCE_SOURCE_LOOKUP,
    CADENCE_SPIKE_RATIO,
    CADENCE_UNPLANNED,
    CADENCE_UNKNOWN,
    CADENCE_VALID,
    CADENCE_YEARLY,
    CADENCE_YEARLY_GAP_MAX,
    CADENCE_YEARLY_GAP_MIN,
    EXPENSE_CADENCE_RULE_COLUMNS,
)
from webapp.processing.flow import _spend_rows
from webapp.processing.parse import parse_amount


def empty_expense_cadence_rules_sheet() -> pd.DataFrame:
    return pd.DataFrame(columns=list(EXPENSE_CADENCE_RULE_COLUMNS))

def normalize_expense_cadence_rules_sheet(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_expense_cadence_rules_sheet()
    out = frame.copy()
    if "Generated Description" not in out.columns:
        return empty_expense_cadence_rules_sheet()
    for col in EXPENSE_CADENCE_RULE_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out[list(EXPENSE_CADENCE_RULE_COLUMNS)]

def merge_expense_cadence_rules(
    existing: pd.DataFrame | None,
    new_rules: pd.DataFrame,
) -> pd.DataFrame:
    """Preserve existing ExpenseCadenceRules; append new Generated Description keys only."""
    cols = list(EXPENSE_CADENCE_RULE_COLUMNS)
    if new_rules is None or new_rules.empty:
        return normalize_expense_cadence_rules_sheet(existing)

    new_rules = normalize_expense_cadence_rules_sheet(new_rules)
    if existing is None or existing.empty:
        return new_rules

    old = normalize_expense_cadence_rules_sheet(existing)
    old_keys = {
        str(r.get("Generated Description", "") or "").strip().lower()
        for _, r in old.iterrows()
        if str(r.get("Generated Description", "") or "").strip()
    }
    append_rows = []
    for _, row in new_rules.iterrows():
        key = str(row.get("Generated Description", "") or "").strip().lower()
        if key and key not in old_keys:
            append_rows.append(row)
    if not append_rows:
        return old
    return pd.concat([old, pd.DataFrame(append_rows)], ignore_index=True)

def _normalize_cadence_label(value: Any) -> str:
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
    if text in CADENCE_VALID:
        return text
    return CADENCE_UNKNOWN

def _normalize_runrate_flag(value: Any, cadence: str) -> str:
    text = str(value or "").strip().upper()
    if text in ("Y", "YES", "TRUE", "1"):
        return "Y"
    if text in ("N", "NO", "FALSE", "0"):
        return "N"
    return CADENCE_DEFAULT_RUNRATE.get(cadence, "Y")

def init_expense_cadence_columns(df: pd.DataFrame) -> None:
    df["Expense Cadence"] = CADENCE_UNKNOWN
    df["In Monthly Run-Rate?"] = ""
    df["Cadence Source"] = ""
    df["Cadence Note"] = ""

def apply_expense_cadence_lookup(
    df: pd.DataFrame,
    rules: pd.DataFrame | None,
    *,
    spend_mask: pd.Series,
) -> int:
    """Apply ExpenseCadenceRules by Generated Description (case-insensitive)."""
    rules = normalize_expense_cadence_rules_sheet(rules)
    if rules.empty or "Generated Description" not in df.columns:
        return 0

    by_desc: dict[str, pd.Series] = {}
    for _, row in rules.iterrows():
        key = str(row.get("Generated Description", "") or "").strip().lower()
        if key:
            by_desc[key] = row

    updated = 0
    for idx in df[spend_mask].index:
        key = str(df.at[idx, "Generated Description"] or "").strip().lower()
        match = by_desc.get(key)
        if match is None:
            continue
        cadence = _normalize_cadence_label(match.get("Cadence", ""))
        runrate = _normalize_runrate_flag(match.get("In Monthly Run-Rate?", ""), cadence)
        df.at[idx, "Expense Cadence"] = cadence
        df.at[idx, "In Monthly Run-Rate?"] = runrate
        df.at[idx, "Cadence Source"] = CADENCE_SOURCE_LOOKUP
        note = str(match.get("Notes", "") or "").strip()
        if note:
            df.at[idx, "Cadence Note"] = note
        updated += 1
    return updated

def build_analytics_ledger(current_df: pd.DataFrame) -> pd.DataFrame:
    """Build ledger from the current run for cadence pattern detection."""
    cur = current_df.copy()
    if "Amount_Numeric" not in cur.columns and "Amount" in cur.columns:
        cur["Amount_Numeric"] = cur["Amount"].apply(parse_amount)
    if "Budget Month" not in cur.columns and "Transaction Date" in cur.columns:
        cur["Budget Month"] = pd.to_datetime(
            cur["Transaction Date"], errors="coerce"
        ).dt.to_period("M").astype(str)
    return cur

def analyze_merchant_cadence_profiles(ledger: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """
    Per Generated Description, infer monthly vs yearly vs one-time vs unplanned
    from multi-month spend totals in the analytics ledger.
    """
    spend = _spend_rows(ledger)
    if spend.empty or "Generated Description" not in spend.columns:
        return {}

    monthly = (
        spend.groupby(["Generated Description", "Budget Month"], dropna=False)["Spend Amount"]
        .sum()
        .reset_index()
    )

    profiles: dict[str, dict[str, Any]] = {}
    for desc, grp in monthly.groupby("Generated Description", dropna=False):
        desc_key = str(desc).strip().lower()
        if not desc_key:
            continue
        by_month = grp.set_index("Budget Month")["Spend Amount"]
        if len(by_month) < CADENCE_MIN_HISTORY_MONTHS:
            continue

        typical = float(by_month.median())
        if typical <= 0:
            continue

        threshold = typical * CADENCE_SPIKE_RATIO
        high_months = [str(m) for m, total in by_month.items() if float(total) >= threshold]

        pattern = "monthly"
        note = f"Typical month ~${typical:,.2f}"
        if len(high_months) >= 2:
            try:
                periods = sorted(pd.Period(m, freq="M") for m in high_months)
                gaps = [(periods[i + 1] - periods[i]).n for i in range(len(periods) - 1)]
            except Exception:
                gaps = []
            if any(CADENCE_YEARLY_GAP_MIN <= g <= CADENCE_YEARLY_GAP_MAX for g in gaps):
                pattern = "yearly"
                note = (
                    f"{len(high_months)} spike month(s); ~annual pattern "
                    f"(typical month ~${typical:,.2f})"
                )
            else:
                pattern = "unplanned"
                note = (
                    f"{len(high_months)} spike month(s); no annual spacing "
                    f"(typical ~${typical:,.2f})"
                )
        elif len(high_months) == 1:
            pattern = "onetime"
            note = f"Single spike month in history (typical ~${typical:,.2f})"

        profiles[desc_key] = {
            "pattern": pattern,
            "typical_monthly": typical,
            "high_months": set(high_months),
            "note": note,
        }
    return profiles

def _cadence_from_pattern(pattern: str) -> str:
    if pattern == "yearly":
        return CADENCE_YEARLY
    if pattern == "onetime":
        return CADENCE_ONETIME
    if pattern == "unplanned":
        return CADENCE_UNPLANNED
    return CADENCE_MONTHLY

def apply_detected_expense_cadence(
    df: pd.DataFrame,
    profiles: dict[str, dict[str, Any]],
    *,
    spend_mask: pd.Series,
) -> int:
    """Tag spend rows from history-based detection (skips Lookup-tagged rows)."""
    if not profiles:
        return 0

    updated = 0
    for idx in df[spend_mask].index:
        if str(df.at[idx, "Cadence Source"] or "").strip() == CADENCE_SOURCE_LOOKUP:
            continue

        desc_key = str(df.at[idx, "Generated Description"] or "").strip().lower()
        profile = profiles.get(desc_key)
        if profile is None:
            continue

        month = str(df.at[idx, "Budget Month"] or "")
        amount = abs(float(df.at[idx, "Amount_Numeric"]))
        typical = float(profile["typical_monthly"])
        threshold = typical * CADENCE_SPIKE_RATIO

        if month in profile["high_months"] and amount >= threshold * 0.5:
            cadence = _cadence_from_pattern(str(profile["pattern"]))
        else:
            cadence = CADENCE_MONTHLY

        df.at[idx, "Expense Cadence"] = cadence
        df.at[idx, "In Monthly Run-Rate?"] = CADENCE_DEFAULT_RUNRATE.get(cadence, "Y")
        df.at[idx, "Cadence Source"] = CADENCE_SOURCE_DETECTED
        df.at[idx, "Cadence Note"] = str(profile.get("note", "") or "")
        updated += 1
    return updated

def finalize_expense_cadence_defaults(df: pd.DataFrame, *, spend_mask: pd.Series) -> None:
    """Unknown cadence defaults to included in monthly run-rate until lookup/detection sets otherwise."""
    for idx in df[spend_mask].index:
        if str(df.at[idx, "Cadence Source"] or "").strip():
            continue
        df.at[idx, "Expense Cadence"] = CADENCE_UNKNOWN
        df.at[idx, "In Monthly Run-Rate?"] = "Y"
        df.at[idx, "Cadence Source"] = CADENCE_SOURCE_DEFAULT

def build_cadence_review_df(df: pd.DataFrame) -> pd.DataFrame:
    """Rows auto-tagged as irregular (Detected, not Monthly) for user review."""
    if "Cadence Source" not in df.columns:
        return pd.DataFrame()
    mask = (
        (df["Cadence Source"].astype(str) == CADENCE_SOURCE_DETECTED)
        & (df["Expense Cadence"] != CADENCE_MONTHLY)
        & (df["Include in Spend?"] == "Y")
    )
    review = df.loc[mask].copy()
    if review.empty:
        return pd.DataFrame()

    cols = [
        c
        for c in (
            "Transaction ID",
            "Transaction Date",
            "Budget Month",
            "Generated Description",
            "Amount",
            "AI Category",
            "AI Sub-Category",
            "Expense Cadence",
            "In Monthly Run-Rate?",
            "Cadence Source",
            "Cadence Note",
        )
        if c in review.columns
    ]
    out = review[cols].copy()
    if "Transaction Date" in out.columns:
        out = out.sort_values(["Budget Month", "Generated Description", "Transaction Date"])
    return out.reset_index(drop=True)
