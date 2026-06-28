"""Unified inbox: aggregate AI proposals awaiting user action."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from webapp.services.categorize import list_review_items
from webapp.services.classification_audit import list_findings, reconcile_open_findings


def list_pending_confirmations(
    conn: sqlite3.Connection,
    *,
    include_review_queue: bool = True,
    review_limit: int = 50,
) -> dict[str, Any]:
    """Return grouped pending items for the Agent Workspace inbox."""
    reconcile_open_findings(conn)
    items: list[dict[str, Any]] = []

    for finding in list_findings(conn, status="open"):
        items.append(
            {
                "confirmation_id": f"audit-{finding['id']}",
                "confirmation_type": "quality_flag",
                "source": "classification_audit",
                "title": finding.get("merchant_key") or "Unknown merchant",
                "summary": _audit_summary(finding),
                "proposal": {
                    "suggested_category": finding.get("suggested_category"),
                    "suggested_sub": finding.get("suggested_sub"),
                    "production_category": finding.get("production_category"),
                    "production_sub": finding.get("production_sub"),
                    "rationale": finding.get("rationale"),
                },
                "entity_key": str(finding.get("merchant_key") or ""),
                "reference_id": finding.get("id"),
                "priority": 80,
            }
        )

    insight_rows = conn.execute(
        """
        SELECT *
        FROM ai_insights
        WHERE status = 'open'
        ORDER BY confidence DESC, created_at DESC
        LIMIT 50
        """
    ).fetchall()
    for row in insight_rows:
        insight = dict(row)
        proposal = {}
        raw = insight.get("proposal_json")
        if isinstance(raw, str) and raw:
            try:
                proposal = json.loads(raw)
            except json.JSONDecodeError:
                proposal = {}
        ctype = str(insight.get("insight_type") or "pattern_insight")
        items.append(
            {
                "confirmation_id": f"insight-{insight['id']}",
                "confirmation_type": ctype,
                "source": "learning_agent",
                "title": insight.get("title") or insight.get("merchant_key") or "AI insight",
                "summary": insight.get("pattern_summary") or insight.get("rationale") or "",
                "proposal": proposal,
                "entity_key": str(insight.get("merchant_key") or ""),
                "reference_id": insight.get("id"),
                "priority": 70,
            }
        )

    if include_review_queue:
        review_items = list_review_items(conn)[:review_limit]
        for item in review_items:
            mk = str(item.get("merchant_key") or "")
            pending = int(item.get("transaction_count") or item.get("pending_count") or 0)
            items.append(
                {
                    "confirmation_id": f"review-{mk}",
                    "confirmation_type": "merchant_label",
                    "source": "confirm_categories",
                    "title": mk,
                    "summary": f"{pending} transaction(s) need label confirmation",
                    "proposal": {
                        "ai_category": item.get("ai_category"),
                        "ai_sub_category": item.get("ai_sub_category"),
                        "expense_type": item.get("expense_type"),
                        "flow_type": item.get("flow_type"),
                        "classification": item.get("classification"),
                    },
                    "entity_key": mk,
                    "reference_id": mk,
                    "transaction_id": item.get("transaction_id"),
                    "priority": 90,
                }
            )

    items.sort(key=lambda x: (-int(x.get("priority") or 0), x.get("title") or ""))
    return {
        "count": len(items),
        "items": items,
        "by_type": _count_by_type(items),
    }


def _audit_summary(finding: dict[str, Any]) -> str:
    prod = finding.get("production_category") or "—"
    sugg = finding.get("suggested_category") or "—"
    return f"At audit: {sugg} · Current in DB: {prod}"


def _count_by_type(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        t = str(item.get("confirmation_type") or "other")
        counts[t] = counts.get(t, 0) + 1
    return counts
