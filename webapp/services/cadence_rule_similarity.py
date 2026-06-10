from __future__ import annotations

from typing import Any

from webapp.services.expense_cadence import (
    CADENCE_KIND_EXCLUDE,
    CADENCE_KIND_ONE_TIME,
    CADENCE_KIND_UNKNOWN,
    _normalize_kind,
    cadence_kind_label,
    expense_cadence_period_label,
    include_in_run_rate_value,
)


def _format_rule_summary(rule: dict[str, Any]) -> str:
    kind = _normalize_kind(rule.get("cadence_kind"))
    period = expense_cadence_period_label(
        kind,
        rule.get("period_count"),
        rule.get("period_unit"),
    )
    run_rate = include_in_run_rate_value(
        kind,
        None if rule.get("include_in_run_rate") is None else bool(int(rule["include_in_run_rate"])),
    )
    parts = [cadence_kind_label(kind)]
    if period != "—":
        parts.append(period)
    parts.append("in core" if run_rate else "excluded from core")
    note = (rule.get("notes") or rule.get("cadence_note") or "").strip()
    if note:
        parts.append(f"({note})")
    return ", ".join(parts)


def cadence_rules_equivalent(proposed: dict[str, Any], existing: dict[str, Any]) -> bool:
    """True when an existing merchant rule already matches the proposal."""
    kind_a = _normalize_kind(proposed.get("cadence_kind"))
    kind_b = _normalize_kind(existing.get("cadence_kind"))
    if kind_a != kind_b:
        return False

    if kind_a in (CADENCE_KIND_ONE_TIME, CADENCE_KIND_EXCLUDE, CADENCE_KIND_UNKNOWN):
        return True

    count_a = proposed.get("period_count")
    count_b = existing.get("period_count")
    unit_a = (proposed.get("period_unit") or "").strip().lower() or None
    unit_b = (existing.get("period_unit") or "").strip().lower() or None
    if count_a != count_b or unit_a != unit_b:
        return False

    prop_run = proposed.get("include_in_run_rate")
    exist_raw = existing.get("include_in_run_rate")
    exist_run = None if exist_raw is None else bool(int(exist_raw))
    if prop_run is not None and exist_run is not None and bool(prop_run) != exist_run:
        return False
    return True


def suppress_duplicate_cadence_proposal(
    result: dict[str, Any],
    *,
    merchant_key: str,
    existing_rule: dict[str, Any] | None,
) -> dict[str, Any]:
    """Clear recommend_save_rule when an equivalent cadence rule already exists."""
    if not existing_rule:
        return result
    if not result.get("recommend_save_rule"):
        return result

    proposed = {
        "cadence_kind": result.get("cadence_kind"),
        "period_count": result.get("period_count"),
        "period_unit": result.get("period_unit"),
        "include_in_run_rate": result.get("include_in_run_rate"),
    }
    if not cadence_rules_equivalent(proposed, existing_rule):
        return result

    updated = dict(result)
    updated["recommend_save_rule"] = False
    updated["existing_similar_rule"] = _format_rule_summary(existing_rule)
    updated["existing_rule_source"] = existing_rule.get("source") or ""
    updated["duplicate_rule_skipped"] = True

    note = (
        f"Cadence rule already saved for {merchant_key}: "
        f"«{updated['existing_similar_rule']}»"
    )
    updated["data_notes"] = list(updated.get("data_notes") or []) + [note]
    return updated
