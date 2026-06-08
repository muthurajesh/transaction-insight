from __future__ import annotations

import re
import sqlite3
from typing import Any

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|TRUNCATE|REINDEX|VACUUM)\b",
    re.I,
)

_DEFAULT_MAX_ROWS = 500


def _normalize_sql(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise ValueError("SQL is required.")
    # Strip one trailing semicolon; reject multiple statements.
    if text.endswith(";"):
        text = text[:-1].strip()
    if ";" in text:
        raise ValueError("Only one SQL statement is allowed.")
    return text


def validate_readonly_sql(sql: str) -> str:
    text = _normalize_sql(sql)
    if not re.match(r"^(SELECT|WITH)\b", text, re.I):
        raise ValueError("Only SELECT queries are allowed (may start with WITH … SELECT).")
    if _FORBIDDEN.search(text):
        raise ValueError("Query contains a forbidden keyword (write/DDL operations).")
    return text


def execute_readonly_sql(
    conn: sqlite3.Connection,
    sql: str,
    *,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """
    Run a read-only SELECT against the web app SQLite database.
    Intended for local chat — wrong queries are acceptable; writes are not.
    """
    if max_rows < 1 or max_rows > 2000:
        raise ValueError("max_rows must be between 1 and 2000")

    safe_sql = validate_readonly_sql(sql)
    cur = conn.execute(safe_sql)
    if not cur.description:
        return {
            "sql": safe_sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
        }

    columns = [str(col[0]) for col in cur.description]
    fetched = cur.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    rows_raw = fetched[:max_rows]

    rows: list[dict[str, Any]] = []
    for row in rows_raw:
        item: dict[str, Any] = {}
        for idx, col in enumerate(columns):
            val = row[idx]
            if isinstance(val, float):
                item[col] = round(val, 2) if val == val else None
            else:
                item[col] = val
        rows.append(item)

    return {
        "sql": safe_sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
