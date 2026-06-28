"""Append-only log of user responses to AI proposals (decision memory)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def log_decision_event(
    conn: sqlite3.Connection,
    *,
    source: str,
    entity_type: str,
    entity_key: str,
    action: str,
    ai_proposal: dict[str, Any] | None = None,
    user_outcome: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> int:
    """Record a HITL decision. Returns new row id."""
    cur = conn.execute(
        """
        INSERT INTO decision_events (
            source, entity_type, entity_key, action,
            ai_proposal_json, user_outcome_json, context_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source.strip(),
            entity_type.strip(),
            entity_key.strip(),
            action.strip(),
            _json_dumps(ai_proposal or {}),
            _json_dumps(user_outcome or {}),
            _json_dumps(context or {}),
            _utc_now(),
        ),
    )
    return int(cur.lastrowid or 0)


def labels_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("ai_category", "ai_sub_category", "expense_type", "flow_type", "classification")
    for key in keys:
        if (str(a.get(key) or "").strip().lower()) != (str(b.get(key) or "").strip().lower()):
            return False
    return True


def infer_confirm_action(
    suggested: dict[str, Any] | None,
    final: dict[str, Any],
) -> str:
    if not suggested:
        return "accepted"
    return "accepted" if labels_match(suggested, final) else "edited"


def list_recent_events(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    limit: int = 500,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM decision_events
        WHERE datetime(created_at) >= datetime('now', ?)
        ORDER BY id DESC
        LIMIT ?
        """,
        (f"-{int(days)} days", limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_accepted_insights_for_prompt(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT insight_type, title, pattern_summary, proposal_json, merchant_key
        FROM ai_insights
        WHERE status = 'accepted'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("ai_proposal_json", "user_outcome_json", "context_json"):
        raw = d.get(key)
        if isinstance(raw, str) and raw:
            try:
                d[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                d[key.replace("_json", "")] = {}
        else:
            d[key.replace("_json", "")] = {}
    return d
