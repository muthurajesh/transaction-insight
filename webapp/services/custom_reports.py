from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from webapp.agent.db_query import execute_readonly_sql, validate_readonly_sql

_PARAM_NAME = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")
_ALLOWED_PARAMS = frozenset({"month", "months", "limit", "category", "expense_view"})
_EXPENSE_VIEWS = frozenset({"cash", "core", "normalized"})

_DEFAULT_REPORT_CONFIG: dict[str, Any] = {
    "expense_view": "cash",
    "display": {"show_grand_total": True},
    "chart": {"enabled": False, "type": "bar"},
    "analysis_mode": None,
    "exclusions": {},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_report_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = json.loads(json.dumps(_DEFAULT_REPORT_CONFIG))
    if not raw:
        return out
    if raw.get("expense_view") in _EXPENSE_VIEWS:
        out["expense_view"] = raw["expense_view"]
    if isinstance(raw.get("display"), dict):
        out["display"].update(raw["display"])
    if isinstance(raw.get("chart"), dict):
        out["chart"].update(raw["chart"])
    if raw.get("analysis_mode"):
        out["analysis_mode"] = raw["analysis_mode"]
    if isinstance(raw.get("exclusions"), dict):
        out["exclusions"] = raw["exclusions"]
    return out


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
        elif k == "expense_view":
            text = str(value or "cash").strip().lower()
            if text not in _EXPENSE_VIEWS:
                raise ValueError("expense_view must be cash, core, or normalized")
            out[k] = text
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


def _next_version(conn: sqlite3.Connection, parent_report_id: str | None) -> int:
    if not parent_report_id:
        return 1
    row = conn.execute(
        """
        SELECT COALESCE(MAX(version), 0) AS max_v
        FROM custom_reports
        WHERE report_id = ? OR parent_report_id = ?
        """,
        (parent_report_id, parent_report_id),
    ).fetchone()
    return int(row["max_v"] if row else 0) + 1


def save_custom_report(
    conn: sqlite3.Connection,
    *,
    name: str,
    sql_template: str,
    description: str = "",
    original_question: str = "",
    report_prompt: str = "",
    report_config: dict[str, Any] | None = None,
    parameters: list[str] | None = None,
    report_id: str | None = None,
    parent_report_id: str | None = None,
    version: int | None = None,
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

    parent = (parent_report_id or "").strip() or None
    if parent:
        if not get_custom_report(conn, parent):
            raise ValueError(f"Parent report not found: {parent}")

    rid = (report_id or "").strip() or uuid.uuid4().hex[:12]
    ver = version if version is not None else _next_version(conn, parent)
    config = _normalize_report_config(report_config)
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO custom_reports (
            report_id, name, description, sql_template, parameters_json,
            original_question, report_prompt, report_config_json,
            parent_report_id, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            sql_template = excluded.sql_template,
            parameters_json = excluded.parameters_json,
            original_question = excluded.original_question,
            report_prompt = excluded.report_prompt,
            report_config_json = excluded.report_config_json,
            parent_report_id = excluded.parent_report_id,
            version = excluded.version,
            updated_at = excluded.updated_at
        """,
        (
            rid,
            title,
            (description or "").strip(),
            safe_sql,
            json.dumps(param_list),
            (original_question or "").strip(),
            (report_prompt or "").strip(),
            json.dumps(config),
            parent,
            ver,
            now,
            now,
        ),
    )
    conn.commit()
    return get_custom_report(conn, rid) or {"report_id": rid, "name": title}


def update_custom_report(
    conn: sqlite3.Connection,
    report_id_or_name: str,
    *,
    name: str | None = None,
    description: str | None = None,
    sql_template: str | None = None,
    report_prompt: str | None = None,
    report_config: dict[str, Any] | None = None,
    original_question: str | None = None,
    parameters: list[str] | None = None,
) -> dict[str, Any]:
    existing = get_custom_report(conn, report_id_or_name)
    if not existing:
        raise ValueError(f"Custom report not found: {report_id_or_name!r}")

    fields: dict[str, Any] = {}
    if name is not None:
        title = name.strip()
        if not title:
            raise ValueError("name cannot be empty")
        fields["name"] = title
    if description is not None:
        fields["description"] = description.strip()
    if original_question is not None:
        fields["original_question"] = original_question.strip()
    if report_prompt is not None:
        fields["report_prompt"] = report_prompt.strip()
    if sql_template is not None:
        fields["sql_template"] = _validate_template_sql(sql_template)
        inferred = _extract_param_names(fields["sql_template"])
        if parameters is not None:
            param_list = [str(p).strip() for p in parameters if str(p).strip()]
            if set(param_list) != set(inferred):
                raise ValueError(
                    f"parameters {param_list} must match placeholders in SQL: {inferred}"
                )
            fields["parameters_json"] = json.dumps(param_list)
        else:
            fields["parameters_json"] = json.dumps(inferred)
    elif parameters is not None:
        param_list = [str(p).strip() for p in parameters if str(p).strip()]
        inferred = existing.get("parameters") or []
        if set(param_list) != set(inferred):
            raise ValueError(
                f"parameters {param_list} must match report SQL placeholders: {inferred}"
            )
        fields["parameters_json"] = json.dumps(param_list)

    if report_config is not None:
        merged = _normalize_report_config(existing.get("report_config"))
        merged.update(_normalize_report_config(report_config))
        fields["report_config_json"] = json.dumps(merged)

    if not fields:
        return existing

    fields["updated_at"] = _utc_now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE custom_reports SET {sets} WHERE report_id = ?",
        (*fields.values(), existing["report_id"]),
    )
    conn.commit()
    return get_custom_report(conn, existing["report_id"]) or existing


def fork_custom_report(
    conn: sqlite3.Connection,
    report_id_or_name: str,
    *,
    new_name: str,
    description: str | None = None,
    sql_template: str | None = None,
    report_prompt: str | None = None,
    report_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parent = get_custom_report(conn, report_id_or_name)
    if not parent:
        raise ValueError(f"Custom report not found: {report_id_or_name!r}")
    return save_custom_report(
        conn,
        name=new_name,
        sql_template=sql_template or parent["sql_template"],
        description=description if description is not None else parent.get("description", ""),
        original_question=parent.get("original_question", ""),
        report_prompt=report_prompt if report_prompt is not None else parent.get("report_prompt", ""),
        report_config=report_config if report_config is not None else parent.get("report_config"),
        parameters=parent.get("parameters"),
        parent_report_id=parent["report_id"],
    )


def finalize_report_from_conversation(
    *,
    sql_template: str,
    conversation_summary: str = "",
    original_question: str = "",
    tool_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Distill a saved report prompt + config from chat context and validated SQL.
    """
    from webapp.services.llm import chat_completion, extract_json

    safe_sql = _validate_template_sql(sql_template)
    trace_lines: list[str] = []
    for entry in tool_trace or []:
        tool = entry.get("tool", "")
        args = entry.get("args") or {}
        if tool == "query_sql" and args.get("sql"):
            trace_lines.append(f"query_sql: {args['sql'][:800]}")
        elif tool == "run_custom_report":
            trace_lines.append(
                f"run_custom_report: {args.get('report')} params={args.get('params')}"
            )

    user_content = "\n".join(
        [
            "Distill a reusable custom finance report from this chat session.",
            "",
            f"User goal / conversation:\n{conversation_summary or original_question or '(not provided)'}",
            "",
            f"Validated SQL template:\n{safe_sql}",
            "",
            "Tool trace:",
            "\n".join(trace_lines) if trace_lines else "(none)",
            "",
            "Return ONLY JSON:",
            "{",
            '  "report_prompt": "Clear instructions for rerunning this report (filters, view, exclusions, output shape). 3-8 sentences.",',
            '  "description": "One-line summary for the report list.",',
            '  "report_config": {',
            '    "expense_view": "cash|core|normalized",',
            '    "display": {"show_grand_total": true},',
            '    "chart": {"enabled": false, "type": "bar"},',
            '    "analysis_mode": null,',
            '    "exclusions": {}',
            "  }",
            "}",
        ]
    )
    raw = chat_completion(
        [
            {
                "role": "system",
                "content": (
                    "You write reusable personal finance report specifications. "
                    "Be precise about filters, months, categories, business exclusions, "
                    "and normalized vs cash view when mentioned."
                ),
            },
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        caller="custom_reports.finalize",
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("LLM did not return a JSON object for report finalize")

    report_prompt = str(parsed.get("report_prompt") or "").strip()
    if not report_prompt:
        report_prompt = (
            conversation_summary.strip()
            or original_question.strip()
            or description_fallback_from_sql(safe_sql)
        )

    description = str(parsed.get("description") or "").strip()
    if not description:
        description = report_prompt.split(".")[0][:120]

    config = _normalize_report_config(
        parsed.get("report_config") if isinstance(parsed.get("report_config"), dict) else None
    )
    return {
        "report_prompt": report_prompt,
        "description": description,
        "report_config": config,
        "sql_template": safe_sql,
        "parameters": _extract_param_names(safe_sql),
    }


def description_fallback_from_sql(sql: str) -> str:
    lowered = sql.lower()
    if "group by" in lowered and "ai_category" in lowered:
        return "Spending grouped by AI category"
    if "merchant_key" in lowered:
        return "Transaction list by merchant"
    return "Custom transaction report"


def list_custom_reports(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT report_id, name, description, sql_template, parameters_json,
               original_question, report_prompt, report_config_json,
               parent_report_id, version, created_at, updated_at
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
               original_question, report_prompt, report_config_json,
               parent_report_id, version, created_at, updated_at
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
        "report_prompt": report.get("report_prompt", ""),
        "report_config": report.get("report_config") or {},
        "parameters_used": bound,
        "sql": safe_sql,
        "columns": columns,
        "rows": result_rows,
        "row_count": len(result_rows),
        "truncated": truncated,
    }


def extract_sql_from_tool_trace(tool_trace: list[dict[str, Any]] | None) -> str | None:
    if not tool_trace:
        return None
    for entry in reversed(tool_trace):
        if entry.get("tool") == "query_sql":
            args = entry.get("args") or {}
            sql = str(args.get("sql") or "").strip()
            result = entry.get("result") or {}
            if sql and not result.get("error") and not result.get("validation_rejected"):
                try:
                    return _validate_template_sql(sql)
                except ValueError:
                    continue
        if entry.get("tool") == "run_custom_report":
            result = entry.get("result") or {}
            sql = str(result.get("sql") or "").strip()
            if sql:
                try:
                    return _validate_template_sql(sql)
                except ValueError:
                    continue
    return None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    params_raw = row["parameters_json"] or "[]"
    try:
        parameters = json.loads(params_raw)
    except json.JSONDecodeError:
        parameters = []
    config_raw = row["report_config_json"] if "report_config_json" in row.keys() else "{}"
    try:
        report_config = _normalize_report_config(json.loads(config_raw or "{}"))
    except json.JSONDecodeError:
        report_config = _normalize_report_config(None)
    return {
        "report_id": row["report_id"],
        "name": row["name"],
        "description": row["description"] or "",
        "sql_template": row["sql_template"],
        "parameters": parameters,
        "original_question": row["original_question"] or "",
        "report_prompt": (row["report_prompt"] or "") if "report_prompt" in row.keys() else "",
        "report_config": report_config,
        "parent_report_id": (row["parent_report_id"] or "") if "parent_report_id" in row.keys() else "",
        "version": int(row["version"] or 1) if "version" in row.keys() else 1,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
