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
        cols = [{"field": c, "title": c.replace("_", " ").title()} for c in columns_raw]
        return table_display(
            title=title,
            summary=f"{result.get('row_count', len(rows))} row(s)",
            columns=cols,
            rows=rows,
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
