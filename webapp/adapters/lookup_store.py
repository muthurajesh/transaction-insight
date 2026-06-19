"""Load and persist pipeline lookup data in SQLite (replaces Excel at runtime)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
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
from webapp.processing.lookups import load_lookup_workbook, update_lookup_workbook
from webapp.services.expense_cadence import sync_cadence_rules_from_lookup_records


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row[0] if row else 0)


def ensure_lookups_seeded(conn: sqlite3.Connection, excel_path: Path) -> bool:
    """
    One-time import from transaction-lookups.xlsx when DB lookup tables are empty.
    Returns True if seed ran.
    """
    if _table_count(conn, "description_lookup") > 0:
        return False
    if not excel_path.is_file():
        return False

    sheets = load_lookup_workbook(excel_path)
    if not sheets:
        return False

    now = _utc_now()
    desc = sheets.get("DescriptionLookup")
    if desc is not None and not desc.empty:
        for _, row in desc.iterrows():
            conn.execute(
                """
                INSERT OR IGNORE INTO description_lookup (
                    source_key, user_description, simple_description,
                    original_description, generated_description, source, model, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("Source Key", "") or "").strip(),
                    str(row.get("User Description", "") or ""),
                    str(row.get("Simple Description", "") or ""),
                    str(row.get("Original Description", "") or ""),
                    str(row.get("Generated Description", "") or "").strip(),
                    str(row.get("Source", "") or ""),
                    str(row.get("Model", "") or ""),
                    str(row.get("Updated At", "") or now),
                ),
            )

    rules = sheets.get("CategoryRules")
    if rules is not None and not rules.empty:
        for _, row in rules.iterrows():
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

    mc = sheets.get(MERCHANT_CATEGORIES_SHEET)
    if mc is not None and not mc.empty:
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
                ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'seed: excel', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(merchant_key) DO UPDATE SET
                    ai_category=excluded.ai_category,
                    ai_sub_category=excluded.ai_sub_category,
                    expense_type=excluded.expense_type,
                    budget_tier=COALESCE(excluded.budget_tier, merchant_labels.budget_tier),
                    classification=COALESCE(excluded.classification, merchant_labels.classification),
                    flow_type=COALESCE(excluded.flow_type, merchant_labels.flow_type),
                    notes=COALESCE(excluded.notes, merchant_labels.notes),
                    sample_count=COALESCE(excluded.sample_count, merchant_labels.sample_count),
                    updated_at=excluded.updated_at
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

    business = sheets.get("BusinessCategoryRules")
    if business is not None and not business.empty:
        for _, row in business.iterrows():
            mk = str(row.get("Generated Description", "") or "").strip()
            cat = str(row.get("AI Category", "") or "").strip()
            if not mk or not cat:
                continue
            conn.execute(
                """
                INSERT INTO merchant_labels (
                    merchant_key, ai_category, ai_sub_category, expense_type,
                    confidence, label_status, rationale, sample_count, updated_at,
                    budget_tier, classification, flow_type, notes
                ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'seed: BusinessCategoryRules', 0, ?, ?, ?, ?, ?)
                ON CONFLICT(merchant_key) DO UPDATE SET
                    ai_category=excluded.ai_category,
                    ai_sub_category=excluded.ai_sub_category,
                    expense_type=excluded.expense_type,
                    budget_tier=COALESCE(excluded.budget_tier, merchant_labels.budget_tier),
                    classification=COALESCE(excluded.classification, merchant_labels.classification),
                    updated_at=excluded.updated_at
                """,
                (
                    mk,
                    cat,
                    str(row.get("AI Sub-Category", "") or ""),
                    str(row.get("Type", "") or "Variable"),
                    now,
                    str(row.get("Budget Tier", "") or ""),
                    str(row.get("Classification", "") or "Business"),
                    "",
                    str(row.get("Notes", "") or ""),
                ),
            )

    custom = sheets.get(CUSTOM_RULES_SHEET)
    if custom is not None and not custom.empty:
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

    cadence = sheets.get(EXPENSE_CADENCE_RULES_SHEET)
    if cadence is not None and not cadence.empty:
        payload = cadence.fillna("").astype(str).to_dict(orient="records")
        sync_cadence_rules_from_lookup_records(conn, payload)

    conn.commit()
    return True


def load_lookup_workbook_from_db(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    """Build the same sheet dict shape as load_lookup_workbook (Excel)."""
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
        ORDER BY merchant_key
        """
    ).fetchall()
    if ml_rows:
        sheets[MERCHANT_CATEGORIES_SHEET] = pd.DataFrame([dict(r) for r in ml_rows])
        business_rows = [
            {
                "Generated Description": r["Merchant Key"],
                "AI Category": r["AI Category"],
                "AI Sub-Category": r["AI Sub-Category"],
                "Budget Tier": r["Budget Tier"],
                "Type": r["Type"],
                "Classification": r["Classification"],
                "Notes": r["Notes"],
            }
            for r in (dict(x) for x in ml_rows)
            if str(dict(x).get("Classification", "")).lower() == "business"
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


def save_lookup_workbook_to_db(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    *,
    lookup_path: Path,
    suggested_business: pd.DataFrame | None = None,
    new_description_entries: pd.DataFrame | None = None,
    rebuild_description_lookup: bool = False,
    custom_rules_sheet: pd.DataFrame | None = None,
    client: Any = None,
    model: str = "",
    batch_size: int = 40,
    use_json_mode: bool = False,
) -> None:
    """
    Reuse Excel merge logic in a temp workbook path, then mirror merged sheets into SQLite.
    """
    update_lookup_workbook(
        df,
        lookup_path,
        client=client,
        model=model,
        batch_size=batch_size,
        use_json_mode=use_json_mode,
        suggested_business=suggested_business,
        new_description_entries=new_description_entries,
        rebuild_description_lookup=rebuild_description_lookup,
        custom_rules_sheet=custom_rules_sheet,
    )
    sheets = load_lookup_workbook(lookup_path)
    now = _utc_now()

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

    rules = sheets.get("CategoryRules")
    if rules is not None and not rules.empty:
        for _, row in rules.iterrows():
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

    mc = sheets.get(MERCHANT_CATEGORIES_SHEET)
    if mc is not None and not mc.empty:
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

    custom = sheets.get(CUSTOM_RULES_SHEET)
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

    cadence = sheets.get(EXPENSE_CADENCE_RULES_SHEET)
    if cadence is not None and not cadence.empty:
        sync_cadence_rules_from_lookup_records(
            conn, cadence.fillna("").astype(str).to_dict(orient="records")
        )

    conn.commit()
