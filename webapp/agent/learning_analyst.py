"""LLM Decision Analyst — query_sql tool loop for the Learning Agent."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from webapp.agent.tools import run_tool
from webapp.llm.prompts import learning_agent_system_prompt
from webapp.services.llm import chat_completion, extract_json

_CHEATSHEET_PATH = Path(__file__).resolve().parent / "DECISION_MEMORY_CHEATSHEET.md"

_VALID_INSIGHT_TYPES = frozenset(
    {
        "pattern_insight",
        "cadence_rule",
        "custom_rule",
        "custom_rule_hint",
        "category_rename",
        "taxonomy_hint",
        "merchant_label",
        "merchant_label_hint",
    }
)

_INSIGHT_TYPE_ALIASES = {
    "taxonomy_hint": "category_rename",
    "custom_rule_hint": "custom_rule",
    "merchant_label_hint": "merchant_label",
}

_SUGGESTED_ACTION_ALIASES = {
    "taxonomy_hint": "rename_category",
    "propose_cadence": "review_cadence",
    "custom_rule": "create_rule",
    "merchant_label": "apply_labels",
}


def _normalize_insight_type(raw: str) -> str:
    t = str(raw or "pattern_insight").strip()
    return _INSIGHT_TYPE_ALIASES.get(t, t)


def _normalize_suggested_action(raw: str) -> str:
    action = str(raw or "").strip()
    return _SUGGESTED_ACTION_ALIASES.get(action, action)


def _normalize_proposal_json(
    proposal: dict[str, Any],
    *,
    insight_type: str,
) -> dict[str, Any]:
    out = dict(proposal)
    if out.get("suggested_action"):
        out["suggested_action"] = _normalize_suggested_action(str(out["suggested_action"]))
    elif insight_type == "category_rename" or (
        insight_type == "pattern_insight"
        and (out.get("from_category") or out.get("to_category"))
    ):
        out["suggested_action"] = "rename_category"
    elif insight_type == "cadence_rule":
        out["suggested_action"] = "review_cadence"
    elif insight_type == "custom_rule":
        out["suggested_action"] = "create_rule"
    elif insight_type == "merchant_label":
        out["suggested_action"] = "apply_labels"
    return out

LEARNING_AGENT_USE_LLM = os.getenv("LEARNING_AGENT_USE_LLM", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
LEARNING_AGENT_MAX_TOOL_ROUNDS = max(
    1, int(os.getenv("LEARNING_AGENT_MAX_TOOL_ROUNDS", "8"))
)


def _load_cheatsheet(*, include_cadence: bool = True) -> str:
    try:
        text = _CHEATSHEET_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "(cheat sheet unavailable)"
    if include_cadence:
        return text
    text = re.sub(r"\| `cadence_rules` \|[^\n]+\n", "", text)
    text = text.replace("`cadence_rule`, ", "").replace(", `cadence_rule`", "")
    text = re.sub(
        r"\nMerchants with expense history but no cadence rule:.*?(?=\n## Expense SQL)",
        "\n",
        text,
        flags=re.DOTALL,
    )
    return text.strip()


def _parse_action(text: str) -> dict[str, Any]:
    try:
        parsed = extract_json(text)
        return parsed if isinstance(parsed, dict) else {"answer": text}
    except ValueError:
        pass
    if "{" in text:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"answer": text}


def _build_seed_context(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    *,
    lookback_days: int,
    include_cadence: bool = True,
) -> str:
    by_source: Counter[str] = Counter()
    by_action: Counter[str] = Counter()
    for ev in events:
        by_source[str(ev.get("source") or "unknown")] += 1
        by_action[str(ev.get("action") or "unknown")] += 1

    open_insights = conn.execute(
        "SELECT COUNT(*) AS c FROM ai_insights WHERE status = 'open'"
    ).fetchone()["c"]
    rejected_insights = conn.execute(
        "SELECT COUNT(*) AS c FROM ai_insights WHERE status = 'rejected'"
    ).fetchone()["c"]
    accepted_insights = conn.execute(
        "SELECT COUNT(*) AS c FROM ai_insights WHERE status = 'accepted'"
    ).fetchone()["c"]

    lines = [
        f"Lookback window: {lookback_days} days",
        f"Decision events in window: {len(events)}",
        f"Events by source: {dict(by_source)}",
        f"Events by action: {dict(by_action)}",
        f"ai_insights — open: {open_insights}, accepted: {accepted_insights}, rejected: {rejected_insights}",
    ]

    if events:
        lines.append("Recent events (newest first, up to 8):")
        for ev in events[:8]:
            lines.append(
                json.dumps(
                    {
                        "source": ev.get("source"),
                        "entity_type": ev.get("entity_type"),
                        "entity_key": ev.get("entity_key"),
                        "action": ev.get("action"),
                        "ai_proposal": ev.get("ai_proposal"),
                        "user_outcome": ev.get("user_outcome"),
                    },
                    default=str,
                )
            )
    else:
        empty_hint = (
            "No decision_events in lookback window — prefer cadence gaps or skip low-confidence insights."
            if include_cadence
            else "No decision_events in lookback window — skip low-confidence insights."
        )
        lines.append(empty_hint)

    return "\n".join(lines)


def _normalize_insight(
    raw: dict[str, Any],
    *,
    include_cadence: bool = True,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    insight_type = str(raw.get("insight_type") or "pattern_insight").strip()
    insight_type = _normalize_insight_type(insight_type)
    if not include_cadence and insight_type == "cadence_rule":
        return None
    if insight_type not in _VALID_INSIGHT_TYPES:
        insight_type = "pattern_insight"
    title = str(raw.get("title") or "").strip()
    pattern_summary = str(raw.get("pattern_summary") or "").strip()
    rationale = str(raw.get("rationale") or "").strip()
    if not pattern_summary and not title:
        return None
    if not title:
        title = pattern_summary[:80]
    if not pattern_summary:
        pattern_summary = title
    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(0.99, confidence))
    merchant_key = str(raw.get("merchant_key") or "").strip()
    proposal = raw.get("proposal_json")
    if not isinstance(proposal, dict):
        proposal = {}
    proposal = _normalize_proposal_json(proposal, insight_type=insight_type)
    if not include_cadence and proposal.get("suggested_action") in (
        "review_cadence",
        "propose_cadence",
    ):
        return None
    return {
        "insight_type": insight_type,
        "title": title,
        "pattern_summary": pattern_summary,
        "rationale": rationale or "Identified by Decision Analyst.",
        "confidence": confidence,
        "merchant_key": merchant_key,
        "proposal_json": proposal,
    }


def _parse_insights_payload(
    parsed: dict[str, Any],
    *,
    max_insights: int,
    include_cadence: bool = True,
) -> list[dict[str, Any]]:
    raw_list = parsed.get("insights")
    if not isinstance(raw_list, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw_list:
        normalized = _normalize_insight(item, include_cadence=include_cadence)
        if normalized:
            out.append(normalized)
        if len(out) >= max_insights:
            break
    return out


def run_decision_analyst(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    *,
    lookback_days: int,
    max_insights: int,
    model: str,
    max_tool_rounds: int | None = None,
    include_cadence: bool = True,
) -> dict[str, Any]:
    """
    Run the LLM Decision Analyst with query_sql tool loop.
    Returns normalized insight proposals and run metadata.
    """
    from webapp.agent.db_query import learning_agent_allowed_tables

    rounds_limit = max_tool_rounds or LEARNING_AGENT_MAX_TOOL_ROUNDS
    allowed_tables = learning_agent_allowed_tables(include_cadence=include_cadence)
    system = learning_agent_system_prompt(include_cadence=include_cadence).format(
        data_cheatsheet=_load_cheatsheet(include_cadence=include_cadence),
        seed_context=_build_seed_context(
            conn, events, lookback_days=lookback_days, include_cadence=include_cadence
        ),
        max_insights=max_insights,
    )
    task = (
        f"Analyze decision memory for repeatable patterns worth surfacing in the Workspace inbox. "
        f"Return at most {max_insights} insights with strong evidence. "
        f"Use query_sql as needed, then respond with {{\"insights\": [...]}}."
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    trace: list[dict[str, Any]] = []

    for _ in range(rounds_limit + 1):
        raw = chat_completion(
            messages,
            model=model,
            temperature=0.2,
            caller="learning_agent.analyst",
        )
        action = _parse_action(raw)

        if "insights" in action and not action.get("tool"):
            insights = _parse_insights_payload(
                action, max_insights=max_insights, include_cadence=include_cadence
            )
            return {
                "insights": insights,
                "tool_rounds": len(trace),
                "model": model,
                "trace": trace,
            }

        tool_name = action.get("tool")
        if tool_name != "query_sql":
            if trace:
                break
            raise ValueError(
                f"Learning Agent expected query_sql or insights JSON; got: {raw[:200]}"
            )

        args = action.get("args") or {}
        try:
            result = run_tool(
                conn,
                "query_sql",
                args,
                learning_agent_mode=True,
                learning_agent_allowed_tables=allowed_tables,
            )
        except Exception as exc:
            result = {"error": str(exc)}

        trace.append({"tool": tool_name, "args": args, "result": result})
        messages.append({"role": "assistant", "content": json.dumps(action)})
        messages.append(
            {
                "role": "user",
                "content": f"Tool result for {tool_name}:\n{json.dumps(result, indent=2)}",
            }
        )

    if trace:
        raw = chat_completion(
            messages
            + [
                {
                    "role": "user",
                    "content": (
                        'Respond with ONLY {"insights": [...]} — no more tool calls. '
                        f"At most {max_insights} insights; use [] if evidence is insufficient."
                    ),
                }
            ],
            model=model,
            temperature=0.1,
            caller="learning_agent.analyst_finalize",
        )
        action = _parse_action(raw)
        if "insights" in action:
            insights = _parse_insights_payload(
                action, max_insights=max_insights, include_cadence=include_cadence
            )
            return {
                "insights": insights,
                "tool_rounds": len(trace),
                "model": model,
                "trace": trace,
            }

    return {
        "insights": [],
        "tool_rounds": len(trace),
        "model": model,
        "trace": trace,
        "error": "max_tool_rounds_exceeded",
    }
