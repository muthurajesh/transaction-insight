from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from webapp.agent.display import display_from_trace
from webapp.agent.tools import TOOL_DEFINITIONS, available_months, run_tool
from webapp.config import INBOX_DIR
from webapp.services.llm import chat_completion, extract_json

_DATA_CHEATSHEET_PATH = Path(__file__).resolve().parent / "DATA_CHEATSHEET.md"

CHAT_SYSTEM = """You are a personal finance assistant with SQL access to a local SQLite database.
Never invent dollar amounts. Pull data with tools before answering.

## How to get data
- **Prefer `query_sql`** — write SELECT queries directly against `transactions`.
- Helper tools (`month_total`, `list_transactions`, etc.) exist for common patterns; use them only if easier.

## Saved custom reports (AI write access)
- **`custom_reports` table** — the ONLY table you may write to (via `save_custom_report` / `delete_custom_report`).
- When the user likes an insight, offer to save it: `save_custom_report` with a clear **name** and parameterized `sql_template`.
- Use SQLite named params: `:month` (YYYY-MM), `:months` (JSON array for multi-month), `:limit`, `:category`.
- List saved reports: `list_custom_reports`. Re-run with new inputs: `run_custom_report`.

## SQL tips
- Monthly questions: filter on `budget_month` (`YYYY-MM`), not `date`.
- Expense spend: `flow_type = 'Expense' AND amount < 0`; use `SUM(-amount)` for totals.
- Income: `flow_type = 'Income'`.
- Categories: `ai_category`, detail: `ai_sub_category`, merchant: `merchant_key`.
- Top expenses (largest outflows): `ORDER BY amount ASC` (more negative first).

## Data model cheat sheet
{data_cheatsheet}

Available tools (return JSON to call one):
{tools}

To call a tool, respond with ONLY:
{{"tool": "<name>", "args": {{ ... }}}}

When you have enough data to answer, respond with ONLY:
{{"answer": "<markdown-friendly plain text>"}}
"""


def _chat_payload(answer: str, trace: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    trace = trace or []
    display = display_from_trace(trace)
    if display:
        title = str(display.get("title") or "").strip()
        summary = str(display.get("summary") or "").strip()
        if title and summary:
            answer = f"**{title}**\n\n{summary}"
        elif title:
            answer = f"**{title}**"
        elif summary:
            answer = summary
    payload: dict[str, Any] = {"answer": answer, "tool_trace": trace}
    if display:
        payload["display"] = display
    return payload


def _load_data_cheatsheet() -> str:
    if not _DATA_CHEATSHEET_PATH.is_file():
        return "(Data cheat sheet not found.)"
    return _DATA_CHEATSHEET_PATH.read_text(encoding="utf-8").strip()


def _save_message(conn: sqlite3.Connection, role: str, content: str, tool_trace: str | None = None) -> None:
    conn.execute(
        "INSERT INTO chat_messages (role, content, tool_trace, created_at) VALUES (?, ?, ?, ?)",
        (role, content, tool_trace, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _parse_action(text: str) -> dict[str, Any]:
    try:
        return extract_json(text)
    except ValueError:
        pass
    if "{" in text:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
    return {"answer": text}


_MONTH_NAMES = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def _format_top_categories(result: list[dict[str, Any]], month: str = "") -> str:
    if not result:
        label = f" for {month}" if month else ""
        return f"No spending categories found{label}."
    title = f"**Top spending categories — {month}**\n" if month else "**Top spending categories**\n"
    lines = [title, "| Category | Spend | Transactions |", "| --- | ---: | ---: |"]
    for row in result:
        lines.append(
            f"| {row['category']} | ${row['spend']:,.2f} | {row['transaction_count']} |"
        )
    return "\n".join(lines)


def _format_flow_totals(result: dict[str, Any]) -> str:
    flow = result.get("flow_type", "Total")
    months = result.get("months") or []
    if not months:
        return f"No {flow.lower()} data for full months in the database yet."
    lines = [f"**{flow} by month** (full exports only)\n", "| Month | Total | Transactions |", "| --- | ---: | ---: |"]
    for row in months:
        lines.append(
            f"| {row['month']} | ${row['total']:,.2f} | {row['transaction_count']} |"
        )
    lines.append(f"\n**Grand total:** ${result.get('grand_total', 0):,.2f}")
    return "\n".join(lines)


def _format_transaction_list(result: dict[str, Any]) -> str:
    month = result.get("month", "")
    category = result.get("category")
    flow = result.get("flow_type", "Expense")
    txs = result.get("transactions") or []
    if not txs:
        label = f"{category} " if category else ""
        return f"No {label}{flow.lower()} transactions found for {month}."

    title_bits = [flow, "transactions", f"for {month}"]
    if category:
        title_bits.insert(1, f"— {category}")
    lines = [f"**{' '.join(title_bits)}**\n"]
    lines.extend(
        [
            "| Date | Amount | Merchant | AI Category | AI Sub-category | Classification | Flow | Expense Type |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for tx in txs:
        amt = float(tx.get("amount") or 0)
        display_amt = f"${abs(amt):,.2f}" if flow == "Expense" else f"${amt:,.2f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(tx.get("date", "")),
                    display_amt,
                    str(tx.get("merchant_key", "")),
                    str(tx.get("ai_category") or "—"),
                    str(tx.get("ai_sub_category") or "—"),
                    str(tx.get("classification") or "—"),
                    str(tx.get("flow_type") or flow),
                    str(tx.get("expense_type") or "—"),
                ]
            )
            + " |"
        )
    lines.append(
        f"\n**Total:** ${result.get('total', 0):,.2f} "
        f"({result.get('transaction_count', 0)} transactions)"
    )
    if result.get("truncated"):
        lines.append("\n_(List truncated — increase limit or narrow the filter.)_")
    return "\n".join(lines)


def _format_query_rows(result: dict[str, Any], *, title: str) -> str:
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    if not rows:
        return f"{title}\n\nNo rows returned."
    lines = [f"**{title}**\n", "| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        cells = []
        for col in columns:
            val = row.get(col, "")
            if isinstance(val, float):
                cells.append(f"{val:,.2f}")
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append(f"\n**{result.get('row_count', len(rows))} row(s)**")
    if result.get("truncated"):
        lines.append("\n_(Truncated — narrow the query or raise max_rows.)_")
    return "\n".join(lines)


def _format_custom_reports_list(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return "You have no saved custom reports yet. Ask me to save a query after we find an insight you like."
    lines = ["**Saved custom reports**\n", "| Name | Parameters | Description |", "| --- | --- | --- |"]
    for r in reports:
        params = ", ".join(r.get("parameters") or []) or "—"
        desc = (r.get("description") or r.get("original_question") or "—")[:80]
        lines.append(f"| {r.get('name', '')} | {params} | {desc} |")
    lines.append(
        "\nRe-run with: `run_custom_report` and the report name (e.g. pass `month` or `months`)."
    )
    return "\n".join(lines)


def _format_custom_report_run(result: dict[str, Any]) -> str:
    title = result.get("name") or "Custom report"
    used = result.get("parameters_used") or {}
    if used:
        title = f"{title} ({', '.join(f'{k}={v}' for k, v in used.items())})"
    return _format_query_rows(result, title=title)


def _parse_month_from_message(user_message: str) -> str | None:
    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", user_message)
    if m:
        return m.group(0)
    msg = user_message.lower()
    for name, num in _MONTH_NAMES.items():
        pat = rf"\b{name}\s+(20\d{{2}})\b"
        hit = re.search(pat, msg)
        if hit:
            return f"{hit.group(1)}-{num}"
    return None


def _resolve_month_from_message(conn: sqlite3.Connection, user_message: str) -> str | None:
    explicit = _parse_month_from_message(user_message)
    if explicit:
        return explicit
    msg = user_message.lower()
    if any(p in msg for p in ("last month", "latest month", "most recent month", "previous month")):
        overview = available_months(conn)
        full = overview.get("full_months") or []
        if full:
            return full[0]
        months = overview.get("months") or []
        if months:
            latest = months[0]
            return latest["month"] if isinstance(latest, dict) else latest
    return None


def _parse_limit_from_message(user_message: str, *, default: int = 100) -> int:
    msg = user_message.lower()
    patterns = (
        r"\bshow\s+me\s+(\d{1,3})\b",
        r"\b(?:top|first|last|list)\s+(\d{1,3})\b",
        r"\b(\d{1,3})\s+of\b",
        r"\b(\d{1,3})\s+(?:transactions?|rows?|results?)\b",
        r"\blimit\s+(\d{1,3})\b",
    )
    for pat in patterns:
        hit = re.search(pat, msg)
        if hit:
            n = int(hit.group(1))
            if 1 <= n <= 500:
                return n
    return default


def _find_category_in_message(conn: sqlite3.Connection, user_message: str) -> str | None:
    msg = user_message.lower()
    rows = conn.execute(
        """
        SELECT DISTINCT ai_category FROM transactions
        WHERE ai_category IS NOT NULL AND TRIM(ai_category) != ''
        ORDER BY LENGTH(ai_category) DESC
        """
    ).fetchall()
    for row in rows:
        cat = str(row["ai_category"] or "").strip()
        if cat and cat.lower() in msg:
            return cat
    return None


def _answer_from_trace(trace: list[dict[str, Any]]) -> str | None:
    for entry in reversed(trace):
        tool = entry.get("tool")
        result = entry.get("result") or {}
        if result.get("error"):
            continue
        if tool == "flow_totals_by_month":
            return _format_flow_totals(result)
        if tool == "month_total" and "total" in result:
            m = result.get("month", "")
            flow = result.get("flow_type", "Total")
            return (
                f"**{flow} for {m}:** ${result['total']:,.2f} "
                f"({result.get('transaction_count', 0)} transactions)"
            )
        if tool == "available_months":
            full = result.get("full_months") or []
            if full:
                return f"Full months in database: {', '.join(full)}"
        if tool == "list_transactions" and "transactions" in result:
            return _format_transaction_list(result)
        if tool == "top_categories" and isinstance(result, list):
            month = ""
            for entry in trace:
                if entry.get("tool") == "top_categories":
                    month = str((entry.get("args") or {}).get("month") or "")
                    break
            return _format_top_categories(result, month)
        if tool == "list_custom_reports":
            reports = result if isinstance(result, list) else []
            return _format_custom_reports_list(reports)
        if tool == "run_custom_report" and "rows" in result:
            return _format_custom_report_run(result)
        if tool == "save_custom_report" and result.get("report_id"):
            return (
                f"Saved custom report **{result.get('name')}** "
                f"(id `{result.get('report_id')}`). "
                f"Parameters: {', '.join(result.get('parameters') or []) or 'none'}. "
                "Ask me to run it for any month."
            )
    return None


def _detect_flow(user_message: str) -> str | None:
    msg = user_message.lower()
    if any(w in msg for w in ("income", "payroll", "salary", "paycheck")):
        return "Income"
    if any(w in msg for w in ("spend", "expense", "spending", "outflow")):
        return "Expense"
    return None


def _is_multi_month_question(user_message: str) -> bool:
    msg = user_message.lower()
    return any(
        p in msg
        for p in (
            "each month",
            "all months",
            "every month",
            "by month",
            "per month",
            "monthly income",
            "monthly spend",
            "monthly expense",
        )
    )


def _maybe_list_transactions_answer(
    conn: sqlite3.Connection, user_message: str
) -> dict[str, Any] | None:
    msg = user_message.lower()
    if not any(
        w in msg
        for w in (
            "transaction",
            "transactions",
            "show all",
            "list all",
            "show me",
            "table format",
            "in a table",
        )
    ):
        return None
    month = _resolve_month_from_message(conn, user_message)
    if not month:
        return None
    category = _find_category_in_message(conn, user_message)
    flow = _detect_flow(user_message) or "Expense"
    limit = _parse_limit_from_message(user_message)
    args: dict[str, Any] = {"month": month, "flow": flow, "limit": limit}
    if category:
        args["category"] = category
    result = run_tool(conn, "list_transactions", args)
    trace = [{"tool": "list_transactions", "args": args, "result": result}]
    return _chat_payload(_format_transaction_list(result), trace)


def _maybe_list_custom_reports_answer(
    conn: sqlite3.Connection, user_message: str
) -> dict[str, Any] | None:
    msg = user_message.lower()
    if not any(
        p in msg
        for p in (
            "custom report",
            "saved report",
            "my reports",
            "saved queries",
            "custom queries",
        )
    ):
        return None
    if not any(w in msg for w in ("list", "show", "what", "all", "my")):
        return None
    result = run_tool(conn, "list_custom_reports", {})
    trace = [{"tool": "list_custom_reports", "args": {}, "result": result}]
    return _chat_payload(_format_custom_reports_list(result), trace)


def _maybe_direct_answer(conn: sqlite3.Connection, user_message: str) -> dict[str, Any] | None:
    reports = _maybe_list_custom_reports_answer(conn, user_message)
    if reports:
        return reports

    listed = _maybe_list_transactions_answer(conn, user_message)
    if listed:
        return listed

    if not _is_multi_month_question(user_message):
        return None
    flow = _detect_flow(user_message)
    if not flow:
        return None
    args = {"flow": flow, "full_months_only": True}
    result = run_tool(conn, "flow_totals_by_month", args)
    trace = [{"tool": "flow_totals_by_month", "args": args, "result": result}]
    return _chat_payload(_format_flow_totals(result), trace)


def _inbox_csv_context() -> str:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p.name for p in INBOX_DIR.glob("*.csv"))
    if not files:
        return f"CSV inbox ({INBOX_DIR}): (empty — copy bank export here, then Run processing)"
    return f"CSV inbox ({INBOX_DIR}): {', '.join(files)}"


def _db_month_context(conn: sqlite3.Connection) -> str:
    overview = available_months(conn)
    full = overview.get("full_months") or []
    if not full:
        return "Processed full months in DB: (none yet — use Run processing on inbox CSV)"
    parts = []
    for m in overview.get("months") or []:
        if m.get("full_month"):
            parts.append(f"{m['month']} ({m['transaction_count']} tx)")
    partial = [
        m for m in (overview.get("months") or []) if not m.get("full_month") and m.get("transaction_count")
    ]
    text = f"Processed full months in DB: {', '.join(parts)}"
    if partial:
        spill = ", ".join(
            f"{m['month']} ({m['transaction_count']} tx, partial)" for m in partial
        )
        text += f". Partial/spillover only: {spill}"
    return text


def _finalize_answer(
    conn: sqlite3.Connection,
    messages: list[dict[str, str]],
    trace: list[dict[str, Any]],
    raw: str,
) -> str:
    action = _parse_action(raw)
    if "answer" in action and not action.get("tool"):
        return str(action["answer"])

    if action.get("tool"):
        tool_name = str(action["tool"])
        args = action.get("args") or {}
        try:
            result = run_tool(conn, tool_name, args)
            trace.append({"tool": tool_name, "args": args, "result": result})
            if tool_name == "flow_totals_by_month":
                return _format_flow_totals(result)
            if tool_name == "month_total" and "total" in result:
                m = result.get("month", "")
                flow = result.get("flow_type", "Total")
                return (
                    f"**{flow} for {m}:** ${result['total']:,.2f} "
                    f"({result.get('transaction_count', 0)} transactions)"
                )
            if tool_name == "list_transactions" and "transactions" in result:
                return _format_transaction_list(result)
            if tool_name == "list_custom_reports":
                return _format_custom_reports_list(result)
            if tool_name == "run_custom_report" and "rows" in result:
                return _format_custom_report_run(result)
            if tool_name == "save_custom_report" and result.get("report_id"):
                return _answer_from_trace(trace) or str(result)
        except Exception as exc:
            trace.append({"tool": tool_name, "args": args, "result": {"error": str(exc)}})

    from_trace = _answer_from_trace(trace)
    if from_trace:
        return from_trace

    summary = chat_completion(
        messages
        + [
            {
                "role": "user",
                "content": (
                    "Summarize the tool results above for the user in plain language. "
                    'Respond with ONLY {"answer": "..."} — no more tool calls.'
                ),
            }
        ]
    )
    parsed = _parse_action(summary)
    if "answer" in parsed:
        return str(parsed["answer"])
    return summary


def list_chat_history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT role, content, tool_trace, created_at FROM chat_messages ORDER BY id ASC"
    ).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        trace: list[dict[str, Any]] = []
        if row["tool_trace"]:
            try:
                parsed = json.loads(row["tool_trace"])
                if isinstance(parsed, list):
                    trace = parsed
            except json.JSONDecodeError:
                pass
        display = display_from_trace(trace)
        item: dict[str, Any] = {
            "role": row["role"],
            "content": row["content"],
            "tool_trace": trace,
            "created_at": row["created_at"],
        }
        if display:
            item["display"] = display
        messages.append(item)
    return messages


def chat(conn: sqlite3.Connection, user_message: str, *, max_tool_rounds: int = 6) -> dict[str, Any]:
    direct = _maybe_direct_answer(conn, user_message)
    if direct:
        _save_message(conn, "user", user_message)
        _save_message(
            conn,
            "assistant",
            direct["answer"],
            json.dumps(direct["tool_trace"]) if direct.get("tool_trace") else None,
        )
        return direct

    tools_desc = json.dumps(TOOL_DEFINITIONS, indent=2)
    system = CHAT_SYSTEM.format(
        data_cheatsheet=_load_data_cheatsheet(),
        tools=tools_desc,
    )
    context = "\n".join([_inbox_csv_context(), _db_month_context(conn)])

    _save_message(conn, "user", user_message)
    trace: list[dict[str, Any]] = []
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{context}\n\nUser: {user_message}"},
    ]

    for _ in range(max_tool_rounds + 1):
        raw = chat_completion(messages)
        action = _parse_action(raw)

        if "answer" in action and not action.get("tool"):
            answer = str(action["answer"])
            _save_message(conn, "assistant", answer, json.dumps(trace) if trace else None)
            return _chat_payload(answer, trace)

        tool_name = action.get("tool")
        if not tool_name:
            answer = _finalize_answer(conn, messages, trace, raw)
            _save_message(conn, "assistant", answer, json.dumps(trace) if trace else None)
            return _chat_payload(answer, trace)

        args = action.get("args") or {}
        try:
            result = run_tool(conn, tool_name, args)
        except Exception as exc:
            result = {"error": str(exc)}

        trace.append({"tool": tool_name, "args": args, "result": result})

        if tool_name == "flow_totals_by_month" and "months" in result:
            answer = _format_flow_totals(result)
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        if tool_name == "top_categories" and isinstance(result, list):
            month = str(args.get("month") or "")
            answer = _format_top_categories(result, month)
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        if tool_name == "list_transactions" and "transactions" in result:
            answer = _format_transaction_list(result)
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        if tool_name == "list_custom_reports":
            answer = _format_custom_reports_list(result)
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        if tool_name == "run_custom_report" and isinstance(result, dict) and "rows" in result:
            answer = _format_custom_report_run(result)
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        if tool_name == "save_custom_report" and isinstance(result, dict) and result.get("report_id"):
            answer = _answer_from_trace(trace) or json.dumps(result)
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        messages.append({"role": "assistant", "content": json.dumps(action)})
        messages.append(
            {
                "role": "user",
                "content": f"Tool result for {tool_name}:\n{json.dumps(result, indent=2)}",
            }
        )

    answer = _finalize_answer(conn, messages, trace, "")
    _save_message(conn, "assistant", answer, json.dumps(trace))
    return _chat_payload(answer, trace)
