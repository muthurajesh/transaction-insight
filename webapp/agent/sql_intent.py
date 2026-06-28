"""Detect question intent and validate query_sql matches what the user asked."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

_MONTH_NAME_TO_NUM: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_COMPARE_WORDS = ("compare", " vs ", " versus ", " vs.", "between")
_DATA_WORDS = (
    "spend",
    "spending",
    "expense",
    "expenses",
    "category",
    "categories",
    "merchant",
    "average",
    "avg",
    "compare",
    "total",
    "how much",
    "top ",
    "income",
    "transaction",
    "month",
    "budget",
    "load ",
    "show ",
    "list ",
)


def extract_last_n_months(text: str) -> int | None:
    """Parse 'last 3 months' style phrases."""
    match = re.search(r"last\s+(\d{1,2})\s+(?:full\s+)?months?\b", (text or "").lower())
    if not match:
        return None
    n = int(match.group(1))
    return n if 1 <= n <= 24 else None


def resolve_budget_months_for_question(
    conn: sqlite3.Connection, user_message: str
) -> list[str]:
    """Map natural-language time range to budget_month values (newest first)."""
    explicit = extract_budget_months(user_message)
    if explicit:
        return explicit
    n = extract_last_n_months(user_message)
    if not n:
        return []
    from webapp.analytics.queries import available_months

    full = available_months(conn).get("full_months") or []
    return list(full[:n])


def message_asks_for_merchant_activity(user_message: str) -> bool:
    lower = (user_message or "").lower()
    if "transaction" in lower or "charges" in lower or "payments" in lower:
        return True
    return any(v in lower for v in ("load ", "show ", "list ", "get ", "find ", "from "))


def merchant_query_hints(conn: sqlite3.Connection, user_message: str) -> str:
    """Plain-English hints injected into chat context — not shown to the user directly."""
    from webapp.services.cadence_insights import find_merchant_key_from_text

    parts: list[str] = []
    mk = find_merchant_key_from_text(conn, user_message)
    if mk:
        parts.append(
            f'Query hint — user merchant/payee: match `merchant_key` '
            f'(exact `{mk}` or `LIKE` for related labels). '
            f'Never use `source_file` for bank or merchant names '
            f'(source_file is the CSV filename only, e.g. ExportData-April-2025.csv).'
        )
        related = conn.execute(
            """
            SELECT DISTINCT merchant_key FROM transactions
            WHERE merchant_key LIKE ? AND merchant_key != ?
            ORDER BY merchant_key
            LIMIT 6
            """,
            (f"%{mk.split()[0]}%", mk),
        ).fetchall()
        if related:
            labels = ", ".join(f'"{r[0]}"' for r in related if r[0])
            if labels:
                parts.append(f"Related merchant_key values: {labels}.")

    months = resolve_budget_months_for_question(conn, user_message)
    if months:
        quoted = ", ".join(f"'{m}'" for m in months)
        parts.append(
            f'Query hint — time range: `budget_month IN ({quoted})` '
            f'(newest full months for "last N months").'
        )

    if mk and message_asks_for_merchant_activity(user_message):
        lower = user_message.lower()
        if not any(w in lower for w in ("spend", "spending", "expense", "expenses")):
            parts.append(
                "Query hint — user asked to list/load activity, not spending only: "
                "include Transfers and other flow_type rows unless they said expenses/spending."
            )

    return "\n".join(parts)


def _sql_filters_source_file_like_merchant(sql: str) -> bool:
    bare = _strip_literals(sql.lower())
    return "source_file" in bare and "like" in bare


def validate_merchant_query_sql(
    user_message: str,
    sql: str,
    result: dict[str, Any],
    conn: sqlite3.Connection | None,
) -> str | None:
    if result.get("error"):
        return None

    from webapp.services.cadence_insights import find_merchant_key_from_text

    sql_lower = _strip_literals(sql.lower())
    mk = find_merchant_key_from_text(conn, user_message) if conn else None
    merchant_question = bool(mk) or message_asks_for_merchant_activity(user_message)

    if merchant_question and _sql_filters_source_file_like_merchant(sql):
        return (
            "Merchant or bank name must filter `merchant_key`, not `source_file`. "
            "`source_file` is only the CSV filename (e.g. ExportData-April-2025.csv) and "
            "does not contain merchant names. Re-run query_sql using merchant_key "
            f"(e.g. merchant_key LIKE '%{mk}%' or merchant_key = '{mk}')."
            if mk
            else (
                "Merchant or bank name must filter `merchant_key`, not `source_file`. "
                "Re-run query_sql using merchant_key LIKE with the payee name from the question."
            )
        )

    rows = result.get("rows") or []
    row_count = int(result.get("row_count") if result.get("row_count") is not None else len(rows))
    if row_count == 0 and conn and mk and merchant_question and "merchant_key" not in sql_lower:
        return (
            f'No rows — question is about "{mk}" but SQL did not filter merchant_key. '
            f"Re-run query_sql with merchant_key matching that payee."
        )

    return None


def extract_budget_months(text: str) -> list[str]:
    """Return YYYY-MM strings mentioned in the user message."""
    found = list(dict.fromkeys(re.findall(r"20\d{2}-\d{2}", text)))
    if len(found) >= 2:
        return sorted(found)

    lower = text.lower()
    year_match = re.search(r"20\d{2}", text)
    if not year_match:
        return found
    year = year_match.group(0)
    nums: list[int] = []
    for name, num in _MONTH_NAME_TO_NUM.items():
        if re.search(rf"\b{re.escape(name)}\b", lower) and num not in nums:
            nums.append(num)
    nums.sort()
    for num in nums:
        ym = f"{year}-{num:02d}"
        if ym not in found:
            found.append(ym)
    return sorted(found)


def detect_compare_intent(user_message: str) -> bool:
    lower = user_message.lower()
    if not any(w in lower for w in _COMPARE_WORDS):
        return False
    months = extract_budget_months(user_message)
    if len(months) >= 2:
        return True
    return any(w in lower for w in ("month", "categor", "spend", "expense"))


def needs_database_answer(user_message: str) -> bool:
    """True when the user expects numbers from transactions, not workflow UI."""
    lower = user_message.lower()
    if any(
        p in lower
        for p in (
            "cadence",
            "annual charge",
            "yearly insurance",
            "custom report",
            "saved report",
        )
    ):
        return False
    return any(w in lower for w in _DATA_WORDS)


def _strip_literals(sql: str) -> str:
    text = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return re.sub(r'"(?:\"\"|[^"])*"', '""', text)


def _group_by_clause(sql: str) -> str:
    bare = _strip_literals(sql.lower())
    match = re.search(r"\bgroup\s+by\s+(.+?)(?:\border\b|\blimit\b|\bhaving\b|$)", bare, re.S)
    return match.group(1).strip() if match else ""


def _compare_month_columns(columns: list[str], expected_months: list[str]) -> int:
    """Count columns that look like separate month buckets."""
    count = 0
    for col in columns:
        cl = col.lower()
        if cl in ("ai_category", "category", "merchant_key", "merchant"):
            continue
        if re.search(r"20\d{2}-\d{2}", cl):
            count += 1
            continue
        if any(m.replace("-", "_") in cl or m.replace("-", "") in cl for m in expected_months):
            count += 1
            continue
        if any(name in cl for name in _MONTH_NAME_TO_NUM):
            count += 1
    return count


def validate_query_sql(
    user_message: str,
    sql: str,
    result: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> str | None:
    """
    Return an error string when SQL/results do not match the question.
    None means validation passed.
    """
    merchant_err = validate_merchant_query_sql(user_message, sql, result, conn)
    if merchant_err:
        return merchant_err

    if result.get("error"):
        return None

    rows = result.get("rows") or []
    columns = [str(c) for c in (result.get("columns") or [])]
    if not detect_compare_intent(user_message):
        return None

    expected_months = extract_budget_months(user_message)
    sql_lower = _strip_literals(sql.lower())
    group_by = _group_by_clause(sql)

    if "budget_month" in [c.lower() for c in columns]:
        distinct_months = {
            str(r.get("budget_month") or "").strip()
            for r in rows
            if r.get("budget_month")
        }
        if len(distinct_months) >= 2:
            return None
        if len(expected_months) >= 2 and len(distinct_months) <= 1:
            if "budget_month" not in group_by:
                return (
                    "Comparison question: query merged months into one total per category. "
                    "Re-run query_sql with either (a) GROUP BY ai_category, budget_month "
                    "so each month is a separate row, or (b) pivot with "
                    "SUM(CASE WHEN budget_month='YYYY-MM' THEN -amount ELSE 0 END) "
                    "so each month is its own column. Do not combine months."
                )

    pivot_cols = _compare_month_columns(columns, expected_months)
    if pivot_cols >= 2:
        return None

    if re.search(r"case\s+when\s+.*budget_month", sql_lower) and pivot_cols >= 1:
        return None

    if any(c.lower() in ("ai_category", "category") for c in columns) and len(columns) <= 2:
        return (
            "Comparison question: results show one total per category with no month breakdown. "
            "Add separate columns or rows per month (see CASE pivot or GROUP BY budget_month)."
        )

    if len(expected_months) >= 2 and "budget_month" in sql_lower and "budget_month" not in group_by:
        if any(c.lower() in ("ai_category", "category") for c in columns):
            return (
                "Comparison question: budget_month appears in WHERE but not in GROUP BY, "
                "so months were combined. Include budget_month in GROUP BY or use a CASE pivot."
            )

    return None


def trace_has_successful_query(trace: list[dict[str, Any]]) -> bool:
    for entry in trace:
        if entry.get("tool") != "query_sql":
            continue
        result = entry.get("result") or {}
        if result.get("error"):
            continue
        if result.get("validation_rejected"):
            continue
        if result.get("rows") is not None:
            return True
    return False
