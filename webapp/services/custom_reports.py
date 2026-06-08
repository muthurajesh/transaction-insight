from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from webapp.agent.db_query import execute_readonly_sql, validate_readonly_sql

_PARAM_NAME = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")
_ALLOWED_PARAMS = frozenset({"month", "months", "limit", "category"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_month(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", text):
        raise ValueError(f"Invalid month (expected YYYY-MM): {value!r}")
    return text


def _validate_limit(value: Any) -> int:
    n = int(value)
    if n < 1 or n > 2000:
        raise ValueError("limit must be between 1 and 2000")
    return n


def _validate_category(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        raise ValueError("category must be a non-empty string (max 120 chars)")
    return text


def _validate_months(value: Any) -> str:
    """JSON array string for sqlite json_each(:months)."""
    if isinstance(value, list):
        months = [_validate_month(m) for m in value]
        return json.dumps(months)
    text = str(value or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("months must be a JSON array of YYYY-MM strings") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("months must be a non-empty JSON array")
    months = [_validate_month(m) for m in parsed]
    return json.dumps(months)


def _coerce_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        k = str(key).strip()
        if k not in _ALLOWED_PARAMS:
            raise ValueError(f"Unsupported parameter: {k}")
        if k == "month":
            out[k] = _validate_month(value)
        elif k == "months":
            out[k] = _validate_months(value)
        elif k == "limit":
            out[k] = _validate_limit(value)
        elif k == "category":
            out[k] = _validate_category(value)
    return out


def _extract_param_names(sql: str) -> list[str]:
    names = _PARAM_NAME.findall(sql)
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _validate_template_sql(sql: str) -> str:
    safe = validate_readonly_sql(sql)
    for name in _extract_param_names(safe):
        if name not in _ALLOWED_PARAMS:
            raise ValueError(
                f"Unsupported SQL parameter :{name}. Allowed: {', '.join(sorted(_ALLOWED_PARAMS))}"
            )
    return safe


def save_custom_report(
    conn: sqlite3.Connection,
    *,
    name: str,
    sql_template: str,
    description: str = "",
    original_question: str = "",
    parameters: list[str] | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    title = (name or "").strip()
    if not title:
        raise ValueError("name is required")

    safe_sql = _validate_template_sql(sql_template)
    inferred = _extract_param_names(safe_sql)
    if parameters:
        param_list = [str(p).strip() for p in parameters if str(p).strip()]
        unknown = [p for p in param_list if p not in _ALLOWED_PARAMS]
        if unknown:
            raise ValueError(f"Unknown parameters: {unknown}")
        if set(param_list) != set(inferred):
            raise ValueError(
                f"parameters {param_list} must match placeholders in SQL: {inferred}"
            )
    else:
        param_list = inferred

    rid = (report_id or "").strip() or uuid.uuid4().hex[:12]
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO custom_reports (
            report_id, name, description, sql_template, parameters_json,
            original_question, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            sql_template = excluded.sql_template,
            parameters_json = excluded.parameters_json,
            original_question = excluded.original_question,
            updated_at = excluded.updated_at
        """,
        (
            rid,
            title,
            (description or "").strip(),
            safe_sql,
            json.dumps(param_list),
            (original_question or "").strip(),
            now,
            now,
        ),
    )
    conn.commit()
    return get_custom_report(conn, rid) or {"report_id": rid, "name": title}


def list_custom_reports(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT report_id, name, description, sql_template, parameters_json,
               original_question, created_at, updated_at
        FROM custom_reports
        ORDER BY updated_at DESC, name ASC
        """
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_custom_report(
    conn: sqlite3.Connection, report_id_or_name: str
) -> dict[str, Any] | None:
    key = (report_id_or_name or "").strip()
    if not key:
        return None
    row = conn.execute(
        """
        SELECT report_id, name, description, sql_template, parameters_json,
               original_question, created_at, updated_at
        FROM custom_reports
        WHERE report_id = ? OR LOWER(name) = LOWER(?)
        LIMIT 1
        """,
        (key, key),
    ).fetchone()
    return _row_to_dict(row) if row else None


def delete_custom_report(conn: sqlite3.Connection, report_id_or_name: str) -> bool:
    key = (report_id_or_name or "").strip()
    if not key:
        raise ValueError("report_id or name is required")
    cur = conn.execute(
        """
        DELETE FROM custom_reports
        WHERE report_id = ? OR LOWER(name) = LOWER(?)
        """,
        (key, key),
    )
    conn.commit()
    return cur.rowcount > 0


def run_custom_report(
    conn: sqlite3.Connection,
    report_id_or_name: str,
    *,
    params: dict[str, Any] | None = None,
    max_rows: int = 500,
) -> dict[str, Any]:
    report = get_custom_report(conn, report_id_or_name)
    if not report:
        raise ValueError(f"Custom report not found: {report_id_or_name!r}")

    bound = _coerce_params(params)
    required = report.get("parameters") or []
    missing = [p for p in required if p not in bound]
    if missing:
        raise ValueError(f"Missing parameters for report '{report['name']}': {missing}")

    safe_sql = _validate_template_sql(report["sql_template"])
    if max_rows < 1 or max_rows > 2000:
        raise ValueError("max_rows must be between 1 and 2000")

    cur = conn.execute(safe_sql, bound)
    if not cur.description:
        result_rows: list[dict[str, Any]] = []
        columns: list[str] = []
        truncated = False
    else:
        columns = [str(col[0]) for col in cur.description]
        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows_raw = fetched[:max_rows]
        result_rows = []
        for row in rows_raw:
            item: dict[str, Any] = {}
            for idx, col in enumerate(columns):
                val = row[idx]
                if isinstance(val, float):
                    item[col] = round(val, 2) if val == val else None
                else:
                    item[col] = val
            result_rows.append(item)

    return {
        "report_id": report["report_id"],
        "name": report["name"],
        "description": report.get("description", ""),
        "parameters_used": bound,
        "sql": safe_sql,
        "columns": columns,
        "rows": result_rows,
        "row_count": len(result_rows),
        "truncated": truncated,
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    params_raw = row["parameters_json"] or "[]"
    try:
        parameters = json.loads(params_raw)
    except json.JSONDecodeError:
        parameters = []
    return {
        "report_id": row["report_id"],
        "name": row["name"],
        "description": row["description"] or "",
        "sql_template": row["sql_template"],
        "parameters": parameters,
        "original_question": row["original_question"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
