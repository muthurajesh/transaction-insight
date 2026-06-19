from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from openai import OpenAI

from webapp.excel.formatting import open_excel_workbook
from webapp.llm.client import BATCH_SIZE
from webapp.llm.descriptions import merge_description_lookup
from webapp.processing.business_rules import (
    enrich_business_lookup_rules,
    merge_business_category_rules,
)
from webapp.processing.cadence import (
    empty_expense_cadence_rules_sheet,
    normalize_expense_cadence_rules_sheet,
)
from webapp.processing.constants import (
    CUSTOM_RULES_SHEET,
    EXPENSE_CADENCE_RULES_SHEET,
    LOOKUP_SHEETS,
    LOOKUP_FILENAME,
    MERCHANT_CATEGORIES_SHEET,
    MERCHANT_CATEGORY_COLUMNS,
    PROJECT_ROOT,
    SCRIPTS_DIR,
)
from webapp.processing.custom_rules import normalize_custom_rules_sheet
from webapp.processing.parse import _is_semantic_sub_category, is_business_row, merchant_key


def _workbook_path(filename: str) -> Path:
    """Resolve lookup workbook path; prefer scripts/, migrate legacy copies from project root."""
    path = Path(filename)
    if path.is_absolute():
        return path
    if len(path.parts) > 1:
        canonical = (PROJECT_ROOT / path).resolve()
    else:
        canonical = (SCRIPTS_DIR / path.name).resolve()

    legacy = (PROJECT_ROOT / path.name).resolve()
    if not canonical.exists() and legacy.exists() and legacy != canonical:
        try:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(canonical)
            print(f"  Relocated {legacy.name} → {canonical.parent.name}/", flush=True)
        except OSError:
            return legacy
    return canonical

def default_lookup_path() -> Path:
    """Shared lookup workbook path (from webapp.config)."""
    from webapp.config import LOOKUP_FILE

    return LOOKUP_FILE

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
    by_merchant = build_merchant_category_map(lookups)
    if not by_merchant:
        return 0

    updated = 0
    from_merchant_sheet = (
        lookups.get(MERCHANT_CATEGORIES_SHEET) is not None
        and not lookups.get(MERCHANT_CATEGORIES_SHEET, pd.DataFrame()).empty
    )

    for idx, row in df.iterrows():
        mk = _row_merchant_key(row).lower()
        match = by_merchant.get(mk)
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

def load_lookup_workbook(lookup_path: Path) -> dict[str, pd.DataFrame]:
    """Load lookup sheets; missing file or sheet returns empty dict entries."""
    sheets: dict[str, pd.DataFrame] = {}
    if not lookup_path.exists():
        return sheets
    try:
        xl = pd.ExcelFile(lookup_path)
    except Exception:
        return sheets
    for name in LOOKUP_SHEETS:
        if name in xl.sheet_names:
            sheets[name] = pd.read_excel(xl, sheet_name=name)
    if CUSTOM_RULES_SHEET in xl.sheet_names:
        sheets[CUSTOM_RULES_SHEET] = pd.read_excel(xl, sheet_name=CUSTOM_RULES_SHEET)
    if EXPENSE_CADENCE_RULES_SHEET in xl.sheet_names:
        sheets[EXPENSE_CADENCE_RULES_SHEET] = pd.read_excel(
            xl, sheet_name=EXPENSE_CADENCE_RULES_SHEET
        )
    return sheets

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

def update_lookup_workbook(
    df: pd.DataFrame,
    lookup_path: Path,
    *,
    client: OpenAI | None = None,
    model: str = "",
    batch_size: int = BATCH_SIZE,
    use_json_mode: bool = False,
    suggested_business: pd.DataFrame | None = None,
    new_description_entries: pd.DataFrame | None = None,
    rebuild_description_lookup: bool = False,
    custom_rules_sheet: pd.DataFrame | None = None,
) -> None:
    """Merge enriched transaction data into the shared lookup workbook."""

    spend_only = df[df.get("Include in Spend?", "N") == "Y"].copy()
    if "Merchant Key" not in spend_only.columns:
        spend_only["Merchant Key"] = spend_only.apply(merchant_key, axis=1)

    semantic_spend = spend_only[
        spend_only.apply(_is_semantic_sub_category, axis=1)
    ].copy()

    merchant_categories = (
        spend_only.groupby("Merchant Key", dropna=False)
        .agg(
            **{
                "AI Category": ("AI Category", lambda s: s.mode().iat[0] if len(s) else ""),
                "AI Sub-Category": (
                    "AI Sub-Category",
                    lambda s: s.mode().iat[0] if len(s) else "",
                ),
                "Budget Tier": ("Budget Tier", lambda s: s.mode().iat[0] if len(s) else ""),
                "Type": ("Type", lambda s: s.mode().iat[0] if len(s) else ""),
                "Transaction Count": ("Merchant Key", "size"),
            }
        )
        .reset_index()
    )
    merchant_categories["Notes"] = ""
    merchant_categories = merchant_categories[
        merchant_categories.apply(_merchant_category_is_semantic, axis=1)
    ].copy()

    categories = (
        semantic_spend.groupby(["AI Category", "AI Sub-Category"], dropna=False)
        .agg(
            **{
                "Transaction Count": ("AI Sub-Category", "size"),
                "Budget Tier": ("Budget Tier", lambda s: s.mode().iat[0] if len(s) else ""),
                "Type": ("Type", lambda s: s.mode().iat[0] if len(s) else ""),
            }
        )
        .reset_index()
    )
    categories["Sub-Type"] = ""
    categories["Notes"] = ""
    categories = categories.sort_values(["AI Category", "AI Sub-Category"]).reset_index(drop=True)

    types = (
        spend_only.groupby(["Type", "Sub-Type"], dropna=False)
        .size()
        .reset_index(name="Transaction Count")
    )
    types["Notes"] = ""

    category_rules = _build_category_rules_from_df(df)

    existing = load_lookup_workbook(lookup_path)
    old_categories = existing.get("Categories")
    old_types = existing.get("Types")
    old_rules = existing.get("CategoryRules")
    old_merchant = existing.get(MERCHANT_CATEGORIES_SHEET)

    if (old_merchant is None or old_merchant.empty) and old_categories is not None:
        legacy_rows: list[dict[str, Any]] = []
        for _, row in old_categories.iterrows():
            mk = str(row.get("AI Sub-Category", "") or "").strip()
            if not mk:
                continue
            legacy_rows.append(
                {
                    "Merchant Key": mk,
                    "AI Category": row.get("AI Category", ""),
                    "AI Sub-Category": "",
                    "Budget Tier": row.get("Budget Tier", ""),
                    "Type": row.get("Type", ""),
                    "Transaction Count": row.get("Transaction Count", ""),
                    "Notes": "migrated from legacy Categories",
                }
            )
        if legacy_rows:
            old_merchant = pd.DataFrame(legacy_rows, columns=list(MERCHANT_CATEGORY_COLUMNS))

    legacy_merchant_keys: set[str] = set()
    if old_categories is not None and not old_categories.empty:
        legacy_merchant_keys = {
            str(v).strip().lower()
            for v in old_categories["AI Sub-Category"].fillna("").astype(str)
            if str(v).strip()
        }

    if old_merchant is not None and not old_merchant.empty:
        mk_key = "Merchant Key"
        old_merchant = old_merchant.copy()
        old_merchant[mk_key] = old_merchant[mk_key].fillna("").astype(str)
        merchant_categories[mk_key] = merchant_categories[mk_key].fillna("").astype(str)
        old_mk = set(old_merchant[mk_key].str.strip().str.lower())
        new_mk_only = merchant_categories[
            ~merchant_categories[mk_key].str.strip().str.lower().isin(old_mk)
        ].copy()
        old_merchant = old_merchant.merge(
            merchant_categories[[mk_key, "AI Sub-Category", "Transaction Count"]],
            on=mk_key,
            how="left",
            suffixes=("", "_new"),
        )
        if "AI Sub-Category_new" in old_merchant.columns:
            has_semantic = old_merchant["AI Sub-Category_new"].fillna("").astype(str).str.strip() != ""
            old_merchant.loc[has_semantic, "AI Sub-Category"] = old_merchant.loc[
                has_semantic, "AI Sub-Category_new"
            ]
            old_merchant.loc[has_semantic, "Transaction Count"] = old_merchant.loc[
                has_semantic, "Transaction Count_new"
            ].fillna(old_merchant.loc[has_semantic, "Transaction Count"])
            old_merchant = old_merchant.drop(
                columns=[c for c in old_merchant.columns if c.endswith("_new")]
            )
        merchant_categories = pd.concat([old_merchant, new_mk_only], ignore_index=True)
        merchant_categories = merchant_categories.sort_values(mk_key).reset_index(drop=True)

    if old_categories is not None and not old_categories.empty:
        old_categories = old_categories.copy()
        old_categories["_sub_lc"] = (
            old_categories["AI Sub-Category"].fillna("").astype(str).str.strip().str.lower()
        )
        old_categories = old_categories[
            ~old_categories["_sub_lc"].isin(legacy_merchant_keys)
        ].drop(columns=["_sub_lc"])

    if old_categories is not None and not old_categories.empty:
        key_cols = ["AI Category", "AI Sub-Category"]
        for col in key_cols:
            old_categories[col] = old_categories[col].fillna("").astype(str)
            categories[col] = categories[col].fillna("").astype(str)
        old_keyset = set(
            tuple(x) for x in old_categories[key_cols].values.tolist()
        )

        new_keyset = set(
            tuple(x) for x in categories[key_cols].values.tolist()
        )

        # Append only truly new pairs; keep existing rows as-is.
        new_only = categories[
            ~categories[key_cols]
            .fillna("")
            .astype(str)
            .apply(lambda r: tuple(r.values.tolist()), axis=1)
            .isin(old_keyset)
        ].copy()

        # Update Transaction Count for existing rows (Budget Tier/Type/Notes preserved).
        old_categories = old_categories.copy()
        computed_counts = categories[key_cols + ["Transaction Count"]].copy()
        old_categories = old_categories.merge(
            computed_counts, on=key_cols, how="left", suffixes=("", "_computed")
        )
        if "Transaction Count_computed" in old_categories.columns:
            old_categories["Transaction Count"] = old_categories[
                "Transaction Count_computed"
            ].fillna(old_categories["Transaction Count"])
            old_categories = old_categories.drop(columns=["Transaction Count_computed"])

        categories = pd.concat([old_categories, new_only], ignore_index=True)
        categories = categories.sort_values(key_cols).reset_index(drop=True)

    if old_types is not None and not old_types.empty:
        type_key_cols = ["Type", "Sub-Type"]
        for col in type_key_cols:
            old_types[col] = old_types[col].fillna("").astype(str)
            types[col] = types[col].fillna("").astype(str)
        old_type_keyset = set(
            tuple(x) for x in old_types[type_key_cols].values.tolist()
        )
        new_only_types = types[
            ~types[type_key_cols]
            .apply(lambda r: tuple(r.values.tolist()), axis=1)
            .isin(old_type_keyset)
        ].copy()

        # Update Transaction Count for existing types only
        old_types = old_types.copy()
        computed_type_counts = types[type_key_cols + ["Transaction Count"]].copy()
        old_types = old_types.merge(
            computed_type_counts, on=type_key_cols, how="left", suffixes=("", "_computed")
        )
        if "Transaction Count_computed" in old_types.columns:
            old_types["Transaction Count"] = old_types[
                "Transaction Count_computed"
            ].fillna(old_types["Transaction Count"])
            old_types = old_types.drop(columns=["Transaction Count_computed"])

        types = pd.concat([old_types, new_only_types], ignore_index=True)
        types = types.sort_values(type_key_cols).reset_index(drop=True)

    if old_rules is not None and not old_rules.empty and "Source Category" in old_rules.columns:
        rule_cols = ["Source Category", "AI Category", "Budget Tier", "Type", "Sub-Type", "Notes"]
        old_rules = old_rules.copy()
        for col in rule_cols:
            if col not in old_rules.columns:
                old_rules[col] = ""
        old_rules["Source Category"] = old_rules["Source Category"].fillna("").astype(str)
        category_rules["Source Category"] = category_rules["Source Category"].fillna("").astype(str)
        old_rule_keys = set(old_rules["Source Category"].tolist())
        new_rules_only = category_rules[
            ~category_rules["Source Category"].isin(old_rule_keys)
        ].copy()
        category_rules = pd.concat([old_rules[rule_cols], new_rules_only], ignore_index=True)
        category_rules = category_rules.sort_values("Source Category").reset_index(drop=True)

    business_rules = existing.get("BusinessCategoryRules")
    if suggested_business is None and client is not None and model:
        suggested_business = enrich_business_lookup_rules(
            df,
            client,
            model,
            existing,
            batch_size=batch_size,
            use_json_mode=use_json_mode,
        )
    business_rules = merge_business_category_rules(
        business_rules, suggested_business if suggested_business is not None else pd.DataFrame()
    )

    description_lookup = merge_description_lookup(
        existing.get("DescriptionLookup"),
        new_description_entries,
        rebuild=rebuild_description_lookup,
    )

    if merchant_categories.empty:
        merchant_categories = pd.DataFrame(columns=list(MERCHANT_CATEGORY_COLUMNS))

    with open_excel_workbook(lookup_path) as writer:
        categories.to_excel(writer, sheet_name="Categories", index=False)
        merchant_categories.to_excel(writer, sheet_name=MERCHANT_CATEGORIES_SHEET, index=False)
        category_rules.to_excel(writer, sheet_name="CategoryRules", index=False)
        types.to_excel(writer, sheet_name="Types", index=False)
        business_rules.to_excel(writer, sheet_name="BusinessCategoryRules", index=False)
        description_lookup.to_excel(writer, sheet_name="DescriptionLookup", index=False)
        custom_out = normalize_custom_rules_sheet(
            custom_rules_sheet if custom_rules_sheet is not None else existing.get(CUSTOM_RULES_SHEET)
        )
        custom_out.to_excel(writer, sheet_name=CUSTOM_RULES_SHEET, index=False)
        cadence_rules = normalize_expense_cadence_rules_sheet(
            existing.get(EXPENSE_CADENCE_RULES_SHEET)
        )
        cadence_rules.to_excel(writer, sheet_name=EXPENSE_CADENCE_RULES_SHEET, index=False)
