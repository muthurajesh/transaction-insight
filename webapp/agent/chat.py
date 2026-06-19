from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from webapp.agent.display import display_from_trace
from webapp.agent.tools import CHAT_TOOL_DEFINITIONS, available_months, run_tool
from webapp.services.cadence_insights import (
    cadence_proposal_from_trace,
    find_merchant_key_from_text,
    propose_cadence,
)
from webapp.config import INBOX_DIR
from webapp.services.llm import chat_completion, extract_json

_DATA_CHEATSHEET_PATH = Path(__file__).resolve().parent / "DATA_CHEATSHEET.md"

CHAT_SYSTEM = """You are a personal finance assistant with SQL access to a local SQLite database.
Never invent dollar amounts. Pull data with tools before answering.

## How to get data
- **Always prefer `query_sql`** for spending questions — averages, filters, comparisons, "last N months", categories, merchants.
- Write SELECT against `transactions`. You may call `query_sql` multiple times to refine an answer.
- Helper tools (`month_total`, `flow_totals_by_month`, …) return coarse aggregates only — **do not** use `flow_totals_by_month` when the user asks about a category, merchant, average, or a specific month window.
- After tool results return, respond with `{{"answer": "..."}}` that directly answers the question (include dollar amounts and the time range).

## SQL patterns (SQLite)
- Monthly rollups: `GROUP BY budget_month` with `budget_month` (`YYYY-MM`), not `date`.
- Expense spend: `flow_type = 'Expense' AND amount < 0`; totals use `SUM(-amount)`.
- Income: `flow_type = 'Income'`.
- Last N full months: use the "Recent full months" list in context — take the first N months, filter `budget_month IN (...)`.
- Category filter: `ai_category IN (...)` — map natural language (eating out, groceries, gym) to the closest `ai_category` values from context.
- Average over months: sum per month, then divide by number of months in the window.
- Merchant filter: `merchant_key = '...'` or `LIKE`.

## Example — average dining spend, last 5 full months
```sql
SELECT budget_month, SUM(-amount) AS spend
FROM transactions
WHERE flow_type = 'Expense' AND amount < 0
  AND ai_category IN ('Dining', 'Restaurants', 'Restaurants/Dining', 'Food & Dining')
  AND budget_month IN ('2026-05','2026-04','2026-03','2026-02','2026-01')
GROUP BY budget_month
ORDER BY budget_month DESC
```

## Read-only policy
- Chat has **read-only** database access. You cannot create, update, or delete transactions, users, permissions, cadence rules, or saved reports.
- `query_sql` only allows SELECT on: `transactions`, `merchant_labels`, `cadence_rules`, `custom_reports`.
- `propose_cadence_rule` returns a proposal for the UI — it does **not** save until the user confirms in the app.

## Saved custom reports (read-only)
- List saved reports: `list_custom_reports`. Re-run with new inputs: `run_custom_report`.
- If the user asks to save or delete a report, explain that must be done in Settings (chat cannot modify reports).

## Expense cadence views (helper tools only)
- **cash** — raw bank outflows (default). Raw SQL `SUM(-amount)` is always cash.
- **core** / **normalized** — pass `expense_view` on helper tools when user asks for run-rate or spread annual charges.

## Expense cadence rules
- When the user explains how a merchant charge should be treated, call **`propose_cadence_rule`** with `merchant_key` and/or `hint`.

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
    payload: dict[str, Any] = {"answer": answer, "tool_trace": trace}
    if display:
        payload["display"] = display
    proposal = cadence_proposal_from_trace(trace)
    if proposal:
        payload["cadence_proposal"] = proposal
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


# Tools that return immediately (UI workflow); all other tools feed results back to the LLM.


def _immediate_tool_answer(tool_name: str, result: Any, trace: list[dict[str, Any]]) -> str | None:
    if tool_name == "propose_cadence_rule" and isinstance(result, dict) and result.get("insight"):
        return _format_cadence_proposal(result)
    return None


def _format_top_categories(
    result: list[dict[str, Any]],
    month: str = "",
    *,
    expense_view_label: str = "",
) -> str:
    if not result:
        label = f" for {month}" if month else ""
        return f"No spending categories found{label}."
    view_suffix = f" ({expense_view_label})" if expense_view_label else ""
    title = (
        f"**Top spending categories — {month}{view_suffix}**\n"
        if month
        else f"**Top spending categories{view_suffix}**\n"
    )
    lines = [title, "| Category | Spend | Transactions |", "| --- | ---: | ---: |"]
    for row in result:
        lines.append(
            f"| {row['category']} | ${row['spend']:,.2f} | {row['transaction_count']} |"
        )
    return "\n".join(lines)


def _format_flow_totals(result: dict[str, Any]) -> str:
    flow = result.get("flow_type", "Total")
    view_label = result.get("expense_view_label") or ""
    months = result.get("months") or []
    if not months:
        return f"No {flow.lower()} data for full months in the database yet."
    heading = f"**{flow} by month**"
    if view_label:
        heading += f" — {view_label}"
    heading += " (full exports only)\n"
    lines = [heading, "| Month | Total | Transactions |", "| --- | ---: | ---: |"]
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
        return "You have no saved custom reports yet. Save reports from Settings after running a query you like."
    lines = ["**Saved custom reports**\n", "| Name | Parameters | Description |", "| --- | --- | --- |"]
    for r in reports:
        params = ", ".join(r.get("parameters") or []) or "—"
        desc = (r.get("description") or r.get("original_question") or "—")[:80]
        lines.append(f"| {r.get('name', '')} | {params} | {desc} |")
    lines.append(
        "\nRe-run with: `run_custom_report` and the report name (e.g. pass `month` or `months`)."
    )
    return "\n".join(lines)


def _format_cadence_proposal(proposal: dict[str, Any]) -> str:
    lines = [str(proposal.get("insight") or "").strip()]
    amounts = proposal.get("effective_amounts") or {}
    if amounts:
        lines.append(
            "\n**Effective amounts:** "
            f"cash ${amounts.get('cash', 0):,.2f} · "
            f"core ${amounts.get('core', 0):,.2f} · "
            f"normalized ${amounts.get('normalized', 0):,.2f}/mo"
        )
    if proposal.get("recommend_save_rule"):
        lines.append("\n_Use **Review & save cadence** below to confirm._")
    elif proposal.get("existing_similar_rule"):
        lines.append(
            f"\n_Existing cadence rule: {proposal.get('existing_similar_rule')}_"
        )
    return "\n".join(line for line in lines if line)


def _format_custom_report_run(result: dict[str, Any]) -> str:
    title = result.get("name") or "Custom report"
    used = result.get("parameters_used") or {}
    if used:
        title = f"{title} ({', '.join(f'{k}={v}' for k, v in used.items())})"
    return _format_query_rows(result, title=title)


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
            view = result.get("expense_view_label") or ""
            view_part = f" ({view})" if view else ""
            return (
                f"**{flow} for {m}{view_part}:** ${result['total']:,.2f} "
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
            view_label = ""
            for entry in trace:
                if entry.get("tool") == "top_categories":
                    args = entry.get("args") or {}
                    month = str(args.get("month") or "")
                    view = str(args.get("expense_view") or "cash")
                    if view != "cash":
                        from webapp.services.expense_cadence import expense_view_label

                        view_label = expense_view_label(view)
                    break
            return _format_top_categories(result, month, expense_view_label=view_label)
        if tool == "list_custom_reports":
            reports = result if isinstance(result, list) else []
            return _format_custom_reports_list(reports)
        if tool == "run_custom_report" and "rows" in result:
            return _format_custom_report_run(result)
        if tool == "propose_cadence_rule" and result.get("insight"):
            return _format_cadence_proposal(result)
        if tool == "query_sql" and "rows" in result:
            return _format_query_rows(result, title="Query results")
    return None


def _has_cadence_intent(user_message: str) -> bool:
    msg = user_message.lower()
    return any(
        p in msg
        for p in (
            "cadence",
            "annual",
            "yearly",
            "semi-annual",
            "semi annual",
            "bi-weekly",
            "biweekly",
            "every 12",
            "every 6",
            "lump sum",
            "run-rate",
            "run rate",
            "spread monthly",
            "once a year",
            "one-time charge",
            "recurring charge",
            "normalized monthly",
        )
    )


def _maybe_cadence_propose_answer(
    conn: sqlite3.Connection, user_message: str
) -> dict[str, Any] | None:
    if not _has_cadence_intent(user_message):
        return None
    merchant_key = find_merchant_key_from_text(conn, user_message)
    if not merchant_key:
        return None
    try:
        result = propose_cadence(conn, merchant_key=merchant_key, hint=user_message)
    except ValueError:
        return None
    args = {"merchant_key": merchant_key, "hint": user_message}
    trace = [{"tool": "propose_cadence_rule", "args": args, "result": result}]
    return _chat_payload(_format_cadence_proposal(result), trace)


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
    """Bypass the LLM only for unambiguous workflow actions (cadence modal, report list)."""
    cadence = _maybe_cadence_propose_answer(conn, user_message)
    if cadence:
        return cadence
    return _maybe_list_custom_reports_answer(conn, user_message)


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
    if full:
        text += (
            f"\nRecent full months (newest first — use for 'last N months'): "
            f"{', '.join(full[:12])}"
        )
    if partial:
        spill = ", ".join(
            f"{m['month']} ({m['transaction_count']} tx, partial)" for m in partial
        )
        text += f". Partial/spillover only: {spill}"
    return text


def _db_category_context(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT DISTINCT ai_category FROM transactions
        WHERE ai_category IS NOT NULL AND TRIM(ai_category) != ''
        ORDER BY ai_category
        """
    ).fetchall()
    cats = [str(r["ai_category"]).strip() for r in rows if r["ai_category"]]
    if not cats:
        return "Categories in DB (ai_category): (none labeled yet)"
    if len(cats) > 72:
        return (
            f"Categories in DB ({len(cats)} total, ai_category): "
            f"{', '.join(cats[:72])}, …"
        )
    return f"Categories in DB (ai_category): {', '.join(cats)}"


def _synthesize_answer_from_trace(
    messages: list[dict[str, str]], trace: list[dict[str, Any]]
) -> str | None:
    if not trace:
        return None
    summary = chat_completion(
        messages
        + [
            {
                "role": "user",
                "content": (
                    "Using ONLY the tool results already fetched, write a clear markdown answer "
                    "for the user's question. Include specific dollar amounts, the time range, "
                    "and a short plain-language summary. "
                    'Respond with ONLY {"answer": "..."} — no more tool calls.'
                ),
            }
        ]
    )
    parsed = _parse_action(summary)
    if "answer" in parsed:
        return str(parsed["answer"])
    return None


def _tool_call_signature(tool_name: str, args: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"


def _errors_from_trace(trace: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for entry in trace:
        result = entry.get("result") or {}
        err = result.get("error")
        if err:
            tool = entry.get("tool", "tool")
            errors.append(f"{tool}: {err}")
    return errors


def _finalize_answer(
    conn: sqlite3.Connection,
    messages: list[dict[str, str]],
    trace: list[dict[str, Any]],
    raw: str,
) -> str:
    action = _parse_action(raw)
    if "answer" in action and not action.get("tool"):
        return str(action["answer"])

    synthesized = _synthesize_answer_from_trace(messages, trace)
    if synthesized:
        return synthesized

    from_trace = _answer_from_trace(trace)
    if from_trace:
        return from_trace

    errors = _errors_from_trace(trace)
    if errors:
        return (
            "I could not complete that question — the database query was rejected or failed:\n\n"
            + "\n".join(f"- {e}" for e in errors[:3])
        )

    text = (raw or "").strip()
    if text:
        return text
    return "I could not complete that question. Try rephrasing or narrowing the time range."


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
        proposal = cadence_proposal_from_trace(trace)
        if proposal:
            item["cadence_proposal"] = proposal
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

    tools_desc = json.dumps(CHAT_TOOL_DEFINITIONS, indent=2)
    system = CHAT_SYSTEM.format(
        data_cheatsheet=_load_data_cheatsheet(),
        tools=tools_desc,
    )
    context = "\n".join(
        [_inbox_csv_context(), _db_month_context(conn), _db_category_context(conn)]
    )

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
        signature = _tool_call_signature(tool_name, args)
        if any(
            _tool_call_signature(e.get("tool", ""), e.get("args") or {}) == signature
            for e in trace
        ):
            trace.append(
                {
                    "tool": tool_name,
                    "args": args,
                    "result": {"error": "Duplicate tool call — using prior results."},
                }
            )
            answer = _finalize_answer(conn, messages, trace, "")
            _save_message(conn, "assistant", answer, json.dumps(trace))
            return _chat_payload(answer, trace)

        try:
            result = run_tool(conn, tool_name, args)
        except Exception as exc:
            result = {"error": str(exc)}

        trace.append({"tool": tool_name, "args": args, "result": result})

        immediate = _immediate_tool_answer(tool_name, result, trace)
        if immediate is not None:
            _save_message(conn, "assistant", immediate, json.dumps(trace))
            return _chat_payload(immediate, trace)

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
