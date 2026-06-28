from __future__ import annotations

import sqlite3
from typing import Any

from webapp import analytics

from webapp.agent.db_query import LEARNING_AGENT_ALLOWED_TABLES, execute_readonly_sql
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
        "list_open_insights",
        "propose_custom_rule",
        "run_decision_analysis",
        "accept_insight",
        "reject_insight",
    }
)

# Chat: query_sql-first. Helpers for HITL workspace workflows.
CHAT_READ_ONLY_TOOLS = frozenset(
    {
        "query_sql",
        "list_custom_reports",
        "run_custom_report",
        "propose_cadence_rule",
        "list_open_insights",
        "propose_custom_rule",
        "run_decision_analysis",
        "accept_insight",
        "reject_insight",
    }
)

LEARNING_AGENT_TOOLS = frozenset({"query_sql"})

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
        "description": (
            "Re-run a saved custom report by name/id with parameters. "
            "Use for reruns and as baseline when user tweaks a saved report."
        ),
        "parameters": {
            "report": "report_id or name (required)",
            "params": "optional dict, e.g. {\"month\": \"2026-05\"} or {\"months\": [\"2026-03\",\"2026-04\"]}",
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
    {
        "name": "list_open_insights",
        "description": (
            "List open Learning Agent insights awaiting user review in the Workspace inbox. "
            "Use when user asks about pending AI proposals or decision patterns."
        ),
        "parameters": {"limit": "optional int default 20 max 50"},
    },
    {
        "name": "propose_custom_rule",
        "description": (
            "Draft a plain-English custom rule and preview matching transactions. "
            "Does not save until user confirms in Workspace."
        ),
        "parameters": {
            "rule_text": "plain-English if/then rule (required)",
            "preview_limit": "optional int default 5",
        },
    },
    {
        "name": "run_decision_analysis",
        "description": (
            "Run Decision Analyst over decision_events and related tables. "
            "Inserts new open insights into the Workspace inbox (same as Settings → Run analysis now)."
        ),
        "parameters": {},
    },
    {
        "name": "accept_insight",
        "description": "Accept an open ai_insights row by id (user explicitly asked to accept).",
        "parameters": {"insight_id": "integer id from list_open_insights or inbox"},
    },
    {
        "name": "reject_insight",
        "description": "Reject/dismiss an open ai_insights row by id.",
        "parameters": {"insight_id": "integer id"},
    },
]

CHAT_TOOL_DEFINITIONS = [t for t in TOOL_DEFINITIONS if t["name"] in CHAT_READ_ONLY_TOOLS]


def chat_tool_definitions(*, include_cadence: bool = True) -> list[dict[str, Any]]:
    if include_cadence:
        return list(CHAT_TOOL_DEFINITIONS)
    return [t for t in CHAT_TOOL_DEFINITIONS if t["name"] != "propose_cadence_rule"]


def run_tool(
    conn: sqlite3.Connection,
    name: str,
    args: dict[str, Any],
    *,
    chat_mode: bool = False,
    learning_agent_mode: bool = False,
    learning_agent_allowed_tables: frozenset[str] | None = None,
) -> Any:
    if name not in ALL_TOOL_NAMES:
        raise ValueError(f"Unknown tool: {name}")
    if learning_agent_mode and name not in LEARNING_AGENT_TOOLS:
        raise ValueError(
            f"Tool {name!r} is not available for the Learning Agent — use query_sql."
        )
    if chat_mode and name not in CHAT_READ_ONLY_TOOLS:
        raise ValueError(
            f"Tool {name!r} is not available in chat — use query_sql for data questions."
        )
    from webapp.config import UI_SHOW_CADENCE

    if chat_mode and name == "propose_cadence_rule" and not UI_SHOW_CADENCE:
        raise ValueError("Cadence proposals are disabled (UI_SHOW_CADENCE=0).")
    if name == "query_sql":
        la_tables = learning_agent_allowed_tables
        if learning_agent_mode and la_tables is None:
            from webapp.agent.db_query import learning_agent_allowed_tables as _la_tables

            la_tables = _la_tables(include_cadence=UI_SHOW_CADENCE)
        return execute_readonly_sql(
            conn,
            args["sql"],
            max_rows=int(args.get("max_rows", 500)),
            allowed_tables=(
                la_tables
                if learning_agent_mode
                else None
            ),
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
    if name == "list_open_insights":
        limit = max(1, min(50, int(args.get("limit", 20))))
        rows = conn.execute(
            """
            SELECT id, insight_type, title, pattern_summary, rationale,
                   confidence, merchant_key, proposal_json, created_at
            FROM ai_insights
            WHERE status = 'open'
            ORDER BY confidence DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "insights": [dict(r) for r in rows]}
    if name == "propose_custom_rule":
        from webapp.services.custom_rule_similarity import find_similar_custom_rule
        from webapp.services.custom_rules import preview_custom_rule

        rule_text = str(args.get("rule_text") or "").strip()
        if not rule_text:
            raise ValueError("rule_text is required")
        preview_limit = max(1, min(20, int(args.get("preview_limit", 5))))
        preview = preview_custom_rule(
            conn, rule_text=rule_text, limit=preview_limit, offset=0
        )
        similar = find_similar_custom_rule(suggested_rule=rule_text, conn=conn)
        recommend = not preview.get("compile_error") and not similar
        return {
            "rule_text": rule_text,
            "preview": preview,
            "recommend_save_rule": recommend,
            "existing_similar_rule": similar.get("rule") if similar else None,
            "existing_rule_status": similar.get("status") if similar else None,
        }
    if name == "run_decision_analysis":
        from webapp.config import DB_PATH
        from webapp.services.learning_agent import run_learning_agent

        return run_learning_agent(DB_PATH, force=True)
    if name == "accept_insight":
        from webapp.services.learning_agent import accept_insight

        insight_id = int(args["insight_id"])
        return accept_insight(conn, insight_id)
    if name == "reject_insight":
        from webapp.services.learning_agent import reject_insight

        insight_id = int(args["insight_id"])
        return reject_insight(conn, insight_id)
    raise ValueError(f"Unknown tool: {name}")
