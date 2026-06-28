"""Map chat tool results to Workspace inbox confirmation items."""

from __future__ import annotations

import json
from typing import Any


def insight_to_inbox_item(insight: dict[str, Any]) -> dict[str, Any]:
    proposal = insight.get("proposal_json") or insight.get("proposal") or {}
    if isinstance(proposal, str) and proposal:
        try:
            proposal = json.loads(proposal)
        except json.JSONDecodeError:
            proposal = {}
    return {
        "confirmation_id": f"insight-{insight.get('id')}",
        "confirmation_type": str(insight.get("insight_type") or "pattern_insight"),
        "source": "learning_agent",
        "title": insight.get("title") or insight.get("merchant_key") or "AI insight",
        "summary": insight.get("pattern_summary") or insight.get("rationale") or "",
        "proposal": proposal if isinstance(proposal, dict) else {},
        "entity_key": str(insight.get("merchant_key") or ""),
        "reference_id": insight.get("id"),
        "priority": 70,
    }


def custom_rule_to_inbox_item(result: dict[str, Any]) -> dict[str, Any]:
    preview = result.get("preview") or {}
    return {
        "confirmation_id": "chat-custom-rule",
        "confirmation_type": "custom_rule",
        "source": "chat",
        "title": "Custom rule proposal",
        "summary": str(result.get("rule_text") or "").strip(),
        "proposal": {
            "rule_text": result.get("rule_text"),
            "match_count": int(preview.get("total") or 0),
            "compile_error": preview.get("compile_error"),
            "existing_similar_rule": result.get("existing_similar_rule"),
        },
        "entity_key": "",
        "reference_id": None,
        "priority": 75,
    }


def workspace_items_from_trace(trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Build unified inbox-shaped items from chat tool trace (most recent first)."""
    if not trace:
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in reversed(trace):
        tool = entry.get("tool")
        result = entry.get("result") or {}
        if result.get("error"):
            continue

        if tool == "propose_custom_rule" and result.get("rule_text"):
            key = f"rule:{result.get('rule_text')}"
            if key not in seen and result.get("recommend_save_rule"):
                seen.add(key)
                items.append(custom_rule_to_inbox_item(result))

        if tool == "run_decision_analysis":
            for ins in result.get("inserted_insights") or []:
                key = f"insight:{ins.get('id')}"
                if ins.get("id") and key not in seen:
                    seen.add(key)
                    items.append(insight_to_inbox_item(ins))

    return items
