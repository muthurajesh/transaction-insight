from __future__ import annotations

import sqlite3
from typing import Any

from webapp import analytics

from webapp.agent.db_query import execute_readonly_sql
from webapp.services import custom_reports as saved_reports
from webapp.services.cadence_insights import propose_cadence

# Full registry (non-chat callers may still use analytics helpers).
ALL_TOOL_NAMES = frozenset(
    {
        "query_sql",
        "month_total",
        "flow_totals_by_month",
        "top_categories",
        "compare_categories_by_months",
        "month_vs_avg",
        "list_outliers",
        "available_months",
        "list_transactions",
        "list_custom_reports",
        "run_custom_report",
        "propose_cadence_rule",
    }
)

# Chat: query_sql-first. Helpers only for workflows SQL cannot replace.
CHAT_READ_ONLY_TOOLS = frozenset(
    {
        "query_sql",
        "list_custom_reports",
        "run_custom_report",
        "propose_cadence_rule",
    }
)

month_total = analytics.month_total
flow_totals_by_month = analytics.flow_totals_by_month
top_categories = analytics.top_categories
compare_categories_by_months = analytics.compare_categories_by_months
month_vs_avg = analytics.month_vs_avg
list_outliers = analytics.list_outliers
available_months = analytics.available_months
list_transactions = analytics.list_transactions


TOOL_DEFINITIONS = [
    {
        "name": "query_sql",
        "description": (
            "**Primary tool.** Run read-only SELECT on SQLite `transactions` (and related tables). "
            "Use for spending, categories, merchants, comparisons, averages, trends, lists. "
            "Expenses: flow_type='Expense' AND amount<0; totals use SUM(-amount). "
            "Monthly rollups: budget_month (YYYY-MM). "
            "Compare months side-by-side: GROUP BY ai_category, budget_month OR pivot with CASE WHEN budget_month=..."
        ),
        "parameters": {
            "sql": "SELECT … (required, read-only)",
            "max_rows": "optional int default 500 max 2000",
        },
    },
    {
        "name": "list_custom_reports",
        "description": "List saved custom reports (names, parameters). Not for ad-hoc analysis — use query_sql.",
        "parameters": {},
    },
    {
        "name": "run_custom_report",
        "description": "Re-run a saved custom report by name/id with parameters.",
        "parameters": {
            "report": "report_id or name (required)",
            "params": "optional dict of parameter values",
            "max_rows": "optional int default 500",
        },
    },
    {
        "name": "propose_cadence_rule",
        "description": (
            "Propose expense cadence for a merchant (annual, monthly, bi-weekly). "
            "UI workflow only — does not save until user confirms."
        ),
        "parameters": {
            "merchant_key": "optional exact merchant_key",
            "transaction_id": "optional transaction id",
            "hint": "optional user words about cadence",
        },
    },
]

CHAT_TOOL_DEFINITIONS = [t for t in TOOL_DEFINITIONS if t["name"] in CHAT_READ_ONLY_TOOLS]


def run_tool(
    conn: sqlite3.Connection,
    name: str,
    args: dict[str, Any],
    *,
    chat_mode: bool = False,
) -> Any:
    if name not in ALL_TOOL_NAMES:
        raise ValueError(f"Unknown tool: {name}")
    if chat_mode and name not in CHAT_READ_ONLY_TOOLS:
        raise ValueError(
            f"Tool {name!r} is not available in chat — use query_sql for data questions."
        )
    if name == "query_sql":
        return execute_readonly_sql(
            conn,
            args["sql"],
            max_rows=int(args.get("max_rows", 500)),
        )
    if name == "month_total":
        return month_total(
            conn,
            args.get("month"),
            flow=args.get("flow", "Expense"),
            expense_view=str(args.get("expense_view", "cash")),
        )
    if name == "flow_totals_by_month":
        return flow_totals_by_month(
            conn,
            flow=args.get("flow", "Income"),
            full_months_only=bool(args.get("full_months_only", True)),
            expense_view=str(args.get("expense_view", "cash")),
        )
    if name == "top_categories":
        return top_categories(
            conn,
            args["month"],
            limit=int(args.get("limit", 10)),
            expense_view=str(args.get("expense_view", "cash")),
        )
    if name == "compare_categories_by_months":
        return compare_categories_by_months(
            conn,
            args["months"],
            limit=int(args.get("limit", 100)),
        )
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
    if name == "list_custom_reports":
        return saved_reports.list_custom_reports(conn)
    if name == "run_custom_report":
        return saved_reports.run_custom_report(
            conn,
            args["report"],
            params=args.get("params"),
            max_rows=int(args.get("max_rows", 500)),
        )
    if name == "propose_cadence_rule":
        return propose_cadence(
            conn,
            merchant_key=args.get("merchant_key"),
            transaction_id=args.get("transaction_id"),
            hint=str(args.get("hint") or ""),
        )
    raise ValueError(f"Unknown tool: {name}")
