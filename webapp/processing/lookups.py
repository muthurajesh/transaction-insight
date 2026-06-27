from __future__ import annotations

from typing import Any

import pandas as pd

from webapp.processing.constants import MERCHANT_CATEGORIES_SHEET
from webapp.processing.parse import _is_semantic_sub_category, _row_merchant_key, is_business_row


def _norm_lookup_key(category: str, sub_category: str) -> tuple[str, str]:
    return str(category or "").strip().lower(), str(sub_category or "").strip().lower()


def build_merchant_category_map(
    lookups: dict[str, pd.DataFrame],
) -> dict[str, pd.Series]:
    """Merchant Key (lower) -> lookup row. Falls back to legacy Categories sheet."""
    result: dict[str, pd.Series] = {}
    sheet = lookups.get(MERCHANT_CATEGORIES_SHEET)
    if sheet is not None and not sheet.empty and "Merchant Key" in sheet.columns:
        for _, row in sheet.iterrows():
            mk = str(row.get("Merchant Key", "") or "").strip().lower()
            if mk:
                result[mk] = row
        return result

    categories = lookups.get("Categories")
    if categories is not None and not categories.empty:
        for _, row in categories.iterrows():
            mk = str(row.get("AI Sub-Category", "") or "").strip().lower()
            if mk:
                result[mk] = row
    return result


def apply_merchant_category_lookup(
    df: pd.DataFrame,
    lookups: dict[str, pd.DataFrame],
    *,
    spend_mask: pd.Series | None = None,
) -> int:
    """Apply per-merchant categorization from MerchantCategories (or legacy Categories)."""
    from webapp.llm.validation import generated_description_plausible

    by_merchant = build_merchant_category_map(lookups)
    if not by_merchant:
        return 0

    updated = 0
    from_merchant_sheet = (
        lookups.get(MERCHANT_CATEGORIES_SHEET) is not None
        and not lookups.get(MERCHANT_CATEGORIES_SHEET, pd.DataFrame()).empty
    )

    for idx, row in df.iterrows():
        mk = _row_merchant_key(row)
        if not generated_description_plausible(mk, row):
            continue
        match = by_merchant.get(mk.lower())
        if match is None:
            continue

        sub = str(match.get("AI Sub-Category", "") or "").strip()
        apply_sub = bool(sub)
        if not from_merchant_sheet and sub.lower() == mk:
            apply_sub = False

        for col, target in (
            ("AI Category", "AI Category"),
            ("Type", "Type"),
            ("Sub-Type", "Sub-Type"),
            ("Flow Type", "Flow Type"),
            ("Classification", "Classification"),
        ):
            val = match.get(col, "")
            if pd.notna(val) and str(val).strip() not in ("", "nan"):
                df.at[idx, target] = str(val).strip()

        if apply_sub and sub.lower() != mk:
            df.at[idx, "AI Sub-Category"] = sub

        if spend_mask is None or bool(spend_mask.loc[idx]):
            tier = match.get("Budget Tier", "")
            if pd.notna(tier) and str(tier).strip() not in ("", "nan", "Review"):
                df.at[idx, "Budget Tier"] = str(tier).strip()
        updated += 1

    return updated


def apply_lookup_rules(
    df: pd.DataFrame,
    lookups: dict[str, pd.DataFrame],
    *,
    spend_mask: pd.Series | None = None,
) -> int:
    """
    Apply CategoryRules (by source Category) then merchant Categories lookup.
    Budget Tier from lookup is applied only on spend rows (when spend_mask is set).
    Returns approximate count of rows touched by lookup.
    """
    updated = 0
    rules = lookups.get("CategoryRules")
    if rules is not None and not rules.empty and "Source Category" in rules.columns:
        rules = rules.copy()
        rules["Source Category"] = rules["Source Category"].fillna("").astype(str).str.strip()
        rules_by_source = {
            row["Source Category"]: row
            for _, row in rules.iterrows()
            if row["Source Category"]
        }
        for idx, row in df.iterrows():
            src = str(row.get("Category", "") or "").strip()
            rule = rules_by_source.get(src)
            if rule is None:
                continue
            for col, target in (
                ("AI Category", "AI Category"),
                ("Type", "Type"),
                ("Sub-Type", "Sub-Type"),
            ):
                val = rule.get(col, "")
                if pd.notna(val) and str(val).strip() not in ("", "nan"):
                    df.at[idx, target] = str(val).strip()
            if spend_mask is None or bool(spend_mask.loc[idx]):
                tier = rule.get("Budget Tier", "")
                if pd.notna(tier) and str(tier).strip() not in ("", "nan", "Review"):
                    df.at[idx, "Budget Tier"] = str(tier).strip()
            updated += 1

    categories = lookups.get("Categories")
    if categories is None or categories.empty:
        return updated

    categories = categories.copy()
    categories["AI Category"] = categories["AI Category"].fillna("").astype(str)
    categories["AI Sub-Category"] = categories["AI Sub-Category"].fillna("").astype(str)

    lookup_by_pair: dict[tuple[str, str], pd.Series] = {}
    for _, row in categories.iterrows():
        key = _norm_lookup_key(row["AI Category"], row["AI Sub-Category"])
        if not key[1]:
            continue
        lookup_by_pair[key] = row

    for idx, row in df.iterrows():
        if not _is_semantic_sub_category(row):
            continue
        key = _norm_lookup_key(row.get("AI Category", ""), row.get("AI Sub-Category", ""))
        match = lookup_by_pair.get(key)
        if match is None:
            continue
        for col in ("AI Category", "AI Sub-Category", "Type", "Sub-Type"):
            val = match.get(col, "")
            if pd.notna(val) and str(val).strip() not in ("", "nan"):
                df.at[idx, col] = str(val).strip()
        if spend_mask is None or bool(spend_mask.loc[idx]):
            tier = match.get("Budget Tier", "")
            if pd.notna(tier) and str(tier).strip() not in ("", "nan", "Review"):
                df.at[idx, "Budget Tier"] = str(tier).strip()
        updated += 1

    business_rules = lookups.get("BusinessCategoryRules")
    if business_rules is not None and not business_rules.empty:
        if "Generated Description" in business_rules.columns:
            business_rules = business_rules.copy()
            by_desc: dict[str, pd.Series] = {}
            for _, brow in business_rules.iterrows():
                key = str(brow.get("Generated Description", "") or "").strip().lower()
                if key:
                    by_desc[key] = brow
            for idx, row in df.iterrows():
                if not is_business_row(row):
                    continue
                key = str(row.get("Generated Description", "") or "").strip().lower()
                match = by_desc.get(key)
                if match is None:
                    continue
                for col, target in (
                    ("AI Category", "AI Category"),
                    ("AI Sub-Category", "AI Sub-Category"),
                    ("Type", "Type"),
                    ("Sub-Type", "Sub-Type"),
                ):
                    val = match.get(col, "")
                    if pd.notna(val) and str(val).strip() not in ("", "nan"):
                        df.at[idx, target] = str(val).strip()
                if spend_mask is None or bool(spend_mask.loc[idx]):
                    tier = match.get("Budget Tier", "")
                    if pd.notna(tier) and str(tier).strip() not in ("", "nan", "Review"):
                        df.at[idx, "Budget Tier"] = str(tier).strip()
                updated += 1

    return updated


def _build_category_rules_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """Default mapping by source Category (one row per source category)."""
    spend = df[(df.get("Include in Spend?", "N") == "Y")].copy()
    if spend.empty or "Category" not in spend.columns:
        return pd.DataFrame(
            columns=["Source Category", "AI Category", "Budget Tier", "Type", "Sub-Type", "Notes"]
        )

    grouped = (
        spend.groupby("Category", dropna=False)
        .agg(
            **{
                "AI Category": ("AI Category", lambda s: s.mode().iat[0] if len(s) else ""),
                "Budget Tier": ("Budget Tier", lambda s: s.mode().iat[0] if len(s) else ""),
                "Type": ("Type", lambda s: s.mode().iat[0] if len(s) else ""),
            }
        )
        .reset_index()
        .rename(columns={"Category": "Source Category"})
    )
    grouped["Sub-Type"] = ""
    grouped["Notes"] = ""
    return grouped.sort_values("Source Category").reset_index(drop=True)
