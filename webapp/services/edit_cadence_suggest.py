from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from webapp.services.cadence_insights import (
    _gather_merchant_stats,
    gather_merchant_cadence_pattern,
    package_cadence_proposal,
    propose_cadence,
)
from webapp.services.expense_cadence import (
    CADENCE_KIND_LUMP,
    CADENCE_KIND_ONE_TIME,
    CADENCE_KIND_RECURRING,
    CADENCE_KIND_UNKNOWN,
    get_cadence_rule,
)

CADENCE_SUGGEST_BATCH_LIMITS = frozenset({10, 25, 50})

_MERCHANTS_NEEDING_CADENCE_BASE = """
SELECT t.merchant_key,
       COUNT(*) AS tx_count,
       COUNT(DISTINCT t.budget_month) AS month_count,
       COALESCE(
           NULLIF(TRIM(MAX(t.ai_category)), ''),
           (
               SELECT NULLIF(TRIM(ml.ai_category), '')
               FROM merchant_labels ml
               WHERE ml.merchant_key = t.merchant_key
               LIMIT 1
           )
       ) AS ai_category
FROM transactions t
LEFT JOIN cadence_rules cr
  ON cr.merchant_key = t.merchant_key AND cr.enabled = 1
WHERE t.merchant_key IS NOT NULL AND TRIM(t.merchant_key) != ''
  AND t.flow_type = 'Expense'
  AND cr.merchant_key IS NULL
  AND (
    t.cadence_kind IS NULL
    OR TRIM(t.cadence_kind) = ''
    OR LOWER(t.cadence_kind) = 'unknown'
    OR LOWER(COALESCE(t.cadence_source, '')) IN ('pipeline', 'detected', 'default', 'lookup', '')
  )
  AND LOWER(COALESCE(t.cadence_source, '')) != 'user'
"""


@dataclass
class CadenceSuggestFilters:
    """Same label/search filters as Edit Transactions search (subset)."""

    q: str = ""
    month: str = ""
    category: str = ""
    sub_category: str = ""
    expense_type: str = ""
    classification: str = ""

    def active_labels(self) -> list[str]:
        labels: list[str] = []
        if (self.q or "").strip():
            labels.append(f"search “{(self.q or '').strip()}”")
        if (self.month or "").strip():
            labels.append(f"month {self.month.strip()}")
        if (self.category or "").strip():
            labels.append(f"category {self.category.strip()}")
        if (self.sub_category or "").strip():
            labels.append(f"sub {self.sub_category.strip()}")
        if (self.expense_type or "").strip():
            labels.append(f"type {self.expense_type.strip()}")
        if (self.classification or "").strip():
            labels.append(f"class {self.classification.strip()}")
        return labels

    def to_dict(self) -> dict[str, str]:
        return {
            "q": (self.q or "").strip(),
            "month": (self.month or "").strip(),
            "category": (self.category or "").strip(),
            "sub_category": (self.sub_category or "").strip(),
            "expense_type": (self.expense_type or "").strip(),
            "classification": (self.classification or "").strip(),
        }


def _search_filter_clauses(filters: CadenceSuggestFilters | None) -> tuple[list[str], list[Any]]:
    """Mirror transaction_edit.search_transactions filters on transactions t."""
    if filters is None:
        return [], []

    clauses: list[str] = []
    params: list[Any] = []

    text = (filters.q or "").strip()
    if text:
        like = f"%{text}%"
        clauses.append(
            """(
                t.merchant_key LIKE ? COLLATE NOCASE
                OR COALESCE(t.simple_description, '') LIKE ? COLLATE NOCASE
                OR COALESCE(t.user_description, '') LIKE ? COLLATE NOCASE
                OR COALESCE(t.original_description, '') LIKE ? COLLATE NOCASE
            )"""
        )
        params.extend([like, like, like, like])

    month = (filters.month or "").strip()
    if month:
        clauses.append("t.budget_month = ?")
        params.append(month)

    category = (filters.category or "").strip()
    if category:
        clauses.append("t.ai_category = ?")
        params.append(category)

    sub_category = (filters.sub_category or "").strip()
    if sub_category:
        clauses.append("t.ai_sub_category = ?")
        params.append(sub_category)

    expense_type = (filters.expense_type or "").strip()
    if expense_type:
        clauses.append("t.expense_type = ?")
        params.append(expense_type)

    classification = (filters.classification or "").strip()
    if classification:
        clauses.append("t.classification = ?")
        params.append(classification)

    return clauses, params


def _merchants_needing_cadence_sql(
    filters: CadenceSuggestFilters | None = None,
) -> tuple[str, list[Any]]:
    extra_clauses, extra_params = _search_filter_clauses(filters)
    where_extra = ""
    if extra_clauses:
        where_extra = " AND " + " AND ".join(extra_clauses)
    sql = (
        f"{_MERCHANTS_NEEDING_CADENCE_BASE}{where_extra}\n"
        "GROUP BY t.merchant_key\n"
        "HAVING month_count >= 3\n"
        "ORDER BY tx_count DESC, t.merchant_key"
    )
    return sql, extra_params


def count_merchants_needing_cadence(
    conn: sqlite3.Connection,
    *,
    filters: CadenceSuggestFilters | None = None,
) -> int:
    sql, params = _merchants_needing_cadence_sql(filters)
    row = conn.execute(f"SELECT COUNT(*) AS c FROM ({sql})", params).fetchone()
    return int(row["c"] or 0)


def list_merchants_needing_cadence(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int = 0,
    filters: CadenceSuggestFilters | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    sql, params = _merchants_needing_cadence_sql(filters)
    rows = conn.execute(f"{sql}\nLIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def _heuristic_cadence_proposal(
    *,
    merchant_key: str,
    stats: dict[str, Any],
    pattern: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a proposal when pattern detection is strong enough to skip the LLM."""
    pat = str(pattern.get("pattern") or "monthly")
    pat_conf = str(pattern.get("confidence") or "low")
    months_active = int(stats.get("months_active") or pattern.get("months_active") or 0)
    typical = float(pattern.get("typical_monthly") or 0)
    dominant = pattern.get("dominant_amount")
    dom_count = int(pattern.get("dominant_amount_count") or 0)

    if pat == "yearly" and pat_conf == "high":
        amt = abs(float(dominant or 0))
        amt_text = f"**${amt:,.2f}**" if amt else "large charges"
        gaps = pattern.get("gap_months_between_spikes") or []
        gap_hint = f" (~{gaps[0]} months apart)" if gaps else ""
        return {
            "insight": (
                f"**{merchant_key}** shows **{len(pattern.get('high_months') or [])}** spike "
                f"month(s){gap_hint} with {amt_text} — typical quiet months near "
                f"**${typical:,.2f}**. Treat as an annual lump spread monthly."
            ),
            "cadence_kind": CADENCE_KIND_LUMP,
            "period_count": 12,
            "period_unit": "months",
            "include_in_run_rate": False,
            "cadence_note": str(pattern.get("note") or "")[:200],
            "confidence": "high",
            "recommend_save_rule": True,
            "apply_scope": "merchant",
            "data_notes": [],
            "source": "heuristic",
        }

    if pat == "onetime" and months_active >= 3:
        amt = abs(float(dominant or 0))
        return {
            "insight": (
                f"**{merchant_key}** has a single spike month across **{months_active}** "
                f"months of history"
                + (f" (charge **${amt:,.2f}**)" if amt else "")
                + " — likely a one-time expense."
            ),
            "cadence_kind": CADENCE_KIND_ONE_TIME,
            "period_count": None,
            "period_unit": None,
            "include_in_run_rate": False,
            "cadence_note": str(pattern.get("note") or "")[:200],
            "confidence": "medium",
            "recommend_save_rule": False,
            "apply_scope": "transaction",
            "data_notes": [],
            "source": "heuristic",
        }

    if (
        pat == "monthly"
        and pat_conf == "medium"
        and months_active >= 3
        and dom_count >= months_active - 1
        and dominant is not None
    ):
        amt = abs(float(dominant))
        return {
            "insight": (
                f"**{merchant_key}** charges **${amt:,.2f}** in most active months "
                f"({dom_count} of {months_active}) with no annual spikes — monthly recurring."
            ),
            "cadence_kind": CADENCE_KIND_RECURRING,
            "period_count": 1,
            "period_unit": "months",
            "include_in_run_rate": True,
            "cadence_note": str(pattern.get("note") or "")[:200],
            "confidence": "medium",
            "recommend_save_rule": True,
            "apply_scope": "merchant",
            "data_notes": [],
            "source": "heuristic",
        }

    return None


def suggest_cadence_for_merchant(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    transaction_id: str | None = None,
    hint: str = "",
) -> dict[str, Any]:
    mk = (merchant_key or "").strip()
    if not mk:
        raise ValueError("merchant_key is required")

    if get_cadence_rule(conn, mk):
        raise ValueError(f"Cadence rule already exists for {mk!r}")

    stats = _gather_merchant_stats(conn, merchant_key=mk, transaction_id=transaction_id)
    pattern = stats.get("pattern_features") or gather_merchant_cadence_pattern(conn, mk)

    if not hint:
        heuristic = _heuristic_cadence_proposal(
            merchant_key=mk,
            stats=stats,
            pattern=pattern,
        )
        if heuristic and heuristic.get("confidence") == "high":
            return package_cadence_proposal(
                conn,
                merchant_key=mk,
                transaction_id=transaction_id,
                proposal=heuristic,
                stats=stats,
            )
        if heuristic and heuristic.get("source") == "heuristic" and heuristic.get("confidence") == "medium":
            return package_cadence_proposal(
                conn,
                merchant_key=mk,
                transaction_id=transaction_id,
                proposal=heuristic,
                stats=stats,
            )

    proposal = propose_cadence(
        conn,
        merchant_key=mk,
        transaction_id=transaction_id,
        hint=hint,
    )
    if proposal.get("cadence_kind") == CADENCE_KIND_UNKNOWN and pattern.get("pattern") == "unplanned":
        note = str(pattern.get("note") or "")
        if note and not proposal.get("cadence_note"):
            proposal = dict(proposal)
            proposal["cadence_note"] = note
    return proposal


def suggest_cadence_bulk(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
    filters: CadenceSuggestFilters | None = None,
) -> dict[str, Any]:
    if limit not in CADENCE_SUGGEST_BATCH_LIMITS:
        raise ValueError("limit must be one of: 10, 25, 50")

    filters = filters or CadenceSuggestFilters()
    merchants = list_merchants_needing_cadence(conn, limit=limit, filters=filters)
    suggestions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    heuristic_count = 0
    llm_count = 0
    fallback_count = 0

    for row in merchants:
        mk = str(row.get("merchant_key") or "").strip()
        if not mk:
            continue
        try:
            sug = suggest_cadence_for_merchant(conn, mk)
            suggestions.append(sug)
            src = str(sug.get("source") or "")
            if src == "heuristic":
                heuristic_count += 1
            elif src == "llm":
                llm_count += 1
            elif src == "fallback":
                fallback_count += 1
        except Exception as exc:
            errors.append({"merchant_key": mk, "error": str(exc)})

    return {
        "limit": limit,
        "processed": len(merchants),
        "suggestion_count": len(suggestions),
        "heuristic_count": heuristic_count,
        "llm_count": llm_count,
        "fallback_count": fallback_count,
        "error_count": len(errors),
        "suggestions": suggestions,
        "errors": errors,
        "queue_total": count_merchants_needing_cadence(conn, filters=filters),
        "filters": filters.to_dict(),
        "filter_labels": filters.active_labels(),
        "message": (
            f"Suggested cadence for {len(suggestions)} of {len(merchants)} merchant(s) "
            f"({heuristic_count} heuristic, {llm_count} AI, {fallback_count} fallback)."
        ),
    }
