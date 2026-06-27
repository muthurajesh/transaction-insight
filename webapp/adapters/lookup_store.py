"""Load and persist pipeline lookup data in SQLite."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from webapp.processing.constants import (
    CUSTOM_RULES_COLUMNS,
    CUSTOM_RULES_SHEET,
    DESCRIPTION_LOOKUP_COLUMNS,
    EXPENSE_CADENCE_RULES_SHEET,
    MERCHANT_CATEGORIES_SHEET,
    MERCHANT_CATEGORY_COLUMNS,
)
from webapp.processing.lookups import _build_category_rules_from_df
from webapp.processing.parse import _merchant_category_is_semantic, merchant_key


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row[0] if row else 0)


def load_lookup_workbook_from_db(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    """Build in-memory lookup dicts (sheet names) from SQLite tables."""
    sheets: dict[str, pd.DataFrame] = {}

    desc_rows = conn.execute(
        """
        SELECT source_key AS "Source Key",
               user_description AS "User Description",
               simple_description AS "Simple Description",
               original_description AS "Original Description",
               generated_description AS "Generated Description",
               source AS "Source",
               model AS "Model",
               updated_at AS "Updated At"
        FROM description_lookup
        ORDER BY source_key
        """
    ).fetchall()
    if desc_rows:
        sheets["DescriptionLookup"] = pd.DataFrame([dict(r) for r in desc_rows])

    rule_rows = conn.execute(
        """
        SELECT source_category AS "Source Category",
               ai_category AS "AI Category",
               budget_tier AS "Budget Tier",
               type AS "Type",
               sub_type AS "Sub-Type",
               notes AS "Notes"
        FROM category_rules
        ORDER BY source_category
        """
    ).fetchall()
    if rule_rows:
        sheets["CategoryRules"] = pd.DataFrame([dict(r) for r in rule_rows])

    ml_rows = conn.execute(
        """
        SELECT merchant_key AS "Merchant Key",
               ai_category AS "AI Category",
               ai_sub_category AS "AI Sub-Category",
               COALESCE(budget_tier, '') AS "Budget Tier",
               expense_type AS "Type",
               COALESCE(flow_type, '') AS "Flow Type",
               COALESCE(classification, '') AS "Classification",
               COALESCE(sample_count, 0) AS "Transaction Count",
               COALESCE(notes, '') AS "Notes"
        FROM merchant_labels
        WHERE label_status = 'confirmed'
          AND (
              LOWER(COALESCE(rationale, '')) IN ('user edited', 'user confirmed')
              OR LOWER(COALESCE(rationale, '')) LIKE 'confirmed via web%'
              OR LOWER(COALESCE(notes, '')) LIKE '%confirmed via web%'
          )
        ORDER BY merchant_key
        """
    ).fetchall()
    if ml_rows:
        sheets[MERCHANT_CATEGORIES_SHEET] = pd.DataFrame([dict(r) for r in ml_rows])
        business_rows = [
            {
                "Generated Description": row["Merchant Key"],
                "AI Category": row["AI Category"],
                "AI Sub-Category": row["AI Sub-Category"],
                "Budget Tier": row["Budget Tier"],
                "Type": row["Type"],
                "Classification": row["Classification"],
                "Notes": row["Notes"],
            }
            for row in (dict(r) for r in ml_rows)
            if str(row.get("Classification", "")).lower() == "business"
        ]
        if business_rows:
            sheets["BusinessCategoryRules"] = pd.DataFrame(business_rows)

    custom_rows = conn.execute(
        """
        SELECT rule_text AS "Rule",
               status AS "Status",
               compiled_rule AS "Compiled Rule",
               last_error AS "Last Error",
               updated_at AS "Updated At"
        FROM pipeline_custom_rules
        ORDER BY id
        """
    ).fetchall()
    if custom_rows:
        sheets[CUSTOM_RULES_SHEET] = pd.DataFrame([dict(r) for r in custom_rows])
    else:
        sheets[CUSTOM_RULES_SHEET] = pd.DataFrame(columns=list(CUSTOM_RULES_COLUMNS))

    cadence_rows = conn.execute(
        """
        SELECT merchant_key AS "Generated Description",
               cadence_kind AS "Cadence",
               CASE WHEN include_in_run_rate = 1 THEN 'Y' WHEN include_in_run_rate = 0 THEN 'N' ELSE '' END
                   AS "In Monthly Run-Rate?",
               COALESCE(notes, '') AS "Notes"
        FROM cadence_rules
        WHERE enabled = 1
        ORDER BY merchant_key
        """
    ).fetchall()
    if cadence_rows:
        sheets[EXPENSE_CADENCE_RULES_SHEET] = pd.DataFrame([dict(r) for r in cadence_rows])

    return sheets


def _merchant_categories_from_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate spend rows into merchant label rows (no Excel)."""
    from webapp.processing.constants import MERCHANT_CATEGORY_COLUMNS

    spend_only = df[df.get("Include in Spend?", "N") == "Y"].copy()
    if spend_only.empty:
        return pd.DataFrame(columns=list(MERCHANT_CATEGORY_COLUMNS))
    if "Merchant Key" not in spend_only.columns:
        spend_only["Merchant Key"] = spend_only.apply(merchant_key, axis=1)

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
    merchant_categories["Flow Type"] = ""
    merchant_categories["Classification"] = ""
    return merchant_categories[
        merchant_categories.apply(_merchant_category_is_semantic, axis=1)
    ].copy()


def save_lookup_workbook_to_db(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    *,
    suggested_business: pd.DataFrame | None = None,
    new_description_entries: pd.DataFrame | None = None,
    rebuild_description_lookup: bool = False,
    custom_rules_sheet: pd.DataFrame | None = None,
    client: Any = None,
    model: str = "",
    batch_size: int = 40,
    use_json_mode: bool = False,
) -> None:
    """Persist pipeline lookup updates to SQLite."""
    from webapp.llm.validation import generated_description_plausible

    now = _utc_now()

    if new_description_entries is not None and not new_description_entries.empty:
        valid_rows = []
        for _, row in new_description_entries.iterrows():
            desc = str(row.get("Generated Description", "") or "").strip()
            pseudo = pd.Series(
                {
                    "User Description": row.get("User Description", ""),
                    "Simple Description": row.get("Simple Description", ""),
                    "Original Description": row.get("Original Description", ""),
                }
            )
            if desc and generated_description_plausible(desc, pseudo):
                valid_rows.append(row)
        if valid_rows:
            new_description_entries = pd.DataFrame(valid_rows)
        else:
            new_description_entries = pd.DataFrame()

    if new_description_entries is not None and not new_description_entries.empty:
        for _, row in new_description_entries.iterrows():
            key = str(row.get("Source Key", "") or "").strip()
            gen = str(row.get("Generated Description", "") or "").strip()
            if not key or not gen:
                continue
            conn.execute(
                """
                INSERT INTO description_lookup (
                    source_key, user_description, simple_description,
                    original_description, generated_description, source, model, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    generated_description=excluded.generated_description,
                    source=excluded.source,
                    model=excluded.model,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    str(row.get("User Description", "") or ""),
                    str(row.get("Simple Description", "") or ""),
                    str(row.get("Original Description", "") or ""),
                    gen,
                    str(row.get("Source", "") or ""),
                    str(row.get("Model", "") or ""),
                    str(row.get("Updated At", "") or now),
                ),
            )

    category_rules = _build_category_rules_from_df(df)
    if not category_rules.empty:
        for _, row in category_rules.iterrows():
            src = str(row.get("Source Category", "") or "").strip()
            if not src:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO category_rules (
                    source_category, ai_category, budget_tier, type, sub_type, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    src,
                    str(row.get("AI Category", "") or ""),
                    str(row.get("Budget Tier", "") or ""),
                    str(row.get("Type", "") or ""),
                    str(row.get("Sub-Type", "") or ""),
                    str(row.get("Notes", "") or ""),
                    now,
                ),
            )

    mc = _merchant_categories_from_pipeline(df)
    if not mc.empty:
        for _, row in mc.iterrows():
            mk = str(row.get("Merchant Key", "") or "").strip()
            cat = str(row.get("AI Category", "") or "").strip()
            if not mk or not cat:
                continue
            conn.execute(
                """
                INSERT INTO merchant_labels (
                    merchant_key, ai_category, ai_sub_category, expense_type,
                    confidence, label_status, rationale, sample_count, updated_at,
                    budget_tier, classification, flow_type, notes
                ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'pipeline', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(merchant_key) DO UPDATE SET
                    ai_category=excluded.ai_category,
                    ai_sub_category=excluded.ai_sub_category,
                    expense_type=excluded.expense_type,
                    budget_tier=excluded.budget_tier,
                    classification=excluded.classification,
                    flow_type=excluded.flow_type,
                    notes=excluded.notes,
                    sample_count=excluded.sample_count,
                    updated_at=excluded.updated_at
                WHERE NOT (
                    merchant_labels.label_status = 'confirmed'
                    AND (
                        LOWER(COALESCE(merchant_labels.rationale, '')) IN ('user edited', 'user confirmed')
                        OR LOWER(COALESCE(merchant_labels.rationale, '')) LIKE 'confirmed via web%'
                        OR LOWER(COALESCE(merchant_labels.notes, '')) LIKE '%confirmed via web%'
                    )
                )
                """,
                (
                    mk,
                    cat,
                    str(row.get("AI Sub-Category", "") or ""),
                    str(row.get("Type", "") or "Variable"),
                    int(row.get("Transaction Count", 0) or 0),
                    now,
                    str(row.get("Budget Tier", "") or ""),
                    str(row.get("Classification", "") or ""),
                    str(row.get("Flow Type", "") or ""),
                    str(row.get("Notes", "") or ""),
                ),
            )

    custom = custom_rules_sheet
    if custom is not None:
        conn.execute("DELETE FROM pipeline_custom_rules")
        for _, row in custom.iterrows():
            rule_text = str(row.get("Rule", "") or "").strip()
            if not rule_text:
                continue
            conn.execute(
                """
                INSERT INTO pipeline_custom_rules (
                    rule_text, status, compiled_rule, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rule_text,
                    str(row.get("Status", "") or "Pending"),
                    str(row.get("Compiled Rule", "") or ""),
                    str(row.get("Last Error", "") or ""),
                    str(row.get("Updated At", "") or now),
                ),
            )

    conn.commit()
