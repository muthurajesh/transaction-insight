from __future__ import annotations

import sqlite3
from typing import Any

from transaction_insight import analytics

from webapp.agent.db_query import execute_readonly_sql
from webapp.services import custom_reports as saved_reports

month_total = analytics.month_total
flow_totals_by_month = analytics.flow_totals_by_month
top_categories = analytics.top_categories
month_vs_avg = analytics.month_vs_avg
list_outliers = analytics.list_outliers
available_months = analytics.available_months
list_transactions = analytics.list_transactions


TOOL_DEFINITIONS = [
    {
        "name": "query_sql",
        "description": (
            "Run a read-only SELECT against the SQLite database. "
            "Preferred for most data questions — write SQL directly. "
            "Main table: transactions (use budget_month for monthly rollups; "
            "expenses are negative amounts with flow_type='Expense')."
        ),
        "parameters": {
            "sql": "SELECT … (required, read-only)",
            "max_rows": "optional int default 500 max 2000",
        },
    },
    {
        "name": "month_total",
        "description": "Total spend or income for ONE budget month (YYYY-MM). Omit month to use latest full month.",
        "parameters": {"month": "optional YYYY-MM", "flow": "Expense or Income"},
    },
    {
        "name": "flow_totals_by_month",
        "description": (
            "Totals for ALL full months in one call. Use for 'each month', 'all months', "
            "'income by month', or 'spending by month'. Income respects paycheck spillover "
            "(stored in budget_month)."
        ),
        "parameters": {"flow": "Income or Expense", "full_months_only": "optional bool default true"},
    },
    {
        "name": "top_categories",
        "description": "Top spending categories for a month (outflows only).",
        "parameters": {"month": "YYYY-MM", "limit": "optional int"},
    },
    {
        "name": "month_vs_avg",
        "description": "Compare month total expenses to average across all months.",
        "parameters": {"month": "YYYY-MM"},
    },
    {
        "name": "list_outliers",
        "description": "Categories with unusual spend vs their historical monthly average.",
        "parameters": {"month": "YYYY-MM", "threshold_pct": "optional float default 50"},
    },
    {
        "name": "available_months",
        "description": (
            "List budget months in the database with transaction counts. "
            "Use full_months for complete monthly exports; partial months may be payroll spillover only."
        ),
        "parameters": {},
    },
    {
        "name": "save_custom_report",
        "description": (
            "Save a read-only SELECT as a named custom report the user can re-run later. "
            "AI has write access ONLY to custom_reports (not transactions). "
            "Use SQLite named params: :month, :months (JSON array), :limit, :category."
        ),
        "parameters": {
            "name": "short display name (required)",
            "sql_template": "SELECT with :month / :months / :limit / :category placeholders",
            "description": "optional what this report shows",
            "original_question": "optional user words that led to this report",
            "parameters": "optional list matching SQL placeholders",
        },
    },
    {
        "name": "list_custom_reports",
        "description": "List all saved custom reports (names, parameters, descriptions).",
        "parameters": {},
    },
    {
        "name": "run_custom_report",
        "description": (
            "Run a saved report by report_id or name. "
            "Pass params e.g. month='2026-04' or months=['2026-03','2026-04'], limit=10."
        ),
        "parameters": {
            "report": "report_id or name (required)",
            "params": "optional dict of parameter values",
            "max_rows": "optional int default 500",
        },
    },
    {
        "name": "delete_custom_report",
        "description": "Delete a saved custom report by report_id or name.",
        "parameters": {"report": "report_id or name (required)"},
    },
    {
        "name": "list_transactions",
        "description": (
            "List individual transactions for one budget month, optionally filtered by ai_category. "
            "Use when the user asks to see/show/list transactions for a category and month."
        ),
        "parameters": {
            "month": "YYYY-MM (required)",
            "category": "optional ai_category e.g. Insurance, Groceries",
            "flow": "optional Expense (default), Income, Transfer, Adjustment",
            "limit": "optional int default 100 max 500",
        },
    },
]


def run_tool(conn: sqlite3.Connection, name: str, args: dict[str, Any]) -> Any:
    if name == "query_sql":
        return execute_readonly_sql(
            conn,
            args["sql"],
            max_rows=int(args.get("max_rows", 500)),
        )
    if name == "month_total":
        return month_total(conn, args.get("month"), flow=args.get("flow", "Expense"))
    if name == "flow_totals_by_month":
        return flow_totals_by_month(
            conn,
            flow=args.get("flow", "Income"),
            full_months_only=bool(args.get("full_months_only", True)),
        )
    if name == "top_categories":
        return top_categories(conn, args["month"], limit=int(args.get("limit", 10)))
    if name == "month_vs_avg":
        return month_vs_avg(conn, args["month"])
    if name == "list_outliers":
        return list_outliers(
            conn, args["month"], threshold_pct=float(args.get("threshold_pct", 50))
        )
    if name == "available_months":
        return available_months(conn)
    if name == "list_transactions":
        return list_transactions(
            conn,
            args["month"],
            category=args.get("category"),
            flow=args.get("flow", "Expense"),
            limit=int(args.get("limit", 100)),
        )
    if name == "save_custom_report":
        return saved_reports.save_custom_report(
            conn,
            name=args["name"],
            sql_template=args["sql_template"],
            description=args.get("description", ""),
            original_question=args.get("original_question", ""),
            parameters=args.get("parameters"),
            report_id=args.get("report_id"),
        )
    if name == "list_custom_reports":
        return saved_reports.list_custom_reports(conn)
    if name == "run_custom_report":
        return saved_reports.run_custom_report(
            conn,
            args["report"],
            params=args.get("params"),
            max_rows=int(args.get("max_rows", 500)),
        )
    if name == "delete_custom_report":
        deleted = saved_reports.delete_custom_report(conn, args["report"])
        return {"deleted": deleted, "report": args["report"]}
    raise ValueError(f"Unknown tool: {name}")
