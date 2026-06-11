from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from transaction_insight.config import default_lookup_path
from transaction_insight.core import (
    CUSTOM_RULES_SHEET,
    CUSTOM_RULE_STATUS_ACTIVE,
    LOOKUP_SHEETS,
    MERCHANT_CATEGORIES_SHEET,
    MERCHANT_CATEGORY_COLUMNS,
    budget_tier_from_category,
    load_active_custom_rules,
    load_lookup_workbook,
    normalize_custom_rules_sheet,
    open_excel_workbook,
)
from webapp.services.categorize import (
    CLASSIFICATION_OPTIONS,
    SPLIT_REVIEW_MERCHANT_KEYS,
    _normalize_classification,
    confirm_transaction,
)

FLOW_TYPES = frozenset({"Expense", "Income", "Transfer", "Adjustment"})
CONFIRM_SCOPES = frozenset({"pending", "all"})
LABEL_COMPARE_FIELDS = (
    "ai_category",
    "ai_sub_category",
    "flow_type",
    "expense_type",
    "classification",
)

BUSINESS_AI_CATEGORIES = frozenset({"business expenses", "business"})

BUSINESS_RULE_COLUMNS = [
    "Generated Description",
    "Source Category",
    "AI Category",
    "AI Sub-Category",
    "Budget Tier",
    "Type",
    "Sub-Type",
    "Classification",
    "Flow Type",
    "Notes",
]


def lookup_workbook_path() -> Path:
    return default_lookup_path()


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none"):
        return ""
    return text


def _normalize_flow_type(value: str) -> str:
    flow = _norm(value) or "Expense"
    if flow not in FLOW_TYPES:
        raise ValueError(f"flow_type must be one of: {', '.join(sorted(FLOW_TYPES))}")
    return flow


def align_classification_with_category(labels: dict[str, str]) -> dict[str, str]:
    """Business spend categories always use Classification = Business."""
    out = dict(labels)
    category = _norm(out.get("ai_category")).lower()
    if category not in BUSINESS_AI_CATEGORIES:
        return out
    if _normalize_classification(out.get("classification")) != "Business":
        out["classification"] = "Business"
    return out


def classification_adjusted_for_category(
    before: dict[str, str],
    after: dict[str, str],
) -> bool:
    return (
        _normalize_classification(before.get("classification"))
        != _normalize_classification(after.get("classification"))
        and _normalize_classification(after.get("classification")) == "Business"
    )


def _labels_from_proposed(
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(ai_category),
            "ai_sub_category": _norm(ai_sub_category),
            "expense_type": _norm(expense_type) or "Variable",
            "flow_type": _normalize_flow_type(flow_type),
            "classification": _normalize_classification(classification),
        }
    )


def _labels_from_merchant_row(row: pd.Series) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(row.get("AI Category")),
            "ai_sub_category": _norm(row.get("AI Sub-Category")),
            "expense_type": _norm(row.get("Type")) or "Variable",
            "flow_type": _norm(row.get("Flow Type")) or "Expense",
            "classification": _norm(row.get("Classification")) or "Personal",
        }
    )


def _labels_from_business_row(row: pd.Series) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(row.get("AI Category")),
            "ai_sub_category": _norm(row.get("AI Sub-Category")),
            "expense_type": _norm(row.get("Type")) or "Variable",
            "flow_type": _norm(row.get("Flow Type")) or "Expense",
            "classification": _norm(row.get("Classification")) or "Business",
        }
    )


def _labels_from_custom_set(spec: dict[str, Any]) -> dict[str, str]:
    ai_category = _norm(spec.get("ai_category")) or _norm(spec.get("category"))
    return align_classification_with_category(
        {
            "ai_category": ai_category,
            "ai_sub_category": _norm(spec.get("ai_sub_category")),
            "expense_type": _norm(spec.get("type")) or "Variable",
            "flow_type": _norm(spec.get("flow_type")) or "Expense",
            "classification": _norm(spec.get("classification")) or "Personal",
        }
    )


def _labels_conflict(existing: dict[str, str], proposed: dict[str, str]) -> bool:
    for key in LABEL_COMPARE_FIELDS:
        ex = _norm(existing.get(key))
        pr = _norm(proposed.get(key))
        if not ex:
            continue
        if ex != pr:
            return True
    return False


def _load_all_sheets(path: Path) -> dict[str, pd.DataFrame]:
    if not path.is_file():
        return {}
    xl = pd.ExcelFile(path)
    return {name: pd.read_excel(path, sheet_name=name) for name in xl.sheet_names}


def _save_workbook_sheets(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_excel_workbook(path) as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)


def _ensure_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy() if frame is not None and not frame.empty else pd.DataFrame()
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    if out.empty:
        return pd.DataFrame(columns=columns)
    return out[list(columns)]


def _custom_rule_targets_merchant(rule: dict[str, Any], merchant_key: str) -> bool:
    match = rule.get("match") or {}
    gen = match.get("generated_description")
    mk = merchant_key.strip().lower()
    if gen is None:
        return False
    patterns = gen if isinstance(gen, list) else [gen]
    for pattern in patterns:
        text = _norm(pattern).lower()
        if not text:
            continue
        if text == mk or text.strip("*") == mk:
            return True
        if text.startswith("*") and text.endswith("*") and mk in text.strip("*"):
            return True
    return False


def find_excel_conflicts(
    merchant_key: str,
    proposed: dict[str, str],
    *,
    lookup_path: Path | None = None,
) -> list[dict[str, Any]]:
    path = lookup_path or lookup_workbook_path()
    if not path.is_file():
        return []

    conflicts: list[dict[str, Any]] = []
    lookups = load_lookup_workbook(path)
    mk_lower = merchant_key.strip().lower()

    merchant_sheet = lookups.get(MERCHANT_CATEGORIES_SHEET)
    if merchant_sheet is not None and not merchant_sheet.empty and "Merchant Key" in merchant_sheet.columns:
        for _, row in merchant_sheet.iterrows():
            mk = _norm(row.get("Merchant Key"))
            if mk.lower() != mk_lower:
                continue
            existing = _labels_from_merchant_row(row)
            if _labels_conflict(existing, proposed):
                conflicts.append(
                    {
                        "source": MERCHANT_CATEGORIES_SHEET,
                        "merchant_key": mk,
                        "existing": existing,
                        "proposed": proposed,
                    }
                )
            break

    business_sheet = lookups.get("BusinessCategoryRules")
    if business_sheet is not None and not business_sheet.empty:
        if "Generated Description" in business_sheet.columns:
            for _, row in business_sheet.iterrows():
                desc = _norm(row.get("Generated Description"))
                if desc.lower() != mk_lower:
                    continue
                existing = _labels_from_business_row(row)
                if _labels_conflict(existing, proposed):
                    conflicts.append(
                        {
                            "source": "BusinessCategoryRules",
                            "merchant_key": desc,
                            "existing": existing,
                            "proposed": proposed,
                        }
                    )
                break

    custom_sheet = normalize_custom_rules_sheet(lookups.get(CUSTOM_RULES_SHEET))
    for rule in load_active_custom_rules(custom_sheet):
        if not _custom_rule_targets_merchant(rule, merchant_key):
            continue
        spec = rule.get("set") or {}
        existing = _labels_from_custom_set(spec)
        if _labels_conflict(existing, proposed):
            conflicts.append(
                {
                    "source": CUSTOM_RULES_SHEET,
                    "merchant_key": merchant_key,
                    "existing": existing,
                    "proposed": proposed,
                    "rule_type": rule.get("rule_type"),
                }
            )
    return conflicts


def _category_breakdown(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (_norm(row["ai_category"]), _norm(row["ai_sub_category"]))
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "ai_category": cat,
            "ai_sub_category": sub,
            "count": count,
        }
        for (cat, sub), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _row_differs_from_proposed(row: sqlite3.Row, proposed: dict[str, str]) -> bool:
    for field in LABEL_COMPARE_FIELDS:
        if _norm(row[field]) != _norm(proposed.get(field)):
            return True
    return False


def confirm_preview(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
) -> dict[str, Any]:
    mk = merchant_key.strip()
    if not mk:
        raise ValueError("merchant_key is required")
    proposed = _labels_from_proposed(
        ai_category=ai_category,
        ai_sub_category=ai_sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
    )
    if not proposed["ai_category"]:
        raise ValueError("ai_category is required")

    all_rows = conn.execute(
        """
        SELECT ai_category, ai_sub_category, flow_type, expense_type,
               classification, label_status
        FROM transactions
        WHERE merchant_key = ?
        """,
        (mk,),
    ).fetchall()

    pending_rows = [
        r for r in all_rows if _norm(r["label_status"]).lower() in ("needs_review", "pending")
    ]
    confirmed_rows = [
        r for r in all_rows if _norm(r["label_status"]).lower() not in ("needs_review", "pending")
    ]
    differing_confirmed = [r for r in confirmed_rows if _row_differs_from_proposed(r, proposed)]

    breakdown = _category_breakdown(all_rows)
    distinct_patterns = len(breakdown)
    suggest_custom_rule = distinct_patterns >= 2

    excel_conflicts = find_excel_conflicts(mk, proposed)
    lookup_path = lookup_workbook_path()

    return {
        "merchant_key": mk,
        "proposed": proposed,
        "total_count": len(all_rows),
        "pending_count": len(pending_rows),
        "confirmed_count": len(confirmed_rows),
        "differing_confirmed_count": len(differing_confirmed),
        "differing_breakdown": _category_breakdown(differing_confirmed),
        "category_patterns": breakdown,
        "distinct_category_patterns": distinct_patterns,
        "suggest_custom_rule": suggest_custom_rule,
        "custom_rule_hint": (
            "This merchant has multiple category patterns in your data. "
            "MerchantCategories allows one label per merchant — use a custom rule "
            "for amount- or description-based splits."
            if suggest_custom_rule
            else ""
        ),
        "excel_conflicts": excel_conflicts,
        "lookup_file": str(lookup_path),
        "lookup_file_exists": lookup_path.is_file(),
    }


def sync_merchant_labels_to_workbook(
    merchant_key: str,
    proposed: dict[str, str],
    *,
    lookup_path: Path | None = None,
    tx_count: int | None = None,
) -> dict[str, Any]:
    path = lookup_path or lookup_workbook_path()
    mk = merchant_key.strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    notes = f"confirmed via web {now}"

    sheets = _load_all_sheets(path) if path.is_file() else {}
    for sheet_name in LOOKUP_SHEETS:
        if sheet_name not in sheets:
            sheets[sheet_name] = pd.DataFrame()

    merchant_df = _ensure_columns(
        sheets.get(MERCHANT_CATEGORIES_SHEET, pd.DataFrame()),
        list(MERCHANT_CATEGORY_COLUMNS),
    )
    for col in MERCHANT_CATEGORY_COLUMNS:
        merchant_df[col] = merchant_df[col].fillna("").astype(str).replace("nan", "")
    mk_lower = mk.lower()
    tier = budget_tier_from_category(proposed["ai_category"])
    row_data = {
        "Merchant Key": mk,
        "AI Category": proposed["ai_category"],
        "AI Sub-Category": proposed["ai_sub_category"],
        "Budget Tier": tier,
        "Type": proposed["expense_type"],
        "Flow Type": proposed["flow_type"],
        "Classification": proposed["classification"],
        "Transaction Count": str(tx_count) if tx_count is not None else "",
        "Notes": notes,
    }

    if "Merchant Key" in merchant_df.columns and not merchant_df.empty:
        mask = merchant_df["Merchant Key"].fillna("").astype(str).str.strip().str.lower() == mk_lower
        if mask.any():
            idx = merchant_df.index[mask][0]
            for col, val in row_data.items():
                merchant_df.at[idx, col] = val
        else:
            merchant_df = pd.concat([merchant_df, pd.DataFrame([row_data])], ignore_index=True)
    else:
        merchant_df = pd.DataFrame([row_data], columns=list(MERCHANT_CATEGORY_COLUMNS))

    sheets[MERCHANT_CATEGORIES_SHEET] = merchant_df

    if proposed["classification"] == "Business":
        business_df = _ensure_columns(
            sheets.get("BusinessCategoryRules", pd.DataFrame()),
            BUSINESS_RULE_COLUMNS,
        )
        for col in BUSINESS_RULE_COLUMNS:
            business_df[col] = business_df[col].fillna("").astype(str).replace("nan", "")
        biz_row = {
            "Generated Description": mk,
            "Source Category": "",
            "AI Category": proposed["ai_category"],
            "AI Sub-Category": proposed["ai_sub_category"],
            "Budget Tier": tier,
            "Type": proposed["expense_type"],
            "Sub-Type": "",
            "Classification": "Business",
            "Flow Type": proposed["flow_type"],
            "Notes": notes,
        }
        if "Generated Description" in business_df.columns and not business_df.empty:
            mask = (
                business_df["Generated Description"]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.lower()
                == mk_lower
            )
            if mask.any():
                idx = business_df.index[mask][0]
                for col, val in biz_row.items():
                    business_df.at[idx, col] = val
            else:
                business_df = pd.concat([business_df, pd.DataFrame([biz_row])], ignore_index=True)
        else:
            business_df = pd.DataFrame([biz_row], columns=BUSINESS_RULE_COLUMNS)
        sheets["BusinessCategoryRules"] = business_df

    _save_workbook_sheets(path, sheets)
    return {"lookup_file": str(path), "sheets_updated": [MERCHANT_CATEGORIES_SHEET]}


def _apply_merchant_to_db(
    conn: sqlite3.Connection,
    merchant_key: str,
    proposed: dict[str, str],
    *,
    scope: str,
) -> int:
    if scope not in CONFIRM_SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(sorted(CONFIRM_SCOPES))}")

    status_filter = (
        " AND label_status IN ('needs_review', 'pending')"
        if scope == "pending"
        else ""
    )
    cur = conn.execute(
        f"""
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            classification = ?,
            confidence = 1.0,
            label_status = 'confirmed',
            rationale = 'user confirmed'
        WHERE merchant_key = ?{status_filter}
        """,
        (
            proposed["flow_type"],
            proposed["ai_category"],
            proposed["ai_sub_category"],
            proposed["expense_type"],
            proposed["classification"],
            merchant_key,
        ),
    )
    return cur.rowcount


def confirm_merchant_group(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
    scope: str = "pending",
    replace_excel: bool = False,
) -> dict[str, Any]:
    mk = merchant_key.strip()
    if mk in SPLIT_REVIEW_MERCHANT_KEYS:
        raise ValueError(f"{mk} must be confirmed one transaction at a time")

    proposed = _labels_from_proposed(
        ai_category=ai_category,
        ai_sub_category=ai_sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
    )
    if not proposed["ai_category"]:
        raise ValueError("ai_category is required")
    if scope not in CONFIRM_SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(sorted(CONFIRM_SCOPES))}")

    conflicts = find_excel_conflicts(mk, proposed)
    if conflicts and not replace_excel:
        sources = ", ".join(sorted({c["source"] for c in conflicts}))
        raise ValueError(
            f"Excel conflict in {sources}. Choose Replace existing rule to overwrite, "
            "or use a custom rule for complex patterns."
        )

    preview = confirm_preview(
        conn,
        mk,
        ai_category=proposed["ai_category"],
        ai_sub_category=proposed["ai_sub_category"],
        expense_type=proposed["expense_type"],
        flow_type=proposed["flow_type"],
        classification=proposed["classification"],
    )

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at
        ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'user confirmed', 0, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            ai_category=excluded.ai_category,
            ai_sub_category=excluded.ai_sub_category,
            expense_type=excluded.expense_type,
            confidence=1.0,
            label_status='confirmed',
            updated_at=excluded.updated_at
        """,
        (mk, proposed["ai_category"], proposed["ai_sub_category"], proposed["expense_type"], now),
    )

    tx_count = preview["total_count"]
    excel_result = sync_merchant_labels_to_workbook(mk, proposed, tx_count=tx_count)
    rows_updated = _apply_merchant_to_db(conn, mk, proposed, scope=scope)
    conn.commit()

    return {
        "merchant_key": mk,
        "rows_updated": rows_updated,
        "scope": scope,
        "excel_synced": True,
        "excel_conflicts_replaced": bool(conflicts),
        "lookup_file": excel_result["lookup_file"],
        "suggest_custom_rule": preview["suggest_custom_rule"],
        "custom_rule_hint": preview["custom_rule_hint"],
    }


def confirm_merchant_or_transaction(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
    transaction_id: str | None = None,
    scope: str = "pending",
    replace_excel: bool = False,
) -> dict[str, Any]:
    if transaction_id:
        updated = confirm_transaction(
            conn,
            transaction_id,
            ai_category=ai_category,
            ai_sub_category=ai_sub_category,
            expense_type=expense_type,
            flow_type=flow_type,
            classification=classification,
        )
        return {
            "merchant_key": merchant_key,
            "transaction_id": transaction_id,
            "rows_updated": updated,
            "excel_synced": False,
        }
    return confirm_merchant_group(
        conn,
        merchant_key,
        ai_category=ai_category,
        ai_sub_category=ai_sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
        scope=scope,
        replace_excel=replace_excel,
    )
