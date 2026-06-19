from __future__ import annotations

import re
import sqlite3
from typing import Any

# Write / DDL / transaction control — blocked in chat SQL.
# Note: do not block bare END — it closes CASE … END expressions in SELECTs.
_FORBIDDEN = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|REPLACE|MERGE|UPSERT|"
    r"DROP|ALTER|CREATE|TRUNCATE|REINDEX|VACUUM|"
    r"ATTACH|DETACH|PRAGMA|"
    r"GRANT|REVOKE|"
    r"BEGIN|COMMIT|ROLLBACK|SAVEPOINT|"
    r"ANALYZE|LOAD_EXTENSION"
    r")\b",
    re.I,
)
_END_TRANSACTION = re.compile(r"\bEND\s+TRANSACTION\b", re.I)

# Chat may read finance data only — not chat logs, ingest metadata, or sqlite internals.
_CHAT_ALLOWED_TABLES = frozenset(
    {
        "transactions",
        "merchant_labels",
        "cadence_rules",
        "custom_reports",
    }
)

_FROM_JOIN_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
_CTE_NAME = re.compile(r"\b(?:WITH|,)\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\b", re.I)

_DEFAULT_MAX_ROWS = 500


def _normalize_sql(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise ValueError("SQL is required.")
    if text.endswith(";"):
        text = text[:-1].strip()
    if ";" in text:
        raise ValueError("Only one SQL statement is allowed.")
    return text


def _strip_string_literals(sql: str) -> str:
    """Remove string literals so keyword scans do not match inside quotes."""
    text = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return re.sub(r'"(?:\"\"|[^"])*"', '""', text)


def _referenced_tables(sql: str) -> set[str]:
    """Physical table names referenced via FROM / JOIN (CTE aliases excluded)."""
    bare = _strip_string_literals(sql)
    cte_names = {m.lower() for m in _CTE_NAME.findall(bare)}
    tables = {m.lower() for m in _FROM_JOIN_TABLE.findall(bare)}
    return tables - cte_names


def _validate_allowed_tables(sql: str) -> None:
    for table in _referenced_tables(sql):
        if table.startswith("sqlite_"):
            raise ValueError(f"System table not allowed in chat queries: {table}")
        if table not in _CHAT_ALLOWED_TABLES:
            raise ValueError(
                f"Table not allowed for chat queries: {table}. "
                f"Allowed: {', '.join(sorted(_CHAT_ALLOWED_TABLES))}"
            )


def validate_readonly_sql(sql: str, *, enforce_table_allowlist: bool = True) -> str:
    """
    Validate SQL for chat / report execution.
    Only single-statement SELECT (optionally WITH … SELECT). No writes or DDL.
    """
    text = _normalize_sql(sql)
    if not re.match(r"^(SELECT|WITH)\b", text, re.I):
        raise ValueError("Only SELECT queries are allowed (may start with WITH … SELECT).")
    bare = _strip_string_literals(text)
    if _FORBIDDEN.search(bare) or _END_TRANSACTION.search(bare):
        raise ValueError("Query contains a forbidden keyword (write/DDL/transaction control).")
    if enforce_table_allowlist:
        _validate_allowed_tables(text)
    return text


def execute_readonly_sql(
    conn: sqlite3.Connection,
    sql: str,
    *,
    max_rows: int = _DEFAULT_MAX_ROWS,
    enforce_table_allowlist: bool = True,
) -> dict[str, Any]:
    """
    Run a read-only SELECT against the web app SQLite database.
    Chat must never mutate data through this path.
    """
    if max_rows < 1 or max_rows > 2000:
        raise ValueError("max_rows must be between 1 and 2000")

    safe_sql = validate_readonly_sql(sql, enforce_table_allowlist=enforce_table_allowlist)
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
