from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from transaction_insight.config import default_lookup_path
from transaction_insight.core import (
    CUSTOM_RULES_COLUMNS,
    CUSTOM_RULES_SHEET,
    apply_custom_rules,
    compile_custom_rules_sheet,
    create_client,
    load_active_custom_rules,
    load_lookup_workbook,
    normalize_custom_rules_sheet,
    open_excel_workbook,
    resolve_provider_config,
)

_PIPELINE_COLS = [
    "Category",
    "Classification",
    "AI Category",
    "AI Sub-Category",
    "Type",
    "Flow Type",
]


def lookup_workbook_path() -> Path:
    return default_lookup_path()


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


def _load_custom_rules_sheet(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return normalize_custom_rules_sheet(None)
    lookups = load_lookup_workbook(path)
    return normalize_custom_rules_sheet(lookups.get(CUSTOM_RULES_SHEET))


def _persist_custom_rules_sheet(path: Path, sheet: pd.DataFrame) -> None:
    normalized = normalize_custom_rules_sheet(sheet)
    if path.is_file():
        sheets = _load_all_sheets(path)
    else:
        sheets = {}
    sheets[CUSTOM_RULES_SHEET] = normalized
    _save_workbook_sheets(path, sheets)


def list_custom_rules() -> dict[str, Any]:
    path = lookup_workbook_path()
    sheet = _load_custom_rules_sheet(path)
    rules: list[dict[str, Any]] = []
    for idx, row in sheet.iterrows():
        rule_text = str(row.get("Rule", "") or "").strip()
        if not rule_text:
            continue
        compiled = str(row.get("Compiled Rule", "") or "").strip()
        rules.append(
            {
                "index": int(idx),
                "rule": rule_text,
                "status": str(row.get("Status", "") or "").strip() or "Pending",
                "last_error": str(row.get("Last Error", "") or "").strip(),
                "updated_at": str(row.get("Updated At", "") or "").strip(),
                "compiled_preview": compiled[:120] + ("…" if len(compiled) > 120 else ""),
            }
        )
    return {
        "lookup_file": str(path),
        "lookup_file_exists": path.is_file(),
        "rules": rules,
    }


def add_custom_rule(rule_text: str) -> dict[str, Any]:
    text = (rule_text or "").strip()
    if not text:
        raise ValueError("Rule text is required.")

    path = lookup_workbook_path()
    sheet = _load_custom_rules_sheet(path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    new_row = {
        "Rule": text,
        "Status": "Pending",
        "Compiled Rule": "",
        "Last Error": "",
        "Updated At": now,
    }
    sheet = pd.concat([sheet, pd.DataFrame([new_row])], ignore_index=True)
    _persist_custom_rules_sheet(path, sheet)
    return {
        "lookup_file": str(path),
        "message": "Rule added with status Pending. Use Compile & apply to compile and run it.",
        **list_custom_rules(),
    }


def _transactions_dataframe(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT transaction_id, merchant_key, amount, budget_month,
               source_category, classification, flow_type,
               ai_category, ai_sub_category, expense_type,
               original_description, simple_description, user_description
        FROM transactions
        ORDER BY date ASC, id ASC
        """
    ).fetchall()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df["Generated Description"] = df["merchant_key"].fillna("").astype(str)
    df["Original Description"] = df["original_description"].fillna("").astype(str)
    df["Simple Description"] = df["simple_description"].fillna("").astype(str)
    df["User Description"] = df["user_description"].fillna("").astype(str)
    df["Amount_Numeric"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["Amount"] = df["Amount_Numeric"].map(lambda a: f"{a:.2f}")
    df["Budget Month"] = df["budget_month"].fillna("").astype(str)
    df["Category"] = df["source_category"].fillna("").astype(str)
    df["Classification"] = df["classification"].fillna("").astype(str)
    df["AI Category"] = df["ai_category"].fillna("").astype(str)
    df["AI Sub-Category"] = df["ai_sub_category"].fillna("").astype(str)
    df["Type"] = df["expense_type"].fillna("").astype(str)
    df["Flow Type"] = df["flow_type"].fillna("").astype(str)
    return df


def _row_changed(before: pd.Series, after: pd.Series) -> bool:
    for col in _PIPELINE_COLS:
        b = str(before.get(col, "") or "").strip()
        a = str(after.get(col, "") or "").strip()
        if b != a:
            return True
    return False


def _apply_rules_to_db(conn: sqlite3.Connection, rules: list[dict[str, Any]]) -> int:
    if not rules:
        return 0

    df = _transactions_dataframe(conn)
    if df.empty:
        return 0

    before = df[_PIPELINE_COLS].copy()
    apply_custom_rules(df, rules)

    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for idx in df.index:
        if not _row_changed(before.loc[idx], df.loc[idx]):
            continue
        conn.execute(
            """
            UPDATE transactions SET
                source_category = ?,
                classification = ?,
                flow_type = ?,
                ai_category = ?,
                ai_sub_category = ?,
                expense_type = ?,
                confidence = 1.0,
                label_status = 'confirmed',
                rationale = 'custom rule'
            WHERE transaction_id = ?
            """,
            (
                str(df.at[idx, "Category"] or ""),
                str(df.at[idx, "Classification"] or ""),
                str(df.at[idx, "Flow Type"] or "Expense"),
                str(df.at[idx, "AI Category"] or ""),
                str(df.at[idx, "AI Sub-Category"] or ""),
                str(df.at[idx, "Type"] or "Variable"),
                str(df.at[idx, "transaction_id"]),
            ),
        )
        updated += 1

    conn.commit()
    return updated


def compile_and_apply_custom_rules(conn: sqlite3.Connection) -> dict[str, Any]:
    path = lookup_workbook_path()
    if not path.is_file():
        raise FileNotFoundError(f"Lookup workbook not found: {path}")

    sheet = _load_custom_rules_sheet(path)
    if sheet.empty or sheet["Rule"].fillna("").astype(str).str.strip().eq("").all():
        return {
            "message": "No custom rules in workbook.",
            "rows_updated": 0,
            "rules_compiled": 0,
            "rules_active": 0,
            "compile_errors": [],
        }

    provider, base_url, model = resolve_provider_config(
        "auto", base_url_arg=None, model_arg=None, role="pipeline"
    )
    client = create_client(provider, base_url=base_url)
    use_json_mode = provider == "openai"

    pending_before = int(
        sheet["Status"].fillna("").astype(str).str.strip().str.lower().eq("pending").sum()
    )
    compiled_sheet = compile_custom_rules_sheet(
        sheet, client, model, use_json_mode=use_json_mode
    )
    _persist_custom_rules_sheet(path, compiled_sheet)

    compile_errors: list[dict[str, str]] = []
    for idx, row in compiled_sheet.iterrows():
        rule_text = str(row.get("Rule", "") or "").strip()
        if not rule_text:
            continue
        status = str(row.get("Status", "") or "").strip()
        err = str(row.get("Last Error", "") or "").strip()
        if status.lower() == "error" and err:
            compile_errors.append({"rule": rule_text[:200], "error": err})

    active = load_active_custom_rules(compiled_sheet)
    rows_updated = _apply_rules_to_db(conn, active)

    return {
        "lookup_file": str(path),
        "message": (
            f"Compiled {pending_before} pending rule(s); "
            f"{len(active)} active; updated {rows_updated} transaction(s)."
        ),
        "rules_compiled": pending_before,
        "rules_active": len(active),
        "rows_updated": rows_updated,
        "compile_errors": compile_errors,
    }
