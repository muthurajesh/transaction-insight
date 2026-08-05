"""Unified inbox: aggregate AI proposals awaiting user action."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from webapp.services.categorize import list_merchant_transactions, list_review_items
from webapp.services.classification_audit import list_findings, reconcile_open_findings


def list_pending_confirmations(
    conn: sqlite3.Connection,
    *,
    include_review_queue: bool = True,
    review_limit: int = 2000,
    include_cadence: bool = True,
) -> dict[str, Any]:
    """Return grouped pending items for the Agent Workspace inbox.

    ``review_limit`` caps how many payee label items are included (default high so
    Check labels can work the full queue; not a product taxonomy).
    """
    reconcile_open_findings(conn)
    items: list[dict[str, Any]] = []
    review_queue_total = 0

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
        from webapp.agent.learning_analyst import (
            _normalize_insight_type,
            _normalize_proposal_json,
        )

        ctype = _normalize_insight_type(str(insight.get("insight_type") or "pattern_insight"))
        proposal = _normalize_proposal_json(proposal, insight_type=ctype)
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
        review_items = list_review_items(conn)
        review_queue_total = len(review_items)
        for item in review_items[: max(0, int(review_limit))]:
            mk = str(item.get("merchant_key") or "")
            pending = int(item.get("transaction_count") or item.get("pending_count") or 0)
            items.append(
                {
                    "confirmation_id": f"review-{mk}",
                    "confirmation_type": "merchant_label",
                    "source": "confirm_categories",
                    "title": mk,
                    "summary": (
                        f"{pending} purchase{'s' if pending != 1 else ''} — needs a look"
                        if pending
                        else "Needs a look"
                    ),
                    "proposal": {
                        "ai_category": item.get("ai_category"),
                        "ai_sub_category": item.get("ai_sub_category"),
                        "expense_type": item.get("expense_type"),
                        "flow_type": item.get("flow_type"),
                        "classification": item.get("classification"),
                        "confidence": item.get("confidence"),
                        "sample_rationale": item.get("sample_rationale"),
                    },
                    "entity_key": mk,
                    "reference_id": mk,
                    "transaction_id": item.get("transaction_id"),
                    "priority": 90,
                }
            )

    items.sort(key=lambda x: (-int(x.get("priority") or 0), x.get("title") or ""))
    items = filter_cadence_confirmations(items, include_cadence=include_cadence)
    return {
        "count": len(items),
        "items": items,
        "by_type": _count_by_type(items),
        "review_queue_total": review_queue_total,
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


def is_cadence_confirmation(item: dict[str, Any]) -> bool:
    """True when a pending item is a cadence review proposal."""
    ctype = str(item.get("confirmation_type") or "")
    if ctype == "cadence_rule":
        return True
    proposal = item.get("proposal") or {}
    action = str(proposal.get("suggested_action") or "").strip()
    return action in ("review_cadence", "propose_cadence")


def filter_cadence_confirmations(
    items: list[dict[str, Any]],
    *,
    include_cadence: bool,
) -> list[dict[str, Any]]:
    if include_cadence:
        return items
    return [item for item in items if not is_cadence_confirmation(item)]


def _proposed_labels_for_item(item: dict[str, Any]) -> dict[str, Any] | None:
    ctype = str(item.get("confirmation_type") or "")
    proposal = item.get("proposal") or {}
    if ctype == "merchant_label":
        return {
            "ai_category": proposal.get("ai_category"),
            "ai_sub_category": proposal.get("ai_sub_category"),
            "expense_type": proposal.get("expense_type"),
            "flow_type": proposal.get("flow_type"),
            "classification": proposal.get("classification"),
        }
    if ctype == "quality_flag":
        return {
            "ai_category": proposal.get("suggested_category"),
            "ai_sub_category": proposal.get("suggested_sub"),
        }
    return None


def _norm_text(val: Any) -> str:
    return str(val or "").strip()


def _format_label_line(labels: dict[str, Any] | None) -> str:
    if not labels:
        return "—"
    parts = [_norm_text(labels.get("ai_category")) or "—"]
    sub = _norm_text(labels.get("ai_sub_category"))
    if sub:
        parts.append(f" / {sub}")
    for key in ("expense_type", "classification", "flow_type"):
        val = _norm_text(labels.get(key))
        if val:
            parts.append(f" · {val}")
    return "".join(parts)


def _row_matches_proposed(row: dict[str, Any], proposed: dict[str, Any] | None) -> bool:
    if not proposed:
        return True
    for key in ("ai_category", "ai_sub_category", "expense_type", "flow_type", "classification"):
        if _norm_text(row.get(key)) != _norm_text(proposed.get(key)):
            return False
    return True


def _status_summary(rows: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        status = _norm_text(row.get("label_status")).lower() or "pending"
        counts[status] = counts.get(status, 0) + 1
    bits: list[str] = []
    needs_review = counts.get("needs_review", 0)
    pending = counts.get("pending", 0)
    if needs_review:
        bits.append(f"{needs_review} need{'s' if needs_review == 1 else ''} review")
    if pending:
        bits.append(f"{pending} pending")
    for status, count in sorted(counts.items()):
        if status in ("needs_review", "pending"):
            continue
        bits.append(f"{count} {status.replace('_', ' ')}")
    phrase = ", ".join(bits) if bits else "status unknown"
    return phrase, counts


def _sample_rationale(rows: list[dict[str, Any]], fallback: str = "") -> str:
    for row in rows:
        text = _norm_text(row.get("rationale"))
        if text:
            return text
    return _norm_text(fallback)


def _build_merchant_label_context(
    item: dict[str, Any],
    merchant_key: str,
    rows: list[dict[str, Any]],
    proposed: dict[str, Any] | None,
) -> dict[str, Any]:
    proposal = item.get("proposal") or {}
    status_phrase, status_counts = _status_summary(rows)
    labels_match = bool(rows) and all(_row_matches_proposed(dict(r), proposed) for r in rows)
    ai_rationale = _sample_rationale(rows, str(proposal.get("sample_rationale") or ""))
    confidence = proposal.get("confidence")
    if confidence is None and rows:
        confidence = max((float(r.get("confidence") or 0) for r in rows), default=None)

    paragraphs: list[str] = [
        (
            f"The AI classified {len(rows)} transaction(s) for merchant "
            f"\"{merchant_key}\". They are awaiting your confirmation before "
            f"being saved as a reusable merchant rule."
        ),
        f"Label status: {status_phrase} (not yet confirmed).",
    ]
    if labels_match:
        paragraphs.append(
            "Current and proposed labels match. Approving confirms the classification "
            "without changing transaction data."
        )
    elif proposed:
        paragraphs.append(
            f"Approving would apply these labels to pending transactions: "
            f"{_format_label_line(proposed)}."
        )
    if ai_rationale:
        paragraphs.append(f"AI analysis: {ai_rationale}")

    return {
        "kind": "merchant_label",
        "proposal_labels": _format_label_line(proposed),
        "status_phrase": status_phrase,
        "status_counts": status_counts,
        "labels_match": labels_match,
        "ai_rationale": ai_rationale,
        "confidence": confidence,
        "paragraphs": paragraphs,
    }


def _build_quality_flag_context(
    item: dict[str, Any],
    merchant_key: str,
    rows: list[dict[str, Any]],
    proposed: dict[str, Any] | None,
) -> dict[str, Any]:
    proposal = item.get("proposal") or {}
    status_phrase, status_counts = _status_summary(rows)
    ai_rationale = _norm_text(proposal.get("rationale")) or _sample_rationale(rows)
    prod = _format_label_line(
        {
            "ai_category": proposal.get("production_category"),
            "ai_sub_category": proposal.get("production_sub"),
        }
    )
    sugg = _format_label_line(proposed)

    paragraphs: list[str] = [
        (
            f"Classification audit flagged {len(rows)} transaction(s) for "
            f"\"{merchant_key}\" where stored labels may not match the AI suggestion."
        ),
        f"Label status in preview: {status_phrase}.",
        f"Suggested fix: {sugg}.",
    ]
    if prod and prod != "—":
        paragraphs.append(f"Labels currently in the database: {prod}.")
    if ai_rationale:
        paragraphs.append(f"AI analysis: {ai_rationale}")

    return {
        "kind": "quality_flag",
        "proposal_labels": sugg,
        "status_phrase": status_phrase,
        "status_counts": status_counts,
        "ai_rationale": ai_rationale,
        "paragraphs": paragraphs,
    }


def _build_learning_agent_context(
    item: dict[str, Any],
    merchant_key: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    proposal = item.get("proposal") or {}
    summary = _norm_text(item.get("summary"))
    ai_rationale = _norm_text(proposal.get("rationale")) or summary
    status_phrase, status_counts = _status_summary(rows) if rows else ("", {})

    paragraphs: list[str] = []
    if summary:
        paragraphs.append(summary)
    elif ai_rationale:
        paragraphs.append(ai_rationale)
    else:
        paragraphs.append("The Learning Agent surfaced a pattern worth reviewing.")

    if rows:
        paragraphs.append(
            f"{len(rows)} related transaction(s) for \"{merchant_key}\" are in "
            f"{status_phrase or 'review or pending status'}."
        )
    elif merchant_key:
        paragraphs.append(f"Related merchant: {merchant_key}.")

    action = _norm_text(proposal.get("suggested_action"))
    if action:
        paragraphs.append(f"Suggested next step: {action.replace('_', ' ')}.")

    return {
        "kind": str(item.get("confirmation_type") or "insight"),
        "status_phrase": status_phrase,
        "status_counts": status_counts,
        "ai_rationale": ai_rationale,
        "paragraphs": paragraphs,
    }


def _build_item_context(
    item: dict[str, Any],
    *,
    merchant_key: str = "",
    rows: list[dict[str, Any]] | None = None,
    proposed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctype = str(item.get("confirmation_type") or "")
    proposal = item.get("proposal") or {}
    rows = rows or []

    if ctype == "merchant_label":
        return _build_merchant_label_context(item, merchant_key, rows, proposed)
    if ctype == "quality_flag":
        return _build_quality_flag_context(item, merchant_key, rows, proposed)
    if item.get("source") == "learning_agent":
        return _build_learning_agent_context(item, merchant_key, rows)

    paragraphs: list[str] = []
    summary = _norm_text(item.get("summary"))
    if summary:
        paragraphs.append(summary)
    if ctype == "custom_rule":
        rule_text = _norm_text(proposal.get("rule_text"))
        if rule_text:
            paragraphs.append(f"Proposed custom rule: {rule_text}")
    rationale = _norm_text(proposal.get("rationale"))
    if rationale and rationale not in paragraphs:
        paragraphs.append(f"AI analysis: {rationale}")
    if not paragraphs:
        paragraphs.append("Review this AI proposal and choose Approve, Reject, or Cancel.")

    return {
        "kind": ctype or "other",
        "ai_rationale": rationale,
        "paragraphs": paragraphs,
    }


def _tx_preview_row(row: dict[str, Any], proposed: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "date": row.get("date"),
        "amount": row.get("amount"),
        "merchant_key": row.get("merchant_key") or "",
        "ai_category": row.get("ai_category"),
        "ai_sub_category": row.get("ai_sub_category"),
        "expense_type": row.get("expense_type"),
        "classification": row.get("classification"),
        "flow_type": row.get("flow_type"),
        "label_status": row.get("label_status"),
        "proposed": proposed,
    }


def preview_pending_confirmation(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    *,
    limit: int = 25,
) -> dict[str, Any]:
    """Return transaction rows (current vs proposed labels) for inbox modal preview."""
    limit = max(1, min(100, int(limit)))
    ctype = str(item.get("confirmation_type") or "")
    proposal = item.get("proposal") or {}

    if ctype == "custom_rule":
        from webapp.services.custom_rules import preview_custom_rule

        rule_text = str(proposal.get("rule_text") or item.get("summary") or "").strip()
        if not rule_text:
            return {
                "preview_kind": "rule",
                "total": 0,
                "transactions": [],
                "message": "No rule text to preview.",
                "context": _build_item_context(item),
            }
        prev = preview_custom_rule(conn, rule_text=rule_text, limit=limit, offset=0)
        return {
            "preview_kind": "rule",
            "total": int(prev.get("total") or 0),
            "transactions": prev.get("transactions") or [],
            "compile_error": prev.get("compile_error"),
            "context": _build_item_context(item),
        }

    merchant_key = str(item.get("entity_key") or item.get("title") or "").strip()
    if not merchant_key:
        return {
            "preview_kind": "none",
            "total": 0,
            "transactions": [],
            "message": "No merchant linked — transaction preview not available for this insight.",
            "context": _build_item_context(item),
        }

    proposed = _proposed_labels_for_item(item)
    review_only = ctype == "merchant_label"
    rows = list_merchant_transactions(conn, merchant_key, review_only=review_only)
    if not rows and not review_only:
        rows = list_merchant_transactions(conn, merchant_key, review_only=False)

    row_dicts = [dict(r) for r in rows]
    transactions = [_tx_preview_row(r, proposed) for r in row_dicts[:limit]]
    context = _build_item_context(
        item,
        merchant_key=merchant_key,
        rows=row_dicts,
        proposed=proposed,
    )
    return {
        "preview_kind": "merchant",
        "merchant_key": merchant_key,
        "total": len(row_dicts),
        "transactions": transactions,
        "review_only": review_only,
        "context": context,
    }
