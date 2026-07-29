from __future__ import annotations

from typing import Any

import pandas as pd
from openai import OpenAI

from webapp.llm.classify import suggest_business_rules_batch
from webapp.processing.parse import _row_merchant_key, is_business_row


def business_category_rule_keys(lookups: dict[str, pd.DataFrame]) -> set[str]:
    """Generated Description keys on BusinessCategoryRules (case-insensitive match)."""
    frame = lookups.get("BusinessCategoryRules")
    if frame is None or frame.empty or "Generated Description" not in frame.columns:
        return set()
    return {
        str(val).strip().lower()
        for val in frame["Generated Description"].fillna("").astype(str)
        if str(val).strip()
    }


def mark_business_from_category_rules(
    df: pd.DataFrame,
    lookups: dict[str, pd.DataFrame],
) -> int:
    """
    Set Classification=Business when Generated Description matches BusinessCategoryRules.
    Bank exports often leave Classification as Personal; this applies your lookup list.
    """
    keys = business_category_rule_keys(lookups)
    if not keys or "Generated Description" not in df.columns:
        return 0
    if "Classification" not in df.columns:
        df["Classification"] = "Personal"

    marked = 0
    for idx, row in df.iterrows():
        desc = str(row.get("Generated Description", "") or "").strip().lower()
        if desc not in keys:
            continue
        if str(row.get("Classification", "") or "").strip().lower() != "business":
            df.at[idx, "Classification"] = "Business"
            marked += 1
    return marked

def _business_rules_lookup_keys(lookups: dict[str, pd.DataFrame]) -> set[str]:
    from webapp.processing.lookups import build_merchant_category_map

    keys: set[str] = set()
    keys |= business_category_rule_keys(lookups)
    for mk in build_merchant_category_map(lookups):
        if mk:
            keys.add(mk)
    return keys

def business_rows_needing_rules(
    df: pd.DataFrame,
    lookups: dict[str, pd.DataFrame],
) -> pd.Index:
    known = _business_rules_lookup_keys(lookups)
    indices: list[int] = []
    for idx, row in df.iterrows():
        if not is_business_row(row):
            continue
        desc = str(row.get("Generated Description", "") or "").strip().lower()
        mk = _row_merchant_key(row).lower()
        if desc in known or mk in known:
            continue
        indices.append(int(idx))
    return pd.Index(indices)

def enrich_business_lookup_rules(
    df: pd.DataFrame,
    client: OpenAI,
    model: str,
    lookups: dict[str, pd.DataFrame],
    *,
    batch_size: int,
    use_json_mode: bool,
) -> pd.DataFrame:
    """Return new BusinessCategoryRules rows for Business transactions missing lookup coverage."""
    need_idx = business_rows_needing_rules(df, lookups)
    if need_idx.empty:
        return pd.DataFrame(
            columns=[
                "Generated Description",
                "Source Category",
                "AI Category",
                "AI Sub-Category",
                "Budget Tier",
                "Type",
                "Sub-Type",
                "Classification",
                "Notes",
            ]
        )

    print(f"  Business lookup rules to analyze: {len(need_idx)}", flush=True)
    new_rows: list[dict[str, Any]] = []

    indices = need_idx.tolist()
    for start in range(0, len(indices), batch_size):
        batch_idx = indices[start : start + batch_size]
        payloads = []
        for i in batch_idx:
            row = df.loc[i]
            payloads.append(
                {
                    "index": int(i),
                    "generated_description": str(row.get("Generated Description", ""))[:200],
                    "original_description": str(row.get("Original Description", ""))[:300],
                    "category": str(row.get("Category", "")),
                    "amount": str(row.get("Amount", "")),
                    "account": str(row.get("Account Name", "")),
                }
            )
        try:
            results = suggest_business_rules_batch(
                client, payloads, model, use_json_mode=use_json_mode
            )
        except Exception as exc:
            print(f"  Business rule LLM failed; using row defaults: {exc}", flush=True)
            results = []

        result_by_idx = {int(r.get("index", -1)): r for r in results}
        for i in batch_idx:
            row = df.loc[i]
            r = result_by_idx.get(int(i), {})
            desc = str(row.get("Generated Description", "") or "").strip()
            new_rows.append(
                {
                    "Generated Description": desc,
                    "Source Category": str(row.get("Category", "") or ""),
                    "AI Category": str(
                        r.get("ai_category", row.get("AI Category", "")) or ""
                    ).strip(),
                    "AI Sub-Category": str(
                        r.get("ai_sub_category", row.get("AI Sub-Category", desc)) or ""
                    ).strip(),
                    "Budget Tier": str(
                        r.get("budget_tier", row.get("Budget Tier", "")) or ""
                    ).strip(),
                    "Type": str(r.get("type", row.get("Type", "Variable")) or "Variable").strip(),
                    "Sub-Type": str(row.get("Sub-Type", "") or ""),
                    "Classification": "Business",
                    "Notes": str(r.get("notes", "Auto-added from Business transaction") or ""),
                }
            )

    return pd.DataFrame(new_rows)

def apply_business_rules_to_df(
    df: pd.DataFrame,
    rules: pd.DataFrame,
    *,
    spend_mask: pd.Series | None = None,
) -> int:
    """Apply BusinessCategoryRules rows to matching Business transactions in-place."""
    if rules is None or rules.empty or "Generated Description" not in rules.columns:
        return 0
    by_desc: dict[str, pd.Series] = {}
    for _, brow in rules.iterrows():
        key = str(brow.get("Generated Description", "") or "").strip().lower()
        if key:
            by_desc[key] = brow
    updated = 0
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
            ("Flow Type", "Flow Type"),
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

def merge_business_category_rules(
    existing: pd.DataFrame | None,
    new_rules: pd.DataFrame,
) -> pd.DataFrame:
    cols = [
        "Generated Description",
        "Source Category",
        "AI Category",
        "AI Sub-Category",
        "Budget Tier",
        "Type",
        "Sub-Type",
        "Classification",
        "Notes",
    ]
    if new_rules is None or new_rules.empty:
        if existing is None or existing.empty:
            return pd.DataFrame(columns=cols)
        return existing

    if existing is None or existing.empty:
        out = new_rules.copy()
    else:
        existing = existing.copy()
        for col in cols:
            if col not in existing.columns:
                existing[col] = ""
        new_rules = new_rules.copy()
        known = set(existing["Generated Description"].fillna("").astype(str).str.strip().str.lower())
        append = new_rules[
            ~new_rules["Generated Description"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .isin(known)
        ]
        out = pd.concat([existing[cols], append[cols]], ignore_index=True)

    out["Generated Description"] = out["Generated Description"].fillna("").astype(str)
    out = out.drop_duplicates(subset=["Generated Description"], keep="first")
    return out.sort_values("Generated Description").reset_index(drop=True)
