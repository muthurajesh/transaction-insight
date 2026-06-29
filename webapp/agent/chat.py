from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

from webapp.agent.answer_format import (
    coalesce_answer_with_display,
    query_sql_prose_fallback,
)
from webapp.agent.display import display_from_trace
from webapp.agent.sql_intent import (
    merchant_query_hints,
    needs_database_answer,
    trace_has_successful_query,
    validate_query_sql,
)
from webapp.agent.tools import chat_tool_definitions, available_months, run_tool
from webapp.agent.workspace_proposals import workspace_items_from_trace
from webapp.agent.chat_history import list_llm_chat_context
from webapp.agent.chat_context import (
    estimate_messages_tokens,
    estimate_tokens,
    get_context_token_limit,
)
from webapp.services.cadence_insights import (
    cadence_proposal_from_trace,
    find_merchant_key_from_text,
    propose_cadence,
)
from webapp.config import CHAT_HISTORY_MESSAGES, INBOX_DIR, UI_SHOW_CADENCE
from webapp.services.llm import chat_completion, extract_json
from webapp.services.pending_confirmations import filter_cadence_confirmations

_DATA_CHEATSHEET_PATH = Path(__file__).resolve().parent / "DATA_CHEATSHEET.md"

CHAT_SYSTEM = """You are a personal finance assistant with read-only SQL access to a local SQLite database.
**Never invent dollar amounts.** Every number in your answer must come from a tool result.

## Conversation style
The user speaks in **plain English** (like ChatGPT). They do not know table or column names.
- **Never** ask them to use SQL jargon (`merchant_key`, `budget_month`, `source_file`, etc.).
- **Never** show SQL in your answer unless they explicitly ask how you queried.
- Translate their intent internally, call `query_sql`, then reply conversationally with results.
- Use **Query hints** in the message context when present — they map natural language to correct filters.
- **Prior turns** in the thread are real conversation history. Short follow-ups ("yes", "do that", "draft it") refer to the preceding exchange — continue that task without asking the user to repeat themselves.

### Natural language → database (internal translation)
| User says | You query |
|-----------|-----------|
| Capital One, Amazon, a merchant/payee name | `merchant_key` (= or LIKE) — **never** `source_file` |
| last 3 months / recent months | `budget_month IN (...)` from Query hints or Recent full months |
| spending / expenses / how much did I spend | `flow_type = 'Expense' AND amount < 0` |
| load/show/list transactions (no "spending") | include all `flow_type` rows for that merchant unless they said expenses only |
| categories / dining / insurance | `ai_category` (match labels from context) |

## Primary tool: `query_sql`
Use **`query_sql`** for almost all questions — spending, categories, merchants, comparisons, averages, trends, lists.
You may call `query_sql` more than once to refine. Write SELECT against `transactions`.

If validation rejects your SQL, read the error, fix the query, and call `query_sql` again. Do not answer from a wrong or combined query.

### SQL essentials (SQLite)
- Monthly rollups: `budget_month` (`YYYY-MM`), not `date`.
- Expenses: `flow_type = 'Expense' AND amount < 0`; totals use `SUM(-amount)`.
- Income: `flow_type = 'Income'`.
- Category filter: `ai_category` — map natural language to values from context.
- Merchant / payee / bank name: `merchant_key = '...'` or `LIKE` — **not** `source_file`.
- Last N full months: use Query hints or "Recent full months" from context in `budget_month IN (...)`.

### Compare two months by category (side-by-side — never combine)
Pivot example (preferred for compare):
```sql
SELECT ai_category,
  ROUND(SUM(CASE WHEN budget_month='2026-04' THEN -amount ELSE 0 END), 2) AS apr_2026,
  ROUND(SUM(CASE WHEN budget_month='2026-05' THEN -amount ELSE 0 END), 2) AS may_2026
FROM transactions
WHERE flow_type='Expense' AND amount<0
  AND budget_month IN ('2026-04','2026-05')
GROUP BY ai_category
ORDER BY (apr_2026 + may_2026) DESC
```

Long format (also valid):
```sql
SELECT ai_category, budget_month, ROUND(SUM(-amount), 2) AS spend
FROM transactions
WHERE flow_type='Expense' AND amount<0
  AND budget_month IN ('2026-04','2026-05')
GROUP BY ai_category, budget_month
ORDER BY ai_category, budget_month
```

**Wrong for compare:** `GROUP BY ai_category` only with `budget_month IN (...)` — that merges months.

### Average over N months
```sql
SELECT budget_month, ROUND(SUM(-amount), 2) AS spend
FROM transactions
WHERE flow_type='Expense' AND amount<0
  AND ai_category = '<category from user question>'
  AND budget_month IN ('2026-05','2026-04','2026-03')
GROUP BY budget_month
```

## Other tools (special cases only)
{cadence_tool_line}- `propose_custom_rule` — draft plain-English if/then rule; preview matches; user confirms before save.
- `list_open_insights` — open Learning Agent proposals in the Workspace inbox.
- `run_decision_analysis` — run Decision Analyst; new insights appear in inbox (user reviews there).
- `accept_insight` / `reject_insight` — only when the user explicitly asks to accept or dismiss an insight by id.
- `list_custom_reports` / `run_custom_report` — saved reports (prompt + SQL). Use to rerun or as baseline when tweaking.

## Decision memory (meta-analysis)
You may query `decision_events`, `ai_insights`, `pipeline_custom_rules`, `category_rules`, `description_lookup` via `query_sql`.
Use these when the user asks about their correction patterns, pending AI proposals, or saved rules — not for routine spend totals.
Prefer `list_open_insights` or `run_decision_analysis` over inventing patterns from memory.

## Custom reports (conversational workflow)
Users build reports in chat over multiple turns, then save via the **Save as report** button or by asking to save.
Each saved report stores:
- **report_prompt** — distilled instructions (filters, exclusions, view, output shape)
- **sql_template** — validated SELECT with `:month`, `:months`, `:limit`, `:category`, `:expense_view`

When the user asks to **run**, **load**, or **tweak** a saved report:
1. Call `list_custom_reports` if you need the exact name.
2. Call `run_custom_report` with `report` and `params` (e.g. `{{"month": "2026-05"}}` or `{{"months": ["2026-03","2026-04","2026-05"]}}`).
3. For **tweak** (one-off): run the saved report first, then `query_sql` with the same logic plus the user's change. Do not overwrite the saved report unless they ask to **update** or **save as new version**.

For **rename** or **delete**, tell the user to use the report actions on a saved report message, or manage via Help examples.

When saving is discussed, summarize: goal, filters, parameters exposed, and whether a chart or grand total is desired.

Do **not** guess totals. Call `query_sql` before `{{"answer": "..."}}`.

## Read-only policy
- `query_sql` allows SELECT on: transactions, merchant_labels{cadence_tables_clause}, custom_reports, decision_events, ai_insights, pipeline_custom_rules, category_rules, description_lookup.
- Chat cannot write transaction data directly — use propose_* tools or accept/reject insight when the user confirms.

## Data model cheat sheet
{data_cheatsheet}

Available tools:
{tools}

To call a tool, respond with ONLY:
{{"tool": "<name>", "args": {{ ... }}}}

When query results are validated and sufficient, respond with ONLY:
{{"answer": "<1–3 sentences summarizing results — amounts, filters, time range. **Do NOT include markdown tables**; the UI renders query rows as an interactive table below your text.>"}}
"""


def _chat_payload(
    answer: str,
    trace: list[dict[str, Any]] | None = None,
    *,
    context_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = trace or []
    display = display_from_trace(trace)
    if display:
        answer = coalesce_answer_with_display(answer, display)
    payload: dict[str, Any] = {"answer": answer, "tool_trace": trace}
    if display:
        payload["display"] = display
    proposal = cadence_proposal_from_trace(trace)
    if proposal and UI_SHOW_CADENCE:
        payload["cadence_proposal"] = proposal
    workspace_items = filter_cadence_confirmations(
        workspace_items_from_trace(trace),
        include_cadence=UI_SHOW_CADENCE,
    )
    if workspace_items:
        payload["workspace_proposals"] = workspace_items
    if context_usage:
        payload["context_usage"] = context_usage
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
    if tool_name == "propose_custom_rule" and isinstance(result, dict) and result.get("rule_text"):
        return _format_custom_rule_proposal(result)
    if tool_name == "run_decision_analysis" and isinstance(result, dict):
        return _format_decision_analysis_result(result)
    if tool_name in ("accept_insight", "reject_insight") and isinstance(result, dict):
        status = result.get("status") or "updated"
        return f"Insight #{result.get('id')} marked **{status}**."
    return None


def _format_custom_rule_proposal(result: dict[str, Any]) -> str:
    rule_text = str(result.get("rule_text") or "").strip()
    preview = result.get("preview") or {}
    lines = [f"**Custom rule draft:** {rule_text}"]
    if preview.get("compile_error"):
        lines.append(f"\nCompile issue: {preview.get('compile_error')}")
    else:
        total = int(preview.get("total") or 0)
        lines.append(f"\nPreview: {total} matching transaction(s).")
    if result.get("existing_similar_rule"):
        lines.append(
            f"\n_Similar rule already exists ({result.get('existing_rule_status') or 'saved'})._"
        )
    elif result.get("recommend_save_rule"):
        lines.append("\n_Use **Review in Workspace** below to save and apply._")
    return "\n".join(lines)


def _format_decision_analysis_result(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return "Decision analysis skipped (Learning Agent disabled in config)."
    inserted = int(result.get("insights_inserted") or 0)
    events = int(result.get("events_analyzed") or 0)
    lines = [
        f"Analyzed **{events}** decision event(s); **{inserted}** new insight(s) added to the inbox."
    ]
    for ins in (result.get("inserted_insights") or [])[:5]:
        title = ins.get("title") or ins.get("pattern_summary") or "Insight"
        lines.append(f"- {title}")
    if inserted:
        lines.append("\n_Use **Review in Workspace** below or the pending panel to approve._")
    elif not result.get("error"):
        lines.append("\n_No new patterns met the evidence threshold._")
    return "\n".join(lines)


def _format_open_insights(result: dict[str, Any]) -> str:
    insights = result.get("insights") or []
    if not insights:
        return "No open AI insights in the inbox."
    lines = [f"**{len(insights)} open insight(s):**", ""]
    for ins in insights:
        lines.append(
            f"- #{ins.get('id')} **{ins.get('title') or 'Insight'}** — "
            f"{ins.get('pattern_summary') or ins.get('rationale') or ''}"
        )
    return "\n".join(lines)


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
    sql = str(result.get("sql") or "").strip()
    if not rows:
        body = f"{title}\n\nNo rows returned."
        if sql:
            body += f"\n\n```sql\n{sql}\n```"
        return body
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
    if sql:
        lines.append(f"\n<details><summary>SQL used</summary>\n\n```sql\n{sql}\n```\n</details>")
    return "\n".join(lines)


def _format_custom_reports_list(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return (
            "You have no saved custom reports yet. Explore data in chat, then use "
            "**Save as report** on a table result, or ask me to help build one."
        )
    lines = [
        "**Saved custom reports**\n",
        "| Name | Ver | Parameters | Description |",
        "| --- | --- | --- | --- |",
    ]
    for r in reports:
        params = ", ".join(r.get("parameters") or []) or "—"
        desc = (r.get("description") or r.get("report_prompt") or r.get("original_question") or "—")[:80]
        ver = r.get("version") or 1
        lines.append(f"| {r.get('name', '')} | v{ver} | {params} | {desc} |")
    lines.append(
        "\n**Run:** ask to run a report by name with a month, e.g. "
        "`run_custom_report` with `params: {{\"month\": \"2026-05\"}}`."
        "\n**Tweak:** load the report, then ask for one-off changes without saving."
        "\n**New version:** ask to save as a new version after tweaking."
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
        if result.get("error") or result.get("validation_rejected"):
            continue
        if tool == "query_sql" and "rows" in result:
            return query_sql_prose_fallback(result)
        if tool == "run_custom_report" and "rows" in result:
            count = int(result.get("row_count") or len(result.get("rows") or []))
            name = result.get("name") or "Custom report"
            return f"**{name}** — {count} row(s). See table below."
        if tool == "propose_cadence_rule" and result.get("insight"):
            return _format_cadence_proposal(result)
        if tool == "propose_custom_rule" and result.get("rule_text"):
            return _format_custom_rule_proposal(result)
        if tool == "run_decision_analysis":
            return _format_decision_analysis_result(result)
        if tool == "list_open_insights":
            return _format_open_insights(result)
        if tool == "list_custom_reports":
            reports = result if isinstance(result, list) else []
            return _format_custom_reports_list(reports)
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
    if not UI_SHOW_CADENCE:
        return None
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
    result = run_tool(conn, "list_custom_reports", {}, chat_mode=True)
    trace = [{"tool": "list_custom_reports", "args": {}, "result": result}]
    return _chat_payload(_format_custom_reports_list(result), trace)


def _report_context_for_message(
    conn: sqlite3.Connection, user_message: str
) -> str | None:
    """Inject saved report prompt when user references a report by name."""
    from webapp.services import custom_reports as saved_reports

    msg = user_message.lower()
    if not any(
        k in msg
        for k in (
            "custom report",
            "saved report",
            "run report",
            "load report",
            "tweak report",
            "open report",
        )
    ):
        return None

    reports = saved_reports.list_custom_reports(conn)
    if not reports:
        return None

    matched: dict[str, Any] | None = None
    for r in reports:
        name = str(r.get("name") or "").strip()
        if name and name.lower() in msg:
            matched = r
            break
    if not matched and len(reports) == 1:
        matched = reports[0]

    if not matched:
        return None

    prompt = (matched.get("report_prompt") or "").strip()
    params = ", ".join(matched.get("parameters") or []) or "(none)"
    lines = [
        f"Saved report context — **{matched.get('name')}** (v{matched.get('version', 1)}):",
        f"Parameters: {params}",
    ]
    if prompt:
        lines.append(f"Report prompt:\n{prompt}")
    if matched.get("description"):
        lines.append(f"Description: {matched['description']}")
    lines.append(
        "When tweaking: run_custom_report first, then query_sql for one-off changes. "
        "Do not overwrite unless user asks to update or save a new version."
    )
    return "\n".join(lines)


def _assemble_chat_system(conn: sqlite3.Connection) -> str:
    tools_desc = json.dumps(chat_tool_definitions(include_cadence=UI_SHOW_CADENCE), indent=2)
    cadence_tool_line = (
        "- `propose_cadence_rule` — user explains annual/recurring charge treatment (UI confirm).\n"
        if UI_SHOW_CADENCE
        else ""
    )
    cadence_tables_clause = ", cadence_rules" if UI_SHOW_CADENCE else ""
    return CHAT_SYSTEM.format(
        data_cheatsheet=_load_data_cheatsheet(),
        tools=tools_desc,
        cadence_tool_line=cadence_tool_line,
        cadence_tables_clause=cadence_tables_clause,
    )


def _assemble_chat_db_context(conn: sqlite3.Connection, user_message: str) -> str:
    context_parts = [
        _inbox_csv_context(),
        _db_month_context(conn),
        _db_category_context(conn),
        _db_decision_context(conn),
    ]
    query_hints = merchant_query_hints(conn, user_message)
    if query_hints:
        context_parts.append(query_hints)
    report_ctx = _report_context_for_message(conn, user_message)
    if report_ctx:
        context_parts.append(report_ctx)
    return "\n".join(context_parts)


def build_chat_llm_messages(
    conn: sqlite3.Connection,
    user_message: str,
) -> list[dict[str, str]]:
    system = _assemble_chat_system(conn)
    context = _assemble_chat_db_context(conn, user_message)
    prior_turns = list_llm_chat_context(conn, limit=CHAT_HISTORY_MESSAGES)
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    messages.extend(prior_turns)
    if user_message.strip():
        messages.append({"role": "user", "content": f"{context}\n\nUser: {user_message}"})
    return messages


def estimate_chat_context_usage(
    conn: sqlite3.Connection,
    user_message: str = "",
) -> dict[str, Any]:
    """Estimate tokens for the next chat request (approximate; no model tokenizer)."""
    system = _assemble_chat_system(conn)
    context = _assemble_chat_db_context(conn, user_message)
    prior_turns = list_llm_chat_context(conn, limit=CHAT_HISTORY_MESSAGES)
    system_tokens = estimate_messages_tokens([{"role": "system", "content": system}])
    history_tokens = estimate_messages_tokens(prior_turns)
    context_tokens = estimate_tokens(context)
    next_user_tokens = 0
    if user_message.strip():
        next_user_tokens = estimate_message_tokens(
            {"role": "user", "content": f"{context}\n\nUser: {user_message}"}
        )
    total = system_tokens + history_tokens + next_user_tokens
    limit = get_context_token_limit()
    meter_tokens = history_tokens + next_user_tokens
    meter_limit = max(1024, limit - system_tokens)
    usage_percent = round(100.0 * meter_tokens / meter_limit, 1) if meter_limit else 0.0
    return {
        "total_tokens": total,
        "meter_tokens": meter_tokens,
        "meter_limit": meter_limit,
        "system_tokens": system_tokens,
        "history_tokens": history_tokens,
        "context_tokens": context_tokens,
        "next_user_tokens": next_user_tokens,
        "history_message_count": len(prior_turns),
        "context_token_limit": limit,
        "usage_percent": usage_percent,
    }


def _usage_after_turn(conn: sqlite3.Connection) -> dict[str, Any]:
    return estimate_chat_context_usage(conn, user_message="")


def _maybe_direct_answer(conn: sqlite3.Connection, user_message: str) -> dict[str, Any] | None:
    """Bypass the LLM only for UI workflow actions (cadence modal, report list)."""
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


def _db_decision_context(conn: sqlite3.Connection) -> str:
    open_n = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM ai_insights WHERE status = 'open'"
        ).fetchone()["c"]
    )
    events_n = int(
        conn.execute(
            """
            SELECT COUNT(*) AS c FROM decision_events
            WHERE datetime(created_at) >= datetime('now', '-30 days')
            """
        ).fetchone()["c"]
    )
    if open_n == 0 and events_n == 0:
        return ""
    return (
        f"Decision memory: {open_n} open AI insight(s) in inbox; "
        f"{events_n} decision event(s) in last 30 days."
    )


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
                    "Using ONLY the tool results already fetched, write a clear answer "
                    "for the user's question in 1–3 sentences. Include specific dollar amounts "
                    "and the time range. **Do NOT include markdown tables** — the UI shows "
                    "query data in an interactive table. "
                    "If the user asked to COMPARE months, describe each month separately "
                    "— never describe a combined total unless they asked for combined. "
                    'Respond with ONLY {"answer": "..."} — no more tool calls.'
                ),
            }
        ],
        caller="chat.synthesize_answer",
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
        return _chat_payload(
            direct["answer"],
            direct.get("tool_trace"),
            context_usage=_usage_after_turn(conn),
        )

    messages = build_chat_llm_messages(conn, user_message)
    _save_message(conn, "user", user_message)
    trace: list[dict[str, Any]] = []

    for _ in range(max_tool_rounds + 1):
        raw = chat_completion(messages, caller="chat.turn")
        action = _parse_action(raw)

        if "answer" in action and not action.get("tool"):
            if needs_database_answer(user_message) and not trace_has_successful_query(trace):
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You must call query_sql and use real database results before answering. "
                            "Do not guess dollar amounts."
                        ),
                    }
                )
                continue
            answer = str(action["answer"])
            _save_message(conn, "assistant", answer, json.dumps(trace) if trace else None)
            return _chat_payload(answer, trace, context_usage=_usage_after_turn(conn))

        tool_name = action.get("tool")
        if not tool_name:
            answer = _finalize_answer(conn, messages, trace, raw)
            _save_message(conn, "assistant", answer, json.dumps(trace) if trace else None)
            return _chat_payload(answer, trace, context_usage=_usage_after_turn(conn))

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
            return _chat_payload(answer, trace, context_usage=_usage_after_turn(conn))

        try:
            result = run_tool(conn, tool_name, args, chat_mode=True)
        except Exception as exc:
            result = {"error": str(exc)}

        if tool_name == "query_sql" and not result.get("error"):
            validation_err = validate_query_sql(
                user_message,
                str(result.get("sql") or args.get("sql") or ""),
                result,
                conn=conn,
            )
            if validation_err:
                result = {
                    **result,
                    "error": validation_err,
                    "validation_rejected": True,
                }

        trace.append({"tool": tool_name, "args": args, "result": result})

        immediate = _immediate_tool_answer(tool_name, result, trace)
        if immediate is not None:
            _save_message(conn, "assistant", immediate, json.dumps(trace))
            return _chat_payload(immediate, trace, context_usage=_usage_after_turn(conn))

        messages.append({"role": "assistant", "content": json.dumps(action)})
        feedback = f"Tool result for {tool_name}:\n{json.dumps(result, indent=2)}"
        if result.get("validation_rejected"):
            feedback += (
                "\n\nFix the SQL and call query_sql again. "
                "Do not answer until the query matches the user's intent."
            )
            err_text = str(result.get("error") or "")
            if "month" in err_text.lower() and "separately" in err_text.lower():
                feedback += " Show each month separately for comparisons."
            elif "merchant_key" in err_text or "source_file" in err_text:
                feedback += " Use Query hints in context for merchant_key and budget_month."
        messages.append({"role": "user", "content": feedback})

    answer = _finalize_answer(conn, messages, trace, "")
    _save_message(conn, "assistant", answer, json.dumps(trace))
    return _chat_payload(answer, trace, context_usage=_usage_after_turn(conn))
