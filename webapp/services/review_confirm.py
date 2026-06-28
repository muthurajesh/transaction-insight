from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from webapp.adapters.lookup_store import load_lookup_workbook_from_db
from webapp.processing import (
    CUSTOM_RULES_SHEET,
    MERCHANT_CATEGORIES_SHEET,
    load_active_custom_rules,
    normalize_custom_rules_sheet,
)
from webapp.services.categorize import (
    SPLIT_REVIEW_MERCHANT_KEYS,
    _normalize_classification,
    confirm_transaction,
)

FLOW_TYPES = frozenset({"Expense", "Income", "Transfer", "Adjustment"})
CONFIRM_SCOPES = frozenset({"pending", "all"})
LABEL_COMPARE_FIELDS = (
    "ai_category",
    "ai_sub_category",
    "flow_type",
    "expense_type",
    "classification",
)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none"):
        return ""
    return text


def _normalize_flow_type(value: str) -> str:
    flow = _norm(value) or "Expense"
    if flow not in FLOW_TYPES:
        raise ValueError(f"flow_type must be one of: {', '.join(sorted(FLOW_TYPES))}")
    return flow


def align_classification_with_category(labels: dict[str, str]) -> dict[str, str]:
    """When the category name indicates business spend, align classification to Business."""
    out = dict(labels)
    category = _norm(out.get("ai_category")).lower()
    if "business" not in category:
        return out
    if _normalize_classification(out.get("classification")) != "Business":
        out["classification"] = "Business"
    return out


def classification_adjusted_for_category(
    before: dict[str, str],
    after: dict[str, str],
) -> bool:
    return (
        _normalize_classification(before.get("classification"))
        != _normalize_classification(after.get("classification"))
        and _normalize_classification(after.get("classification")) == "Business"
    )


def _labels_from_proposed(
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(ai_category),
            "ai_sub_category": _norm(ai_sub_category),
            "expense_type": _norm(expense_type) or "Variable",
            "flow_type": _normalize_flow_type(flow_type),
            "classification": _normalize_classification(classification),
        }
    )


def _labels_from_merchant_row(row: Any) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(row.get("AI Category")),
            "ai_sub_category": _norm(row.get("AI Sub-Category")),
            "expense_type": _norm(row.get("Type")) or "Variable",
            "flow_type": _norm(row.get("Flow Type")) or "Expense",
            "classification": _norm(row.get("Classification")) or "Personal",
        }
    )


def _labels_from_business_row(row: Any) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(row.get("AI Category")),
            "ai_sub_category": _norm(row.get("AI Sub-Category")),
            "expense_type": _norm(row.get("Type")) or "Variable",
            "flow_type": _norm(row.get("Flow Type")) or "Expense",
            "classification": _norm(row.get("Classification")) or "Business",
        }
    )


def _labels_from_custom_set(spec: dict[str, Any]) -> dict[str, str]:
    ai_category = _norm(spec.get("ai_category")) or _norm(spec.get("category"))
    return align_classification_with_category(
        {
            "ai_category": ai_category,
            "ai_sub_category": _norm(spec.get("ai_sub_category")),
            "expense_type": _norm(spec.get("type")) or "Variable",
            "flow_type": _norm(spec.get("flow_type")) or "Expense",
            "classification": _norm(spec.get("classification")) or "Personal",
        }
    )


def _labels_conflict(existing: dict[str, str], proposed: dict[str, str]) -> bool:
    for key in LABEL_COMPARE_FIELDS:
        ex = _norm(existing.get(key))
        pr = _norm(proposed.get(key))
        if not ex:
            continue
        if ex != pr:
            return True
    return False


def _custom_rule_targets_merchant(rule: dict[str, Any], merchant_key: str) -> bool:
    match = rule.get("match") or {}
    gen = match.get("generated_description")
    mk = merchant_key.strip().lower()
    if gen is None:
        return False
    patterns = gen if isinstance(gen, list) else [gen]
    for pattern in patterns:
        text = _norm(pattern).lower()
        if not text:
            continue
        if text == mk or text.strip("*") == mk:
            return True
        if text.startswith("*") and text.endswith("*") and mk in text.strip("*"):
            return True
    return False


def find_lookup_conflicts(
    conn: sqlite3.Connection,
    merchant_key: str,
    proposed: dict[str, str],
) -> list[dict[str, Any]]:
    """Conflicts with saved merchant labels or active custom rules in SQLite."""
    lookups = load_lookup_workbook_from_db(conn)
    conflicts: list[dict[str, Any]] = []
    mk_lower = merchant_key.strip().lower()

    merchant_sheet = lookups.get(MERCHANT_CATEGORIES_SHEET)
    if merchant_sheet is not None and not merchant_sheet.empty and "Merchant Key" in merchant_sheet.columns:
        for _, row in merchant_sheet.iterrows():
            mk = _norm(row.get("Merchant Key"))
            if mk.lower() != mk_lower:
                continue
            existing = _labels_from_merchant_row(row)
            if _labels_conflict(existing, proposed):
                conflicts.append(
                    {
                        "source": MERCHANT_CATEGORIES_SHEET,
                        "merchant_key": mk,
                        "existing": existing,
                        "proposed": proposed,
                    }
                )
            break

    business_sheet = lookups.get("BusinessCategoryRules")
    if business_sheet is not None and not business_sheet.empty:
        if "Generated Description" in business_sheet.columns:
            for _, row in business_sheet.iterrows():
                desc = _norm(row.get("Generated Description"))
                if desc.lower() != mk_lower:
                    continue
                existing = _labels_from_business_row(row)
                if _labels_conflict(existing, proposed):
                    conflicts.append(
                        {
                            "source": "BusinessCategoryRules",
                            "merchant_key": desc,
                            "existing": existing,
                            "proposed": proposed,
                        }
                    )
                break

    custom_sheet = normalize_custom_rules_sheet(lookups.get(CUSTOM_RULES_SHEET))
    for rule in load_active_custom_rules(custom_sheet):
        if not _custom_rule_targets_merchant(rule, merchant_key):
            continue
        spec = rule.get("set") or {}
        existing = _labels_from_custom_set(spec)
        if _labels_conflict(existing, proposed):
            conflicts.append(
                {
                    "source": CUSTOM_RULES_SHEET,
                    "merchant_key": merchant_key,
                    "existing": existing,
                    "proposed": proposed,
                    "rule_type": rule.get("rule_type"),
                }
            )
    return conflicts


def _category_breakdown(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (_norm(row["ai_category"]), _norm(row["ai_sub_category"]))
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "ai_category": cat,
            "ai_sub_category": sub,
            "count": count,
        }
        for (cat, sub), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _row_differs_from_proposed(row: sqlite3.Row, proposed: dict[str, str]) -> bool:
    for field in LABEL_COMPARE_FIELDS:
        if _norm(row[field]) != _norm(proposed.get(field)):
            return True
    return False


def sync_merchant_labels_to_db(
    conn: sqlite3.Connection,
    merchant_key: str,
    proposed: dict[str, str],
    *,
    tx_count: int | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    tier = _norm(proposed.get("budget_tier")) or "Review"
    conn.execute(
        """
        INSERT INTO merchant_labels (
            merchant_key, ai_category, ai_sub_category, expense_type,
            confidence, label_status, rationale, sample_count, updated_at,
            budget_tier, classification, flow_type, notes
        ) VALUES (?, ?, ?, ?, 1.0, 'confirmed', 'user confirmed', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(merchant_key) DO UPDATE SET
            ai_category=excluded.ai_category,
            ai_sub_category=excluded.ai_sub_category,
            expense_type=excluded.expense_type,
            confidence=1.0,
            label_status='confirmed',
            rationale='user confirmed',
            sample_count=excluded.sample_count,
            updated_at=excluded.updated_at,
            budget_tier=excluded.budget_tier,
            classification=excluded.classification,
            flow_type=excluded.flow_type,
            notes=excluded.notes
        """,
        (
            merchant_key.strip(),
            proposed["ai_category"],
            proposed["ai_sub_category"],
            proposed["expense_type"],
            int(tx_count or 0),
            now,
            tier,
            proposed["classification"],
            proposed["flow_type"],
            f"confirmed via web {now}",
        ),
    )


def confirm_preview(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
) -> dict[str, Any]:
    mk = merchant_key.strip()
    if not mk:
        raise ValueError("merchant_key is required")
    proposed = _labels_from_proposed(
        ai_category=ai_category,
        ai_sub_category=ai_sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
    )
    if not proposed["ai_category"]:
        raise ValueError("ai_category is required")

    all_rows = conn.execute(
        """
        SELECT ai_category, ai_sub_category, flow_type, expense_type,
               classification, label_status
        FROM transactions
        WHERE merchant_key = ?
        """,
        (mk,),
    ).fetchall()

    pending_rows = [
        r for r in all_rows if _norm(r["label_status"]).lower() in ("needs_review", "pending")
    ]
    confirmed_rows = [
        r for r in all_rows if _norm(r["label_status"]).lower() not in ("needs_review", "pending")
    ]
    differing_confirmed = [r for r in confirmed_rows if _row_differs_from_proposed(r, proposed)]

    breakdown = _category_breakdown(all_rows)
    distinct_patterns = len(breakdown)
    suggest_custom_rule = distinct_patterns >= 2

    lookup_conflicts = find_lookup_conflicts(conn, mk, proposed)

    return {
        "merchant_key": mk,
        "proposed": proposed,
        "total_count": len(all_rows),
        "pending_count": len(pending_rows),
        "confirmed_count": len(confirmed_rows),
        "differing_confirmed_count": len(differing_confirmed),
        "differing_breakdown": _category_breakdown(differing_confirmed),
        "category_patterns": breakdown,
        "distinct_category_patterns": distinct_patterns,
        "suggest_custom_rule": suggest_custom_rule,
        "custom_rule_hint": (
            "This merchant has multiple category patterns in your data. "
            "Merchant labels allow one category per merchant — use a custom rule "
            "for amount- or description-based splits."
            if suggest_custom_rule
            else ""
        ),
        "lookup_conflicts": lookup_conflicts,
    }


def _apply_merchant_to_db(
    conn: sqlite3.Connection,
    merchant_key: str,
    proposed: dict[str, str],
    *,
    scope: str,
) -> int:
    if scope not in CONFIRM_SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(sorted(CONFIRM_SCOPES))}")

    status_filter = (
        " AND label_status IN ('needs_review', 'pending')"
        if scope == "pending"
        else ""
    )
    cur = conn.execute(
        f"""
        UPDATE transactions SET
            flow_type = ?,
            ai_category = ?,
            ai_sub_category = ?,
            expense_type = ?,
            classification = ?,
            confidence = 1.0,
            label_status = 'confirmed',
            rationale = 'user confirmed'
        WHERE merchant_key = ?{status_filter}
        """,
        (
            proposed["flow_type"],
            proposed["ai_category"],
            proposed["ai_sub_category"],
            proposed["expense_type"],
            proposed["classification"],
            merchant_key,
        ),
    )
    return cur.rowcount


def confirm_merchant_group(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
    scope: str = "pending",
    replace_conflicting_rule: bool = False,
) -> dict[str, Any]:
    mk = merchant_key.strip()
    if mk in SPLIT_REVIEW_MERCHANT_KEYS:
        raise ValueError(f"{mk} must be confirmed one transaction at a time")

    proposed = _labels_from_proposed(
        ai_category=ai_category,
        ai_sub_category=ai_sub_category,
        expense_type=expense_type,
        flow_type=flow_type,
        classification=classification,
    )
    if not proposed["ai_category"]:
        raise ValueError("ai_category is required")
    if scope not in CONFIRM_SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(sorted(CONFIRM_SCOPES))}")

    conflicts = find_lookup_conflicts(conn, mk, proposed)
    if conflicts and not replace_conflicting_rule:
        sources = ", ".join(sorted({c["source"] for c in conflicts}))
        raise ValueError(
            f"Saved lookup conflict in {sources}. Choose Replace existing rule to overwrite, "
            "or use a custom rule for complex patterns."
        )

    preview = confirm_preview(
        conn,
        mk,
        ai_category=proposed["ai_category"],
        ai_sub_category=proposed["ai_sub_category"],
        expense_type=proposed["expense_type"],
        flow_type=proposed["flow_type"],
        classification=proposed["classification"],
    )

    sync_merchant_labels_to_db(conn, mk, proposed, tx_count=preview["total_count"])
    rows_updated = _apply_merchant_to_db(conn, mk, proposed, scope=scope)
    conn.commit()

    return {
        "merchant_key": mk,
        "rows_updated": rows_updated,
        "scope": scope,
        "lookup_synced": True,
        "lookup_conflicts_replaced": bool(conflicts),
        "suggest_custom_rule": preview["suggest_custom_rule"],
        "custom_rule_hint": preview["custom_rule_hint"],
    }


def confirm_merchant_or_transaction(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    ai_category: str,
    ai_sub_category: str = "",
    expense_type: str = "Variable",
    flow_type: str = "Expense",
    classification: str = "Personal",
    transaction_id: str | None = None,
    scope: str = "pending",
    replace_conflicting_rule: bool = False,
    suggested_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    final = {
        "ai_category": ai_category,
        "ai_sub_category": ai_sub_category,
        "expense_type": expense_type,
        "flow_type": flow_type,
        "classification": classification,
    }
    if transaction_id:
        result = {
            "merchant_key": merchant_key,
            "transaction_id": transaction_id,
            "rows_updated": confirm_transaction(
                conn,
                transaction_id,
                ai_category=ai_category,
                ai_sub_category=ai_sub_category,
                expense_type=expense_type,
                flow_type=flow_type,
                classification=classification,
            ),
            "lookup_synced": False,
        }
    else:
        result = confirm_merchant_group(
            conn,
            merchant_key,
            ai_category=ai_category,
            ai_sub_category=ai_sub_category,
            expense_type=expense_type,
            flow_type=flow_type,
            classification=classification,
            scope=scope,
            replace_conflicting_rule=replace_conflicting_rule,
        )

    from webapp.services.decision_events import (
        infer_confirm_action,
        log_decision_event,
    )

    log_decision_event(
        conn,
        source="confirm_categories",
        entity_type="transaction" if transaction_id else "merchant",
        entity_key=transaction_id or merchant_key.strip(),
        action=infer_confirm_action(suggested_labels, final),
        ai_proposal=suggested_labels,
        user_outcome=final,
        context={"merchant_key": merchant_key, "scope": scope},
    )
    conn.commit()
    return result
