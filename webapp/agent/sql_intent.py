"""Detect question intent and validate query_sql matches what the user asked."""

from __future__ import annotations

import re
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
)


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
) -> str | None:
    """
    Return an error string when SQL/results do not match the question.
    None means validation passed.
    """
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
