from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from webapp.adapters.lookup_store import load_lookup_workbook_from_db
from webapp.processing import (
    CUSTOM_RULES_COLUMNS,
    CUSTOM_RULES_SHEET,
    CUSTOM_RULE_FIELD_MAP,
    CUSTOM_RULE_STATUS_ACTIVE,
    CUSTOM_RULE_STATUS_DISABLED,
    CUSTOM_RULE_STATUS_ERROR,
    CUSTOM_RULE_STATUS_PENDING,
    apply_custom_rules,
    compile_custom_rule_text,
    compile_custom_rules_sheet,
    create_client,
    load_active_custom_rules,
    normalize_custom_rules_sheet,
    preview_rule_affected,
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

_FLOW_TYPES = frozenset({"Expense", "Income", "Transfer", "Adjustment"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_display() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_compiled(raw: str | None) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fetch_rule_row(conn: sqlite3.Connection, rule_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, rule_text, status, compiled_rule, last_error, updated_at
        FROM pipeline_custom_rules
        WHERE id = ?
        """,
        (rule_id,),
    ).fetchone()


def _rule_row_to_api(row: sqlite3.Row, *, include_compiled: bool = True) -> dict[str, Any]:
    compiled_raw = str(row["compiled_rule"] or "").strip()
    compiled_obj = _parse_compiled(compiled_raw) if include_compiled else None
    return {
        "id": int(row["id"]),
        "index": int(row["id"]),
        "rule": str(row["rule_text"] or "").strip(),
        "status": str(row["status"] or "").strip() or CUSTOM_RULE_STATUS_PENDING,
        "last_error": str(row["last_error"] or "").strip(),
        "updated_at": str(row["updated_at"] or "").strip(),
        "compiled_rule": compiled_obj,
        "compiled_preview": compiled_raw[:120] + ("…" if len(compiled_raw) > 120 else ""),
    }


def _load_custom_rules_sheet(conn: sqlite3.Connection) -> pd.DataFrame:
    lookups = load_lookup_workbook_from_db(conn)
    return normalize_custom_rules_sheet(lookups.get(CUSTOM_RULES_SHEET))


def _sync_compiled_sheet_to_db(conn: sqlite3.Connection, sheet: pd.DataFrame) -> None:
    """Update pipeline_custom_rules rows by row order (ids ascending)."""
    normalized = normalize_custom_rules_sheet(sheet)
    ids = [
        int(r["id"])
        for r in conn.execute(
            "SELECT id FROM pipeline_custom_rules ORDER BY id"
        ).fetchall()
    ]
    now = _utc_now_iso()
    row_idx = 0
    for _, row in normalized.iterrows():
        rule_text = str(row.get("Rule", "") or "").strip()
        if not rule_text:
            continue
        if row_idx >= len(ids):
            conn.execute(
                """
                INSERT INTO pipeline_custom_rules (
                    rule_text, status, compiled_rule, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rule_text,
                    str(row.get("Status", "") or CUSTOM_RULE_STATUS_PENDING),
                    str(row.get("Compiled Rule", "") or ""),
                    str(row.get("Last Error", "") or ""),
                    str(row.get("Updated At", "") or now),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE pipeline_custom_rules SET
                    rule_text = ?,
                    status = ?,
                    compiled_rule = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    rule_text,
                    str(row.get("Status", "") or CUSTOM_RULE_STATUS_PENDING),
                    str(row.get("Compiled Rule", "") or ""),
                    str(row.get("Last Error", "") or ""),
                    str(row.get("Updated At", "") or now),
                    ids[row_idx],
                ),
            )
        row_idx += 1
    if row_idx < len(ids):
        for rid in ids[row_idx:]:
            conn.execute("DELETE FROM pipeline_custom_rules WHERE id = ?", (rid,))
    conn.commit()


def _resolve_llm_client() -> tuple[Any, str, str, bool]:
    provider, base_url, model = resolve_provider_config(
        "auto", base_url_arg=None, model_arg=None, role="pipeline"
    )
    client = create_client(provider, base_url=base_url)
    use_json_mode = provider in ("openai", "lmstudio", "ollama")
    return client, model, provider, use_json_mode


def _spec_to_proposed(spec: dict[str, Any]) -> dict[str, str]:
    """Map compiled set/when_* keys to API field names."""
    out: dict[str, str] = {}
    for key, val in (spec or {}).items():
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        text = str(val).strip()
        if not text or text.lower() == "nan":
            continue
        col = CUSTOM_RULE_FIELD_MAP.get(str(key).strip().lower(), str(key))
        if col == "AI Category":
            out["ai_category"] = text
        elif col == "AI Sub-Category":
            out["ai_sub_category"] = text
        elif col == "Type":
            out["expense_type"] = text
        elif col == "Classification":
            out["classification"] = text
        elif col == "Flow Type":
            out["flow_type"] = text
        elif col == "Category":
            out["source_category"] = text
        elif col == "Budget Tier":
            out["budget_tier"] = text
        elif col == "Sub-Type":
            out["sub_type"] = text
    if out.get("flow_type") and out["flow_type"] not in _FLOW_TYPES:
        del out["flow_type"]
    return out


def _merge_proposed(current: dict[str, str], proposed: dict[str, str]) -> dict[str, str]:
    """Full label row after apply (only overridden keys change)."""
    merged = dict(current)
    for key, val in proposed.items():
        if val:
            merged[key] = val
    return merged


def list_custom_rules(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, rule_text, status, compiled_rule, last_error, updated_at
        FROM pipeline_custom_rules
        ORDER BY id
        """
    ).fetchall()
    return {"rules": [_rule_row_to_api(r) for r in rows]}


def get_custom_rule(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any]:
    row = _fetch_rule_row(conn, rule_id)
    if row is None:
        raise ValueError("Rule not found")
    return _rule_row_to_api(row)


def add_custom_rule(conn: sqlite3.Connection, rule_text: str) -> dict[str, Any]:
    text = (rule_text or "").strip()
    if not text:
        raise ValueError("Rule text is required.")
    now = _utc_now_iso()
    conn.execute(
        """
        INSERT INTO pipeline_custom_rules (
            rule_text, status, compiled_rule, last_error, updated_at
        ) VALUES (?, ?, '', '', ?)
        """,
        (text, CUSTOM_RULE_STATUS_PENDING, now),
    )
    conn.commit()
    return {
        "message": "Rule added with status Pending. Run preview or Save & apply to compile.",
        **list_custom_rules(conn),
    }


def update_custom_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    *,
    rule_text: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    row = _fetch_rule_row(conn, rule_id)
    if row is None:
        raise ValueError("Rule not found")

    new_text = (rule_text if rule_text is not None else str(row["rule_text"] or "")).strip()
    if not new_text:
        raise ValueError("Rule text is required.")

    new_status = (status or str(row["status"] or "")).strip() or CUSTOM_RULE_STATUS_PENDING
    allowed = {
        CUSTOM_RULE_STATUS_PENDING,
        CUSTOM_RULE_STATUS_ACTIVE,
        CUSTOM_RULE_STATUS_ERROR,
        CUSTOM_RULE_STATUS_DISABLED,
    }
    if new_status not in allowed:
        raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")

    text_changed = new_text != str(row["rule_text"] or "").strip()
    compiled = str(row["compiled_rule"] or "")
    last_error = str(row["last_error"] or "")
    if text_changed:
        new_status = CUSTOM_RULE_STATUS_PENDING
        compiled = ""
        last_error = ""

    conn.execute(
        """
        UPDATE pipeline_custom_rules SET
            rule_text = ?,
            status = ?,
            compiled_rule = ?,
            last_error = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (new_text, new_status, compiled, last_error, _utc_now_iso(), rule_id),
    )
    conn.commit()
    return {"message": "Rule updated.", **list_custom_rules(conn)}


def delete_custom_rule(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any]:
    cur = conn.execute("DELETE FROM pipeline_custom_rules WHERE id = ?", (rule_id,))
    if cur.rowcount == 0:
        raise ValueError("Rule not found")
    conn.commit()
    return {"message": "Rule deleted.", **list_custom_rules(conn)}


CUSTOM_RULES_EXPORT_FORMAT = "transaction-insight.custom-rules"
CUSTOM_RULES_EXPORT_VERSION = 1
_IMPORT_STATUSES = frozenset(
    {
        CUSTOM_RULE_STATUS_PENDING,
        CUSTOM_RULE_STATUS_ACTIVE,
        CUSTOM_RULE_STATUS_DISABLED,
        CUSTOM_RULE_STATUS_ERROR,
    }
)


def export_custom_rules(conn: sqlite3.Connection) -> dict[str, Any]:
    """Backup custom rules as a portable JSON document (no DB ids)."""
    rules_out: list[dict[str, Any]] = []
    for row in list_custom_rules(conn).get("rules") or []:
        entry: dict[str, Any] = {
            "rule": str(row.get("rule") or "").strip(),
            "status": str(row.get("status") or CUSTOM_RULE_STATUS_PENDING).strip(),
        }
        compiled = row.get("compiled_rule")
        if isinstance(compiled, dict) and compiled:
            entry["compiled_rule"] = compiled
        err = str(row.get("last_error") or "").strip()
        if err:
            entry["last_error"] = err
        if entry["rule"]:
            rules_out.append(entry)
    return {
        "format": CUSTOM_RULES_EXPORT_FORMAT,
        "version": CUSTOM_RULES_EXPORT_VERSION,
        "exported_at": _utc_now_iso(),
        "rules": rules_out,
    }


def _normalize_import_payload(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        fmt = str(payload.get("format") or "").strip()
        if fmt and fmt != CUSTOM_RULES_EXPORT_FORMAT:
            raise ValueError(f"Unsupported export format: {fmt}")
        version = payload.get("version")
        if version is not None and int(version) != CUSTOM_RULES_EXPORT_VERSION:
            raise ValueError(f"Unsupported export version: {version}")
        items = payload.get("rules")
        if items is None:
            raise ValueError("Import payload must include a rules array.")
    else:
        raise ValueError("Import payload must be a JSON object or array.")

    if not isinstance(items, list):
        raise ValueError("rules must be an array.")

    normalized: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            normalized.append({"rule": text, "status": CUSTOM_RULE_STATUS_PENDING})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"rules[{i}] must be an object or string.")
        text = str(item.get("rule") or item.get("rule_text") or "").strip()
        if not text:
            continue
        status = str(item.get("status") or CUSTOM_RULE_STATUS_PENDING).strip()
        if status not in _IMPORT_STATUSES:
            status = CUSTOM_RULE_STATUS_PENDING
        compiled = item.get("compiled_rule")
        if compiled is not None and not isinstance(compiled, dict):
            raise ValueError(f"rules[{i}].compiled_rule must be an object when present.")
        last_error = str(item.get("last_error") or "").strip()
        normalized.append(
            {
                "rule": text,
                "status": status,
                "compiled_rule": compiled if isinstance(compiled, dict) else None,
                "last_error": last_error,
            }
        )
    return normalized


def _insert_imported_rule(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    text = entry["rule"]
    status = entry["status"]
    compiled_obj = entry.get("compiled_rule")
    last_error = str(entry.get("last_error") or "")

    compiled_raw = ""
    if isinstance(compiled_obj, dict) and compiled_obj:
        compiled_raw = json.dumps(compiled_obj, ensure_ascii=False)

    if status == CUSTOM_RULE_STATUS_ACTIVE and not compiled_raw:
        status = CUSTOM_RULE_STATUS_PENDING
    if status == CUSTOM_RULE_STATUS_ERROR:
        status = CUSTOM_RULE_STATUS_PENDING
        last_error = ""
    if status == CUSTOM_RULE_STATUS_PENDING:
        last_error = ""

    conn.execute(
        """
        INSERT INTO pipeline_custom_rules (
            rule_text, status, compiled_rule, last_error, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (text, status, compiled_raw, last_error, _utc_now_iso()),
    )


def import_custom_rules(
    conn: sqlite3.Connection,
    payload: dict[str, Any] | list[Any],
    *,
    mode: str = "merge",
) -> dict[str, Any]:
    """Restore custom rules from an export document.

    mode=merge — skip rule texts that already exist.
    mode=replace — delete all existing rules, then import.
    """
    mode_norm = (mode or "merge").strip().lower()
    if mode_norm not in {"merge", "replace"}:
        raise ValueError("mode must be merge or replace")

    entries = _normalize_import_payload(payload)
    if not entries:
        raise ValueError("No rules found in import payload.")

    existing_texts: set[str] = set()
    if mode_norm == "replace":
        conn.execute("DELETE FROM pipeline_custom_rules")
    else:
        for row in conn.execute("SELECT rule_text FROM pipeline_custom_rules").fetchall():
            existing_texts.add(str(row["rule_text"] or "").strip())

    imported = 0
    skipped = 0
    for entry in entries:
        text = entry["rule"]
        if mode_norm == "merge" and text in existing_texts:
            skipped += 1
            continue
        _insert_imported_rule(conn, entry)
        existing_texts.add(text)
        imported += 1

    conn.commit()
    return {
        "message": (
            f"Imported {imported} rule(s)"
            + (f", skipped {skipped} duplicate(s)" if skipped else "")
            + (f" (replaced existing rules)" if mode_norm == "replace" else "")
            + "."
        ),
        "imported": imported,
        "skipped": skipped,
        "mode": mode_norm,
        **list_custom_rules(conn),
    }


def _transactions_dataframe(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT transaction_id, date, merchant_key, amount, budget_month,
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


def _current_labels_from_df_row(row: pd.Series) -> dict[str, str]:
    return {
        "source_category": str(row.get("Category", "") or "").strip(),
        "ai_category": str(row.get("AI Category", "") or "").strip(),
        "ai_sub_category": str(row.get("AI Sub-Category", "") or "").strip(),
        "expense_type": str(row.get("Type", "") or "").strip(),
        "classification": str(row.get("Classification", "") or "").strip(),
        "flow_type": str(row.get("Flow Type", "") or "").strip() or "Expense",
    }


def _compile_rule_ephemeral(rule_text: str) -> tuple[dict[str, Any] | None, str]:
    client, model, _provider, use_json_mode = _resolve_llm_client()
    return compile_custom_rule_text(client, rule_text, model, use_json_mode=use_json_mode)


def _resolve_compiled_for_preview(
    conn: sqlite3.Connection,
    *,
    rule_text: str | None,
    rule_id: int | None,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (compiled_rule, compile_error, source_rule_text)."""
    if rule_text and rule_text.strip():
        compiled, err = _compile_rule_ephemeral(rule_text.strip())
        return compiled, err, rule_text.strip()

    if rule_id is None:
        return None, "rule_text or rule_id is required", None

    row = _fetch_rule_row(conn, rule_id)
    if row is None:
        return None, "Rule not found", None

    text = str(row["rule_text"] or "").strip()
    status = str(row["status"] or "").strip().lower()
    stored = _parse_compiled(str(row["compiled_rule"] or ""))
    if stored and status == CUSTOM_RULE_STATUS_ACTIVE.lower():
        return stored, "", text

    compiled, err = _compile_rule_ephemeral(text)
    return compiled, err, text


def preview_custom_rule(
    conn: sqlite3.Connection,
    *,
    rule_text: str | None = None,
    rule_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    compiled, compile_error, _ = _resolve_compiled_for_preview(
        conn, rule_text=rule_text, rule_id=rule_id
    )
    if compile_error:
        return {
            "compile_error": compile_error,
            "compiled_rule": compiled,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "transactions": [],
        }
    if compiled is None or not compiled.get("rule_type"):
        return {
            "compile_error": compile_error or "Could not compile rule",
            "compiled_rule": None,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "transactions": [],
        }

    df = _transactions_dataframe(conn)
    if df.empty:
        return {
            "compile_error": None,
            "compiled_rule": compiled,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "transactions": [],
        }

    affected = preview_rule_affected(df, compiled)
    total = len(affected)
    page = affected[offset : offset + limit]
    transactions: list[dict[str, Any]] = []
    for idx, spec in page:
        row = df.loc[idx]
        current = _current_labels_from_df_row(row)
        proposed_delta = _spec_to_proposed(spec)
        proposed = _merge_proposed(current, proposed_delta)
        transactions.append(
            {
                "transaction_id": str(row.get("transaction_id", "")),
                "date": str(row.get("date", "") or ""),
                "amount": float(row.get("Amount_Numeric", 0) or 0),
                "merchant_key": str(row.get("merchant_key", "") or ""),
                "simple_description": str(row.get("Simple Description", "") or ""),
                "source_category": current["source_category"],
                "ai_category": current["ai_category"],
                "ai_sub_category": current["ai_sub_category"],
                "expense_type": current["expense_type"],
                "classification": current["classification"],
                "flow_type": current["flow_type"],
                "proposed": proposed,
            }
        )

    return {
        "compile_error": None,
        "compiled_rule": compiled,
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": transactions,
    }


def _row_changed(before: pd.Series, after: pd.Series) -> bool:
    for col in _PIPELINE_COLS:
        b = str(before.get(col, "") or "").strip()
        a = str(after.get(col, "") or "").strip()
        if b != a:
            return True
    return False


def _indices_matched_by_rules(df: pd.DataFrame, rules: list[dict[str, Any]]) -> set[int]:
    matched: set[int] = set()
    for rule in rules:
        for idx, _spec in preview_rule_affected(df, rule):
            matched.add(idx)
    return matched


def _apply_rules_to_db(conn: sqlite3.Connection, rules: list[dict[str, Any]]) -> int:
    if not rules:
        return 0

    df = _transactions_dataframe(conn)
    if df.empty:
        return 0

    before = df[_PIPELINE_COLS].copy()
    matched_indices = _indices_matched_by_rules(df, rules)
    apply_custom_rules(df, rules)

    updated = 0
    for idx in df.index:
        if idx not in matched_indices and not _row_changed(before.loc[idx], df.loc[idx]):
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


def _compile_and_store_rule(conn: sqlite3.Connection, rule_id: int) -> tuple[dict[str, Any] | None, str]:
    row = _fetch_rule_row(conn, rule_id)
    if row is None:
        return None, "Rule not found"
    text = str(row["rule_text"] or "").strip()
    if not text:
        return None, "Rule text is empty"

    compiled, err = _compile_rule_ephemeral(text)
    now = _utc_now_iso()
    if compiled is not None:
        conn.execute(
            """
            UPDATE pipeline_custom_rules SET
                compiled_rule = ?,
                status = ?,
                last_error = '',
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(compiled, separators=(",", ":")), CUSTOM_RULE_STATUS_ACTIVE, now, rule_id),
        )
        conn.commit()
        return compiled, ""
    conn.execute(
        """
        UPDATE pipeline_custom_rules SET
            status = ?,
            last_error = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (CUSTOM_RULE_STATUS_ERROR, err[:500], now, rule_id),
    )
    conn.commit()
    return None, err


def apply_custom_rule_by_id(conn: sqlite3.Connection, rule_id: int) -> dict[str, Any]:
    row = _fetch_rule_row(conn, rule_id)
    if row is None:
        raise ValueError("Rule not found")
    status = str(row["status"] or "").strip().lower()
    if status == CUSTOM_RULE_STATUS_DISABLED.lower():
        raise ValueError("Cannot apply a disabled rule")

    compiled = _parse_compiled(str(row["compiled_rule"] or ""))
    if status != CUSTOM_RULE_STATUS_ACTIVE.lower() or not compiled:
        compiled, err = _compile_and_store_rule(conn, rule_id)
        if compiled is None:
            return {
                "ok": False,
                "message": f"Could not compile rule: {err}",
                "rows_updated": 0,
                **list_custom_rules(conn),
            }

    rows_updated = _apply_rules_to_db(conn, [compiled])
    return {
        "ok": True,
        "message": f"Applied rule. Updated {rows_updated} transaction(s).",
        "rows_updated": rows_updated,
        **list_custom_rules(conn),
    }


def save_and_apply_custom_rule(conn: sqlite3.Connection, rule_text: str) -> dict[str, Any]:
    """Append a rule, compile it, and apply only that rule."""
    add_result = add_custom_rule(conn, rule_text)
    rules = add_result.get("rules") or []
    if not rules:
        raise ValueError("Failed to save rule")
    new_id = int(rules[-1]["id"])
    apply_result = apply_custom_rule_by_id(conn, new_id)
    apply_result["rules"] = apply_result.get("rules") or list_custom_rules(conn).get("rules") or []
    return apply_result


def compile_and_apply_custom_rules(conn: sqlite3.Connection) -> dict[str, Any]:
    sheet = _load_custom_rules_sheet(conn)
    if sheet.empty or sheet["Rule"].fillna("").astype(str).str.strip().eq("").all():
        return {
            "message": "No custom rules saved yet.",
            "rows_updated": 0,
            "rules_compiled": 0,
            "rules_active": 0,
            "compile_errors": [],
        }

    client, model, _provider, use_json_mode = _resolve_llm_client()

    pending_before = int(
        sheet["Status"].fillna("").astype(str).str.strip().str.lower().eq("pending").sum()
    )
    compiled_sheet = compile_custom_rules_sheet(
        sheet, client, model, use_json_mode=use_json_mode
    )
    _sync_compiled_sheet_to_db(conn, compiled_sheet)

    compile_errors: list[dict[str, str]] = []
    for _, row in compiled_sheet.iterrows():
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
        "message": (
            f"Compiled {pending_before} pending rule(s); "
            f"{len(active)} active; updated {rows_updated} transaction(s)."
        ),
        "rules_compiled": pending_before,
        "rules_active": len(active),
        "rows_updated": rows_updated,
        "compile_errors": compile_errors,
    }
