from __future__ import annotations

from typing import Any


def _view_suffix(args: dict[str, Any] | None) -> str:
    if not args:
        return ""
    view = str(args.get("expense_view") or "cash").strip().lower()
    if view == "cash":
        return ""
    try:
        from webapp.services.expense_cadence import expense_view_label

        return f" ({expense_view_label(view)})"
    except Exception:
        return f" ({view})"


def _money_format(amount: float) -> str:
    try:
        n = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    abs_n = abs(n)
    text = f"${abs_n:,.2f}"
    return f"-{text}" if n < 0 else text


_COLUMN_TITLE_OVERRIDES: dict[str, str] = {
    "ai_category": "AI Category",
    "ai_sub_category": "AI Sub-category",
}


def _column_title(column: str) -> str:
    """Human-readable column header from SQL field name (snake_case)."""
    key = str(column or "").strip().lower()
    if key in _COLUMN_TITLE_OVERRIDES:
        return _COLUMN_TITLE_OVERRIDES[key]
    titled = key.replace("_", " ").title()
    # str.title() lowercases "AI" → "Ai"; restore common abbreviations.
    return titled.replace("Ai ", "AI ")


_NUMERIC_COL_HINTS = frozenset(
    {
        "spend",
        "total",
        "amount",
        "sum",
        "sum_amount",
        "expense",
        "value",
    }
)
_LABEL_COL_HINTS = frozenset(
    {
        "category",
        "ai_category",
        "merchant_key",
        "budget_month",
        "month",
        "name",
        "label",
    }
)
_TOTAL_LABELS = frozenset({"total", "grand total", "totals", "sum"})


def _is_numeric_value(val: Any) -> bool:
    if isinstance(val, (int, float)):
        return True
    if val is None:
        return False
    try:
        float(val)
        return True
    except (TypeError, ValueError):
        return False


def _detect_label_and_value_columns(
    columns: list[str], rows: list[dict[str, Any]]
) -> tuple[str | None, str | None]:
    if not columns or not rows:
        return None, None
    lower_cols = [c.lower() for c in columns]
    value_col = None
    for hint in _NUMERIC_COL_HINTS:
        for col, lc in zip(columns, lower_cols):
            if hint in lc and any(_is_numeric_value(r.get(col)) for r in rows):
                value_col = col
                break
        if value_col:
            break
    if not value_col:
        for col in columns:
            if any(_is_numeric_value(r.get(col)) for r in rows):
                value_col = col
                break
    label_col = None
    for hint in _LABEL_COL_HINTS:
        for col, lc in zip(columns, lower_cols):
            if hint in lc and col != value_col:
                label_col = col
                break
        if label_col:
            break
    if not label_col:
        for col in columns:
            if col != value_col:
                label_col = col
                break
    return label_col, value_col


def _grand_total_from_rows(
    rows: list[dict[str, Any]], value_col: str | None, label_col: str | None
) -> float | None:
    if not rows or not value_col:
        return None
    total = 0.0
    found = False
    for row in rows:
        label = str(row.get(label_col or "", "")).strip().lower() if label_col else ""
        if label in _TOTAL_LABELS:
            val = row.get(value_col)
            if _is_numeric_value(val):
                return float(val)
        if label in _TOTAL_LABELS:
            continue
        val = row.get(value_col)
        if _is_numeric_value(val):
            total += float(val)
            found = True
    return total if found else None


def _sql_result_display(
    *,
    title: str,
    columns_raw: list[str],
    rows: list[dict[str, Any]],
    row_count: int,
    report_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = report_config or {}
    display_cfg = config.get("display") if isinstance(config.get("display"), dict) else {}
    chart_cfg = config.get("chart") if isinstance(config.get("chart"), dict) else {}
    show_total = display_cfg.get("show_grand_total", True)
    chart_enabled = bool(chart_cfg.get("enabled"))

    label_col, value_col = _detect_label_and_value_columns(columns_raw, rows)
    grand_total = _grand_total_from_rows(rows, value_col, label_col) if show_total else None

    summary_parts = [f"{row_count} row(s)"]
    if grand_total is not None:
        summary_parts.append(f"Grand total: ${grand_total:,.2f}")

    use_chart = chart_enabled and label_col and value_col and len(rows) <= 15
    if not chart_enabled and label_col and value_col and 2 <= len(rows) <= 12:
        cat_like = label_col.lower() in ("category", "ai_category", "merchant_key")
        use_chart = cat_like

    if use_chart and label_col and value_col:
        chart_rows = [
            r
            for r in rows
            if str(r.get(label_col, "")).strip().lower() not in _TOTAL_LABELS
        ]
        labels = [str(r.get(label_col, "")) for r in chart_rows]
        data = [float(r.get(value_col) or 0) for r in chart_rows]
        return chart_display(
            title=title,
            chart_type=str(chart_cfg.get("type") or "bar"),
            labels=labels,
            datasets=[{"label": _column_title(value_col), "data": data}],
            summary=" · ".join(summary_parts),
        )

    cols = [{"field": c, "title": _column_title(c)} for c in columns_raw]
    return table_display(
        title=title,
        summary=" · ".join(summary_parts),
        columns=cols,
        rows=rows,
    )


def table_display(
    *,
    title: str,
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    summary: str = "",
) -> dict[str, Any]:
    return {
        "type": "table",
        "title": title,
        "columns": columns,
        "rows": rows,
        "summary": summary,
    }


def chart_display(
    *,
    title: str,
    chart_type: str,
    labels: list[str],
    datasets: list[dict[str, Any]],
    summary: str = "",
) -> dict[str, Any]:
    return {
        "type": "chart",
        "chartType": chart_type,
        "title": title,
        "labels": labels,
        "datasets": datasets,
        "summary": summary,
    }


def display_from_tool_result(
    tool: str,
    result: Any,
    args: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    args = args or {}

    if tool == "flow_totals_by_month":
        if not isinstance(result, dict) or result.get("error"):
            return None
        months = result.get("months") or []
        if not months:
            return None
        flow = result.get("flow_type", "Expense")
        view_suffix = (
            f" — {result.get('expense_view_label')}"
            if result.get("expense_view_label")
            else _view_suffix(args)
        )
        labels = [str(m["month"]) for m in months]
        data = [float(m["total"] or 0) for m in months]
        return chart_display(
            title=f"{flow} by month{view_suffix}",
            chart_type="bar",
            labels=labels,
            datasets=[{"label": flow, "data": data}],
            summary=(
                f"Grand total: ${result.get('grand_total', 0):,.2f} "
                f"across {len(months)} month(s)"
                + (f" · View: {result.get('expense_view_label')}" if result.get("expense_view_label") else "")
            ),
        )

    if tool == "category_average":
        if not isinstance(result, dict) or not result.get("months"):
            return None
        months = result.get("months") or []
        cats = result.get("categories") or []
        label = ", ".join(cats) if len(cats) <= 2 else f"{cats[0]} (+{len(cats) - 1})"
        n = int(result.get("month_count") or len(months))
        labels = [str(m["month"]) for m in months]
        data = [float(m["spend"] or 0) for m in months]
        return chart_display(
            title=f"{label} — last {n} month(s)",
            chart_type="bar",
            labels=labels,
            datasets=[{"label": "Spend", "data": data}],
            summary=(
                f"Average per month: ${result.get('average_spend', 0):,.2f} "
                f"· Total: ${result.get('total_spend', 0):,.2f}"
            ),
        )

    if tool == "top_categories":
        if not isinstance(result, list) or not result:
            return None
        month = str(args.get("month") or "").strip()
        view_suffix = _view_suffix(args)
        labels = [str(r.get("category") or "") for r in result]
        data = [float(r.get("spend") or 0) for r in result]
        title = (
            f"Top categories — {month}{view_suffix}"
            if month
            else f"Top categories{view_suffix}"
        )
        total = sum(data)
        view_note = view_suffix.strip(" ()") or ""
        summary = f"Total shown: ${total:,.2f} ({len(result)} categories)"
        if view_note:
            summary += f" · View: {view_note}"
        return chart_display(
            title=title,
            chart_type="bar",
            labels=labels,
            datasets=[{"label": "Spend", "data": data}],
            summary=summary,
        )

    if not isinstance(result, dict) or result.get("error"):
        return None

    if tool == "list_transactions":
        txs = result.get("transactions") or []
        if not txs:
            return None
        month = result.get("month", "")
        category = result.get("category")
        flow = result.get("flow_type", "Expense")
        title_bits = [flow, "transactions", month]
        if category:
            title_bits.insert(1, f"— {category}")
        rows = []
        for tx in txs:
            amt = float(tx.get("amount") or 0)
            rows.append(
                {
                    "date": tx.get("date", ""),
                    "amount": amt,
                    "amount_display": _money_format(amt) if flow == "Expense" else f"${amt:,.2f}",
                    "merchant_key": tx.get("merchant_key", ""),
                    "ai_category": tx.get("ai_category") or "—",
                    "ai_sub_category": tx.get("ai_sub_category") or "—",
                    "classification": tx.get("classification") or "—",
                    "flow_type": tx.get("flow_type") or flow,
                    "expense_type": tx.get("expense_type") or "—",
                }
            )
        return table_display(
            title=" ".join(title_bits),
            summary=f"Total: ${result.get('total', 0):,.2f} ({result.get('transaction_count', len(rows))} transactions)",
            columns=[
                {"field": "date", "title": "Date"},
                {"field": "amount_display", "title": "Amount", "formatter": "money"},
                {"field": "merchant_key", "title": "Merchant"},
                {"field": "ai_category", "title": "AI Category"},
                {"field": "ai_sub_category", "title": "AI Sub-category"},
                {"field": "classification", "title": "Classification"},
                {"field": "flow_type", "title": "Flow"},
                {"field": "expense_type", "title": "Expense Type"},
            ],
            rows=rows,
        )

    if tool in ("query_sql", "run_custom_report"):
        rows = result.get("rows") or []
        columns_raw = result.get("columns") or []
        if not rows or not columns_raw:
            return None
        title = result.get("name") or "Query results"
        if tool == "run_custom_report" and result.get("parameters_used"):
            used = ", ".join(f"{k}={v}" for k, v in result["parameters_used"].items())
            title = f"{title} ({used})"
        report_config = result.get("report_config") if tool == "run_custom_report" else None
        return _sql_result_display(
            title=title,
            columns_raw=columns_raw,
            rows=rows,
            row_count=int(result.get("row_count", len(rows))),
            report_config=report_config,
        )

    return None


def display_from_trace(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in reversed(trace):
        tool = entry.get("tool")
        result = entry.get("result")
        if tool == "list_custom_reports":
            continue
        built = display_from_tool_result(str(tool or ""), result, entry.get("args") or {})
        if built:
            return built
    return None
