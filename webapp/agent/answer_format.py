"""Format chat answers without duplicating UI-rendered tables/charts."""

from __future__ import annotations

import re
from typing import Any

# "Shopping: $5,166.80" / "- Income: $6,602.79 (likely payroll)" / "1. Groceries $949.29"
_AMOUNT_BREAKDOWN_LINE = re.compile(
    r"^(?:[-*•]\s+|\d+[.)]\s+)?"
    r".{1,100}?"
    r"(?::\s+|\s+)"
    r"\$[\d,]+(?:\.\d{1,2})?"
    r"(?:\s*\([^)]*\))?"
    r"\s*$"
)


def strip_markdown_tables(text: str) -> str:
    """Remove markdown pipe tables from assistant prose."""
    lines = text.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        s = line.strip()
        is_table_row = s.startswith("|") and s.count("|") >= 2
        is_table_sep = bool(re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$", s))
        if is_table_row or is_table_sep:
            skipping = True
            continue
        if skipping and not s:
            skipping = False
            continue
        if skipping:
            continue
        kept.append(line)
    text = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def is_amount_breakdown_line(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith("#"):
        return False
    return bool(_AMOUNT_BREAKDOWN_LINE.match(s))


def strip_redundant_amount_lists(text: str, display: dict[str, Any] | None = None) -> str:
    """Drop long Category: $amount dumps when the UI already renders those rows."""
    lines = text.splitlines()
    money_idxs = [i for i, line in enumerate(lines) if is_amount_breakdown_line(line)]
    row_count = len((display or {}).get("rows") or [])
    # Enough lines to look like a full table dump (not a short "top 3" highlight).
    threshold = 4 if row_count <= 0 else min(4, max(3, row_count // 3))
    if len(money_idxs) < threshold:
        return text
    drop = set(money_idxs)
    kept = [line for i, line in enumerate(lines) if i not in drop]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned


def display_prose_summary(display: dict[str, Any]) -> str:
    dtype = display.get("type")
    title = str(display.get("title") or "Results")
    summary = str(display.get("summary") or "").strip()
    if dtype == "table":
        row_count = len(display.get("rows") or [])
        detail = summary or f"{row_count} row(s)"
        return f"**{title}** — {detail}. See interactive table below."
    if dtype == "chart":
        suffix = f" — {summary}" if summary else ""
        return f"**{title}**{suffix}. See chart below."
    return summary or title


def coalesce_answer_with_display(
    answer: str,
    display: dict[str, Any] | None,
) -> str:
    """When the UI renders a table/chart, keep short prose only — not a second copy of the rows."""
    if not display or display.get("type") not in ("table", "chart"):
        return answer
    prose = strip_markdown_tables(answer).strip()
    if display.get("type") == "table":
        prose = strip_redundant_amount_lists(prose, display).strip()
    if prose:
        return prose
    return display_prose_summary(display)


def query_sql_prose_fallback(result: dict[str, Any]) -> str:
    count = int(result.get("row_count") or len(result.get("rows") or []))
    return f"Query returned **{count}** row(s). See table below."
