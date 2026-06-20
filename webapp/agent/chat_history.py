"""Chat message persistence helpers — list, paginate, export."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from webapp.agent.display import display_from_trace


def _cadence_proposal_from_trace(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        from webapp.services.cadence_insights import cadence_proposal_from_trace

        return cadence_proposal_from_trace(trace)
    except Exception:
        return None


def _parse_tool_trace(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    trace = _parse_tool_trace(row["tool_trace"])
    display = display_from_trace(trace)
    item: dict[str, Any] = {
        "id": int(row["id"]),
        "role": row["role"],
        "content": row["content"],
        "tool_trace": trace,
        "created_at": row["created_at"],
    }
    if display:
        item["display"] = display
    proposal = _cadence_proposal_from_trace(trace)
    if proposal:
        item["cadence_proposal"] = proposal
    return item


def list_chat_history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, role, content, tool_trace, created_at FROM chat_messages ORDER BY id ASC"
    ).fetchall()
    return [row_to_message(row) for row in rows]


def list_chat_history_page(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    limit: int = 10,
    order: str = "desc",
) -> dict[str, Any]:
    limit = max(1, min(100, int(limit)))
    page = max(1, int(page))
    order_sql = "DESC" if str(order).lower() == "desc" else "ASC"

    total = int(conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0])
    pages = max(1, (total + limit - 1) // limit) if total else 1
    if page > pages:
        page = pages
    offset = (page - 1) * limit

    rows = conn.execute(
        f"""
        SELECT id, role, content, tool_trace, created_at
        FROM chat_messages
        ORDER BY id {order_sql}
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()

    return {
        "messages": [row_to_message(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "order": "desc" if order_sql == "DESC" else "asc",
    }


def _export_filename(ext: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"chat-history-{stamp}.{ext}"


def export_chat_history_json(conn: sqlite3.Connection) -> tuple[str, str, str]:
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "message_count": 0,
        "messages": list_chat_history(conn),
    }
    payload["message_count"] = len(payload["messages"])
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return body, "application/json; charset=utf-8", _export_filename("json")


def export_chat_history_markdown(conn: sqlite3.Connection) -> tuple[str, str, str]:
    messages = list_chat_history(conn)
    lines = [
        "# Transaction Insight — Chat history",
        "",
        f"_Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{len(messages)} message(s)_",
        "",
    ]
    for msg in messages:
        role = str(msg.get("role") or "unknown").title()
        ts = str(msg.get("created_at") or "")[:19].replace("T", " ")
        lines.append(f"## {role} · {ts}")
        lines.append("")
        lines.append(str(msg.get("content") or "").strip())
        lines.append("")
        trace = msg.get("tool_trace") or []
        if trace:
            lines.append("<details><summary>Tools used</summary>")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(trace, indent=2)[:8000])
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    body = "\n".join(lines).strip() + "\n"
    return body, "text/markdown; charset=utf-8", _export_filename("md")


def export_chat_history_csv(conn: sqlite3.Connection) -> tuple[str, str, str]:
    messages = list_chat_history(conn)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "role", "content", "tool_count"])
    for msg in messages:
        writer.writerow(
            [
                msg.get("id", ""),
                msg.get("created_at", ""),
                msg.get("role", ""),
                str(msg.get("content") or "").replace("\r\n", "\n"),
                len(msg.get("tool_trace") or []),
            ]
        )
    body = buf.getvalue()
    return body, "text/csv; charset=utf-8", _export_filename("csv")


def export_chat_history(
    conn: sqlite3.Connection,
    fmt: str,
) -> tuple[str, str, str]:
    key = (fmt or "json").strip().lower()
    if key == "markdown" or key == "md":
        return export_chat_history_markdown(conn)
    if key == "csv":
        return export_chat_history_csv(conn)
    if key == "json":
        return export_chat_history_json(conn)
    raise ValueError(f"Unsupported export format: {fmt}. Use json, markdown, or csv.")
