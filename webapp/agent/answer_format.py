"""Format chat answers without duplicating UI-rendered tables/charts."""

from __future__ import annotations

import re
from typing import Any


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
    """When the UI renders a table/chart, keep prose only in the text answer."""
    if not display or display.get("type") not in ("table", "chart"):
        return answer
    prose = strip_markdown_tables(answer).strip()
    if prose:
        return prose
    return display_prose_summary(display)


def query_sql_prose_fallback(result: dict[str, Any]) -> str:
    count = int(result.get("row_count") or len(result.get("rows") or []))
    return f"Query returned **{count}** row(s). See table below."
