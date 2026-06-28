"""Background Learning Agent — analyzes decision_events and proposes insights."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from webapp.db.schema import get_connection, init_db
from webapp.services.decision_events import list_recent_events


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on")


LEARNING_AGENT_ENABLED = _env_bool("LEARNING_AGENT_ENABLED", default=False)
LEARNING_AGENT_INTERVAL_HOURS = max(
    1, int(os.getenv("LEARNING_AGENT_INTERVAL_HOURS", "3"))
)
LEARNING_AGENT_LOOKBACK_DAYS = max(
    1, int(os.getenv("LEARNING_AGENT_LOOKBACK_DAYS", "30"))
)
LEARNING_AGENT_MAX_INSIGHTS = max(
    1, int(os.getenv("LEARNING_AGENT_MAX_INSIGHTS", "10"))
)


def _db_path_default() -> Path:
    from webapp.config import DB_PATH

    return DB_PATH


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        INSERT INTO agent_runs (run_type, status, detail, started_at)
        VALUES ('learning_agent', 'running', '', ?)
        """,
        (_utc_now(),),
    )
    return int(cur.lastrowid or 0)


def _finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    detail: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE agent_runs
        SET status = ?, detail = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, json.dumps(detail), _utc_now(), run_id),
    )


def _category_correction_patterns(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find repeated category corrections (structural aggregation, no keyword rules)."""
    pairs: Counter[tuple[str, str]] = Counter()
    for ev in events:
        if ev.get("action") not in ("edited", "accepted"):
            continue
        proposal = ev.get("ai_proposal") or {}
        outcome = ev.get("user_outcome") or {}
        from_cat = str(proposal.get("ai_category") or "").strip()
        to_cat = str(outcome.get("ai_category") or "").strip()
        if from_cat and to_cat and from_cat.lower() != to_cat.lower():
            pairs[(from_cat, to_cat)] += 1
    proposals: list[dict[str, Any]] = []
    for (from_cat, to_cat), count in pairs.most_common(5):
        if count < 2:
            continue
        proposals.append(
            {
                "insight_type": "pattern_insight",
                "title": f"Category correction pattern ({count}×)",
                "pattern_summary": (
                    f"You often change **{from_cat}** → **{to_cat}** when confirming labels."
                ),
                "rationale": (
                    f"Observed {count} decision event(s) with this category shift in the "
                    f"last {LEARNING_AGENT_LOOKBACK_DAYS} days."
                ),
                "confidence": min(0.95, 0.5 + count * 0.1),
                "proposal_json": {
                    "suggested_action": "rename_category",
                    "from_category": from_cat,
                    "to_category": to_cat,
                    "occurrence_count": count,
                },
                "merchant_key": "",
            }
        )
    return proposals


def _cadence_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Merchants with spend history but no cadence rule — propose via existing cadence suggest."""
    from webapp.services.edit_cadence_suggest import (
        CadenceSuggestFilters,
        list_merchants_needing_cadence,
    )

    try:
        merchants = list_merchants_needing_cadence(
            conn, filters=CadenceSuggestFilters(), limit=5, offset=0
        )
    except Exception:
        return []

    proposals: list[dict[str, Any]] = []
    for m in merchants:
        mk = str(m.get("merchant_key") or "").strip()
        if not mk:
            continue
        proposals.append(
            {
                "insight_type": "cadence_rule",
                "title": mk,
                "pattern_summary": (
                    f"Recurring spend pattern detected — review cadence for **{mk}**."
                ),
                "rationale": "Merchant has expense history without a confirmed cadence rule.",
                "confidence": 0.6,
                "proposal_json": {
                    "merchant_key": mk,
                    "ai_category": m.get("ai_category"),
                    "suggested_action": "review_cadence",
                },
                "merchant_key": mk,
            }
        )
    return proposals


def _insert_insight(conn: sqlite3.Connection, proposal: dict[str, Any]) -> int | None:
    """Insert open insight unless duplicate. Returns new row id or None."""
    existing = conn.execute(
        """
        SELECT id FROM ai_insights
        WHERE status = 'open'
          AND insight_type = ?
          AND COALESCE(merchant_key, '') = ?
          AND pattern_summary = ?
        LIMIT 1
        """,
        (
            proposal["insight_type"],
            proposal.get("merchant_key") or "",
            proposal.get("pattern_summary") or "",
        ),
    ).fetchone()
    if existing:
        return None
    now = _utc_now()
    cur = conn.execute(
        """
        INSERT INTO ai_insights (
            insight_type, title, pattern_summary, rationale, confidence,
            merchant_key, proposal_json, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        (
            proposal["insight_type"],
            proposal.get("title") or "",
            proposal.get("pattern_summary") or "",
            proposal.get("rationale") or "",
            float(proposal.get("confidence") or 0.5),
            proposal.get("merchant_key") or "",
            json.dumps(proposal.get("proposal_json") or {}),
            now,
            now,
        ),
    )
    return int(cur.lastrowid or 0) or None


def run_learning_agent(
    db_path: Path | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    from webapp.config import LEARNING_AGENT_USE_LLM

    if not LEARNING_AGENT_ENABLED and not force:
        return {"enabled": False, "skipped": True}

    path = db_path or _db_path_default()
    init_db(path)
    conn = get_connection(path)
    run_id = _start_run(conn)
    conn.commit()
    inserted = 0
    llm_insights = 0
    heuristic_insights = 0
    inserted_insights: list[dict[str, Any]] = []
    analyst_meta: dict[str, Any] = {}
    try:
        events = list_recent_events(conn, days=LEARNING_AGENT_LOOKBACK_DAYS)
        proposals: list[dict[str, Any]] = []

        if LEARNING_AGENT_USE_LLM:
            try:
                from webapp.agent.learning_analyst import run_decision_analyst
                from webapp.config import LEARNING_AGENT_MODEL

                analyst_meta = run_decision_analyst(
                    conn,
                    events,
                    lookback_days=LEARNING_AGENT_LOOKBACK_DAYS,
                    max_insights=LEARNING_AGENT_MAX_INSIGHTS,
                    model=LEARNING_AGENT_MODEL,
                )
                llm_proposals = analyst_meta.get("insights") or []
                proposals.extend(llm_proposals)
                llm_insights = len(llm_proposals)
            except Exception as exc:
                analyst_meta = {"error": str(exc), "fallback": "heuristic"}

        if len(proposals) < LEARNING_AGENT_MAX_INSIGHTS:
            remaining = LEARNING_AGENT_MAX_INSIGHTS - len(proposals)
            heuristic = _category_correction_patterns(events)
            heuristic.extend(_cadence_candidates(conn))
            seen_summaries = {p.get("pattern_summary") for p in proposals}
            for p in heuristic:
                if len(proposals) >= LEARNING_AGENT_MAX_INSIGHTS:
                    break
                if p.get("pattern_summary") in seen_summaries:
                    continue
                proposals.append(p)
                heuristic_insights += 1
                if heuristic_insights >= remaining:
                    break

        for p in proposals[:LEARNING_AGENT_MAX_INSIGHTS]:
            row_id = _insert_insight(conn, p)
            if row_id:
                inserted += 1
                inserted_insights.append(
                    {
                        "id": row_id,
                        "insight_type": p.get("insight_type"),
                        "title": p.get("title"),
                        "pattern_summary": p.get("pattern_summary"),
                        "merchant_key": p.get("merchant_key") or "",
                    }
                )
        conn.commit()
        summary = {
            "enabled": True,
            "events_analyzed": len(events),
            "insights_inserted": inserted,
            "inserted_insights": inserted_insights,
            "llm_insights": llm_insights,
            "heuristic_insights": heuristic_insights,
            "analyst": {
                k: v
                for k, v in analyst_meta.items()
                if k != "trace"
            },
            "run_id": run_id,
        }
        _finish_run(conn, run_id, status="completed", detail=summary)
        conn.commit()
        return summary
    except Exception as exc:
        _finish_run(
            conn,
            run_id,
            status="failed",
            detail={"error": str(exc)},
        )
        conn.commit()
        raise
    finally:
        conn.close()


def accept_insight(conn: sqlite3.Connection, insight_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM ai_insights WHERE id = ?", (insight_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Insight not found")
    insight = dict(row)
    now = _utc_now()
    conn.execute(
        "UPDATE ai_insights SET status = 'accepted', updated_at = ? WHERE id = ?",
        (now, insight_id),
    )
    from webapp.services.decision_events import log_decision_event

    proposal = {}
    raw = insight.get("proposal_json")
    if isinstance(raw, str) and raw:
        try:
            proposal = json.loads(raw)
        except json.JSONDecodeError:
            proposal = {}
    log_decision_event(
        conn,
        source="learning_agent",
        entity_type="insight",
        entity_key=str(insight_id),
        action="accepted",
        ai_proposal=proposal,
        user_outcome={"status": "accepted"},
        context={"title": insight.get("title"), "insight_type": insight.get("insight_type")},
    )
    conn.commit()
    return {"id": insight_id, "status": "accepted"}


def reject_insight(conn: sqlite3.Connection, insight_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM ai_insights WHERE id = ?", (insight_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Insight not found")
    insight = dict(row)
    now = _utc_now()
    conn.execute(
        "UPDATE ai_insights SET status = 'rejected', updated_at = ? WHERE id = ?",
        (now, insight_id),
    )
    from webapp.services.decision_events import log_decision_event

    proposal = {}
    raw = insight.get("proposal_json")
    if isinstance(raw, str) and raw:
        try:
            proposal = json.loads(raw)
        except json.JSONDecodeError:
            proposal = {}
    log_decision_event(
        conn,
        source="learning_agent",
        entity_type="insight",
        entity_key=str(insight_id),
        action="rejected",
        ai_proposal=proposal,
        user_outcome={"status": "rejected"},
        context={"title": insight.get("title")},
    )
    conn.commit()
    return {"id": insight_id, "status": "rejected"}


def learning_agent_status(conn: sqlite3.Connection) -> dict[str, Any]:
    from webapp.config import LEARNING_AGENT_MODEL, LEARNING_AGENT_USE_LLM

    last = conn.execute(
        """
        SELECT status, detail, started_at, finished_at
        FROM agent_runs
        WHERE run_type = 'learning_agent'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    open_count = conn.execute(
        "SELECT COUNT(*) AS c FROM ai_insights WHERE status = 'open'"
    ).fetchone()["c"]
    detail = {}
    if last and last["detail"]:
        try:
            detail = json.loads(last["detail"])
        except json.JSONDecodeError:
            detail = {"raw": last["detail"]}
    return {
        "enabled": LEARNING_AGENT_ENABLED,
        "interval_hours": LEARNING_AGENT_INTERVAL_HOURS,
        "use_llm": LEARNING_AGENT_USE_LLM,
        "model": LEARNING_AGENT_MODEL,
        "open_insights": int(open_count),
        "last_run": dict(last) if last else None,
        "last_detail": detail,
    }
