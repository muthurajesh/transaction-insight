from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from webapp.processing import (
    CADENCE_MIN_HISTORY_MONTHS,
    CADENCE_SPIKE_RATIO,
    CADENCE_YEARLY_GAP_MAX,
    CADENCE_YEARLY_GAP_MIN,
)

from webapp.services.cadence_rule_similarity import suppress_duplicate_cadence_proposal
from webapp.services.expense_cadence import (
    CADENCE_KIND_LUMP,
    CADENCE_KIND_RECURRING,
    CADENCE_KIND_UNKNOWN,
    effective_amount,
    get_cadence_rule,
    parse_flexible_cadence_text,
    resolve_effective_cadence,
    _normalize_kind,
)
from webapp.services.llm import chat_completion, extract_json

CADENCE_INSIGHT_SYSTEM_PROMPT = """You are a personal finance assistant helping a user set expense cadence for a merchant.

Cadence describes how a charge should appear in monthly reporting:
- recurring — repeats on a schedule (monthly, bi-weekly, etc.)
- lump — large periodic charge spread for normalized view (yearly insurance, semi-annual)
- one_time — single unexpected charge
- exclude — ignore for run-rate / normalized views
- unknown — insufficient evidence

Return ONLY valid JSON:
{
  "insight": "<2-4 sentences explaining the charge pattern, markdown ok>",
  "cadence_kind": "recurring|lump|one_time|exclude|unknown",
  "period_count": <int or null>,
  "period_unit": "months|weeks|days|null",
  "include_in_run_rate": <boolean>,
  "cadence_note": "<short note or empty>",
  "confidence": "high|medium|low",
  "recommend_save_rule": <boolean>,
  "apply_scope": "merchant|transaction"
}

Guidelines:
- Annual insurance (~once per 12 months, large spike): lump, period_count=12, period_unit=months, include_in_run_rate=false.
- Monthly subscriptions: recurring, period_count=1, period_unit=months, include_in_run_rate=true.
- Bi-weekly payroll-like: recurring, period_count=2, period_unit=weeks.
- User hint overrides weak stats when plausible.
- recommend_save_rule=true when pattern repeats across months for this merchant.
- recommend_save_rule=false for one-off or when existing_cadence_rule already matches.
- apply_scope=merchant when pattern applies to all future charges for this payee.
- Be concise; cite amounts and months from data_stats."""


def _month_index(value: str) -> int | None:
    text = str(value or "").strip()
    if len(text) < 7 or text[4] != "-":
        return None
    try:
        year = int(text[:4])
        month = int(text[5:7])
        if month < 1 or month > 12:
            return None
        return year * 12 + month
    except ValueError:
        return None


def gather_merchant_cadence_pattern(
    conn: sqlite3.Connection,
    merchant_key: str,
) -> dict[str, Any]:
    """
    Infer spend rhythm from SQLite history (mirrors pipeline analyze_merchant_cadence_profiles).
    """
    monthly_rows = conn.execute(
        """
        SELECT budget_month AS m,
               SUM(ABS(amount)) AS total,
               COUNT(*) AS tx_count
        FROM transactions
        WHERE merchant_key = ?
          AND flow_type = 'Expense'
          AND budget_month IS NOT NULL AND TRIM(budget_month) != ''
        GROUP BY budget_month
        ORDER BY m
        """,
        (merchant_key,),
    ).fetchall()

    totals = [float(r["total"] or 0) for r in monthly_rows]
    months = [str(r["m"]) for r in monthly_rows]
    months_active = len(months)

    dominant_rows = conn.execute(
        """
        SELECT amount, COUNT(*) AS c
        FROM transactions
        WHERE merchant_key = ? AND flow_type = 'Expense'
        GROUP BY amount
        ORDER BY c DESC, ABS(amount) DESC
        LIMIT 3
        """,
        (merchant_key,),
    ).fetchall()
    dominant_amount = None
    dominant_count = 0
    if dominant_rows:
        dominant_amount = float(dominant_rows[0]["amount"] or 0)
        dominant_count = int(dominant_rows[0]["c"] or 0)

    pattern = "monthly"
    confidence = "low"
    note = ""
    high_months: list[str] = []
    gap_months: list[int] = []
    typical = 0.0

    dominant_gap_months: list[int] = []
    if dominant_amount is not None and dominant_count >= 2:
        dom_indices = conn.execute(
            """
            SELECT DISTINCT budget_month AS m
            FROM transactions
            WHERE merchant_key = ?
              AND flow_type = 'Expense'
              AND amount = ?
              AND budget_month IS NOT NULL AND TRIM(budget_month) != ''
            ORDER BY m
            """,
            (merchant_key, dominant_amount),
        ).fetchall()
        dom_month_idxs = sorted(
            idx
            for idx in (_month_index(str(r["m"])) for r in dom_indices)
            if idx is not None
        )
        dominant_gap_months = [
            dom_month_idxs[i + 1] - dom_month_idxs[i] for i in range(len(dom_month_idxs) - 1)
        ]
        if any(CADENCE_YEARLY_GAP_MIN <= g <= CADENCE_YEARLY_GAP_MAX for g in dominant_gap_months):
            pattern = "yearly"
            confidence = "high"
            high_months = [str(r["m"]) for r in dom_indices]
            gap_months = dominant_gap_months
            amt = abs(float(dominant_amount))
            note = (
                f"Repeating ~${amt:,.2f} charge every ~12 months "
                f"({dominant_count} occurrence(s))"
            )

    if pattern == "monthly" and months_active >= CADENCE_MIN_HISTORY_MONTHS and totals:
        sorted_totals = sorted(totals)
        mid = len(sorted_totals) // 2
        typical = (
            float(sorted_totals[mid])
            if len(sorted_totals) % 2 == 1
            else (float(sorted_totals[mid - 1]) + float(sorted_totals[mid])) / 2.0
        )
        if typical > 0:
            threshold = typical * CADENCE_SPIKE_RATIO
            high_months = [
                months[i] for i, total in enumerate(totals) if float(total) >= threshold
            ]
            note = f"Typical month ~${typical:,.2f}"
            if len(high_months) >= 2:
                indices = sorted(
                    i for i, m in enumerate(months) if m in high_months and _month_index(m) is not None
                )
                gap_months = []
                for i in range(len(indices) - 1):
                    a = _month_index(months[indices[i]])
                    b = _month_index(months[indices[i + 1]])
                    if a is not None and b is not None:
                        gap_months.append(b - a)
                if any(CADENCE_YEARLY_GAP_MIN <= g <= CADENCE_YEARLY_GAP_MAX for g in gap_months):
                    pattern = "yearly"
                    confidence = "high"
                    note = (
                        f"{len(high_months)} spike month(s); ~annual pattern "
                        f"(typical month ~${typical:,.2f})"
                    )
                else:
                    pattern = "unplanned"
                    note = (
                        f"{len(high_months)} spike month(s); no annual spacing "
                        f"(typical ~${typical:,.2f})"
                    )
            elif len(high_months) == 1:
                pattern = "onetime"
                note = f"Single spike month in history (typical ~${typical:,.2f})"
            elif months_active >= CADENCE_MIN_HISTORY_MONTHS:
                pattern = "monthly"
                confidence = "medium"
                note = f"Spend in {months_active} month(s); no large spikes vs typical ~${typical:,.2f}"

    return {
        "pattern": pattern,
        "confidence": confidence,
        "typical_monthly": typical,
        "high_months": high_months,
        "gap_months_between_spikes": gap_months or dominant_gap_months,
        "dominant_amount_gaps": dominant_gap_months,
        "months_active": months_active,
        "monthly_spend": [
            {"month": str(r["m"]), "total": float(r["total"] or 0), "tx_count": int(r["tx_count"] or 0)}
            for r in monthly_rows
        ],
        "dominant_amount": dominant_amount,
        "dominant_amount_count": dominant_count,
        "note": note,
    }


def find_merchant_key_from_text(conn: sqlite3.Connection, text: str) -> str | None:
    """Resolve merchant_key from chat text or explicit hint."""
    raw = (text or "").strip()
    if not raw:
        return None

    exact = conn.execute(
        "SELECT merchant_key FROM transactions WHERE merchant_key = ? LIMIT 1",
        (raw,),
    ).fetchone()
    if exact:
        return str(exact["merchant_key"])

    msg = raw.lower()
    rows = conn.execute(
        """
        SELECT DISTINCT merchant_key FROM transactions
        WHERE merchant_key IS NOT NULL AND TRIM(merchant_key) != ''
        ORDER BY LENGTH(merchant_key) DESC
        """
    ).fetchall()

    best_key: str | None = None
    best_score = 0
    for row in rows:
        mk = str(row["merchant_key"] or "").strip()
        if not mk:
            continue
        mk_lower = mk.lower()
        if mk_lower in msg:
            return mk
        words = [w for w in re.split(r"\W+", mk_lower) if len(w) >= 4]
        if not words:
            continue
        matched = sum(1 for w in words if w in msg)
        threshold = max(1, (len(words) + 1) // 2)
        if matched >= threshold and matched > best_score:
            best_key = mk
            best_score = matched
    return best_key


def _gather_merchant_stats(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {"merchant_key": merchant_key}
    stats["merchant_transaction_count"] = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE merchant_key = ?",
        (merchant_key,),
    ).fetchone()["c"]

    months = conn.execute(
        """
        SELECT DISTINCT budget_month AS m
        FROM transactions
        WHERE merchant_key = ? AND budget_month IS NOT NULL AND budget_month != ''
        ORDER BY m
        """,
        (merchant_key,),
    ).fetchall()
    stats["months"] = [r["m"] for r in months]
    stats["months_active"] = len(stats["months"])

    amounts = conn.execute(
        """
        SELECT amount, COUNT(*) AS c
        FROM transactions
        WHERE merchant_key = ?
        GROUP BY amount
        ORDER BY c DESC, ABS(amount) DESC
        LIMIT 5
        """,
        (merchant_key,),
    ).fetchall()
    stats["top_amounts"] = [
        {"amount": float(r["amount"]), "count": r["c"]} for r in amounts
    ]

    samples = conn.execute(
        """
        SELECT transaction_id, date, budget_month, amount, ai_category
        FROM transactions
        WHERE merchant_key = ?
        ORDER BY date DESC
        LIMIT 12
        """,
        (merchant_key,),
    ).fetchall()
    stats["recent_transactions"] = [dict(r) for r in samples]

    if transaction_id:
        tx = conn.execute(
            """
            SELECT transaction_id, date, amount, ai_category, ai_sub_category,
                   expense_type, classification, cadence_kind
            FROM transactions WHERE transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
        if tx:
            stats["focus_transaction"] = dict(tx)

    existing = get_cadence_rule(conn, merchant_key)
    if existing:
        stats["existing_cadence_rule"] = existing

    resolved = resolve_effective_cadence(conn, merchant_key=merchant_key)
    stats["current_effective_cadence"] = resolved
    stats["pattern_features"] = gather_merchant_cadence_pattern(conn, merchant_key)
    return stats


def package_cadence_proposal(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    transaction_id: str | None,
    proposal: dict[str, Any],
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach merchant context, sample tx, previews, and duplicate suppression."""
    resolved_stats = stats or _gather_merchant_stats(
        conn, merchant_key=merchant_key, transaction_id=transaction_id
    )
    existing_rule = get_cadence_rule(conn, merchant_key)

    out = dict(proposal)
    out["merchant_key"] = merchant_key
    out["transaction_id"] = transaction_id
    out["stats"] = resolved_stats

    top_amounts = resolved_stats.get("top_amounts") or []
    if top_amounts:
        out["sample_amount"] = top_amounts[0]["amount"]

    sample_tx = None
    if top_amounts:
        target_amt = float(top_amounts[0]["amount"])
        for row in resolved_stats.get("recent_transactions") or []:
            if float(row.get("amount") or 0) == target_amt:
                sample_tx = row
                break
    if not sample_tx and resolved_stats.get("recent_transactions"):
        sample_tx = resolved_stats["recent_transactions"][0]
    if sample_tx:
        out["sample_transaction"] = {
            "transaction_id": sample_tx.get("transaction_id"),
            "ai_category": sample_tx.get("ai_category") or "Uncategorized",
            "ai_sub_category": "",
            "amount": sample_tx.get("amount"),
        }
        if out.get("sample_amount") is None:
            out["sample_amount"] = sample_tx.get("amount")

    out["effective_amounts"] = _preview_amounts(
        conn,
        merchant_key=merchant_key,
        transaction_id=transaction_id,
        proposal=out,
    )
    return suppress_duplicate_cadence_proposal(
        out,
        merchant_key=merchant_key,
        existing_rule=existing_rule,
    )


def _preview_amounts(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    transaction_id: str | None,
    proposal: dict[str, Any],
) -> dict[str, float]:
    amount = 0.0
    if transaction_id:
        tx = conn.execute(
            "SELECT amount FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if tx:
            amount = float(tx["amount"] or 0)
    if amount == 0.0 and proposal.get("sample_amount") is not None:
        amount = float(proposal["sample_amount"])
    if amount == 0.0:
        row = conn.execute(
            """
            SELECT amount FROM transactions
            WHERE merchant_key = ?
            ORDER BY ABS(amount) DESC
            LIMIT 1
            """,
            (merchant_key,),
        ).fetchone()
        if row:
            amount = float(row["amount"] or 0)

    kind = _normalize_kind(proposal.get("cadence_kind"))
    return {
        view: effective_amount(
            amount,
            view=view,
            kind=kind,
            period_count=proposal.get("period_count"),
            period_unit=proposal.get("period_unit"),
            include_in_run_rate=proposal.get("include_in_run_rate"),
        )
        for view in ("cash", "core", "normalized")
    }


def _fallback_proposal(
    *,
    merchant_key: str,
    hint: str,
    stats: dict[str, Any],
) -> dict[str, Any]:
    months = stats.get("months_active") or 0
    top = stats.get("top_amounts") or []
    hint_kind, hint_count, hint_unit = parse_flexible_cadence_text(hint)

    if hint_kind != CADENCE_KIND_UNKNOWN:
        kind, count, unit = hint_kind, hint_count, hint_unit
        confidence = "medium"
        insight = (
            f"Based on your note, **{merchant_key}** looks like "
            f"**{kind}** every **{count} {unit}**."
        )
        recommend = True
    elif len(top) == 1 and months >= 2:
        kind = CADENCE_KIND_LUMP
        count, unit = 12, "months"
        amt = abs(float(top[0]["amount"]))
        confidence = "medium"
        insight = (
            f"**{merchant_key}** has a repeating charge around **${amt:,.2f}** "
            f"across **{months}** month(s) — likely an annual lump to spread monthly."
        )
        recommend = months >= 3
    elif len(top) >= 1 and months >= 3:
        kind = CADENCE_KIND_RECURRING
        count, unit = 1, "months"
        amt = abs(float(top[0]["amount"]))
        confidence = "low"
        insight = (
            f"**{merchant_key}** shows **{stats.get('merchant_transaction_count', 0)}** "
            f"charges; top amount **${amt:,.2f}** — defaulting to monthly recurring."
        )
        recommend = False
    else:
        kind = CADENCE_KIND_UNKNOWN
        count, unit = None, None
        confidence = "low"
        insight = f"Not enough history for **{merchant_key}** to infer cadence confidently."
        recommend = False

    include_run = kind == CADENCE_KIND_RECURRING
    if kind == CADENCE_KIND_LUMP:
        include_run = False

    return {
        "insight": insight,
        "cadence_kind": kind,
        "period_count": count,
        "period_unit": unit,
        "include_in_run_rate": include_run,
        "cadence_note": (hint or "").strip()[:200],
        "confidence": confidence,
        "recommend_save_rule": recommend and kind != CADENCE_KIND_UNKNOWN,
        "apply_scope": "merchant",
        "data_notes": [],
        "source": "fallback",
    }


def _normalize_proposal(parsed: dict[str, Any]) -> dict[str, Any]:
    kind = _normalize_kind(parsed.get("cadence_kind"))
    period_count = parsed.get("period_count")
    period_unit = parsed.get("period_unit")
    if period_count is not None:
        try:
            period_count = int(period_count)
        except (TypeError, ValueError):
            period_count = None
    if period_unit is not None:
        period_unit = str(period_unit).strip().lower() or None

    include_raw = parsed.get("include_in_run_rate")
    if include_raw is None:
        include_flag: bool | None = None
    else:
        include_flag = bool(include_raw)

    scope = str(parsed.get("apply_scope") or "merchant").strip().lower()
    if scope not in ("merchant", "transaction"):
        scope = "merchant"

    return {
        "insight": str(parsed.get("insight") or "").strip(),
        "cadence_kind": kind,
        "period_count": period_count,
        "period_unit": period_unit,
        "include_in_run_rate": include_flag,
        "cadence_note": str(parsed.get("cadence_note") or "").strip(),
        "confidence": str(parsed.get("confidence") or "medium").strip().lower(),
        "recommend_save_rule": bool(parsed.get("recommend_save_rule")),
        "apply_scope": scope,
        "data_notes": list(parsed.get("data_notes") or []),
        "source": "llm",
    }


def propose_cadence(
    conn: sqlite3.Connection,
    *,
    merchant_key: str | None = None,
    transaction_id: str | None = None,
    hint: str = "",
) -> dict[str, Any]:
    resolved_key = (merchant_key or "").strip()
    if not resolved_key and transaction_id:
        row = conn.execute(
            "SELECT merchant_key FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if row:
            resolved_key = str(row["merchant_key"] or "").strip()
    if not resolved_key and hint:
        resolved_key = find_merchant_key_from_text(conn, hint) or ""
    if not resolved_key:
        raise ValueError("merchant_key is required (or provide transaction_id / hint with merchant name)")

    stats = _gather_merchant_stats(
        conn,
        merchant_key=resolved_key,
        transaction_id=transaction_id,
    )

    def _finalize(base: dict[str, Any]) -> dict[str, Any]:
        return package_cadence_proposal(
            conn,
            merchant_key=resolved_key,
            transaction_id=transaction_id,
            proposal=base,
            stats=stats,
        )

    user_payload = {
        "merchant_key": resolved_key,
        "transaction_id": transaction_id,
        "user_hint": (hint or "").strip(),
        "data_stats": stats,
        "existing_cadence_rule": stats.get("existing_cadence_rule"),
    }

    try:
        raw = chat_completion(
            [
                {"role": "system", "content": CADENCE_INSIGHT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Propose cadence for this merchant. Respond with JSON only.\n\n"
                        f"{json.dumps(user_payload, indent=2, default=str)}"
                    ),
                },
            ],
            temperature=0.3,
        )
        parsed = extract_json(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object")
        result = _normalize_proposal(parsed)
        if not result["insight"]:
            raise ValueError("Empty insight from LLM")
        return _finalize(result)
    except Exception:
        return _finalize(_fallback_proposal(merchant_key=resolved_key, hint=hint, stats=stats))


def propose_cadence_batch(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 25))
    rows = conn.execute(
        """
        SELECT t.merchant_key,
               COUNT(*) AS tx_count,
               COUNT(DISTINCT t.budget_month) AS month_count
        FROM transactions t
        LEFT JOIN cadence_rules cr
          ON cr.merchant_key = t.merchant_key AND cr.enabled = 1
        WHERE t.merchant_key IS NOT NULL AND TRIM(t.merchant_key) != ''
          AND t.flow_type = 'Expense'
          AND (t.cadence_kind IS NULL OR t.cadence_kind = '' OR t.cadence_kind = 'unknown')
          AND cr.merchant_key IS NULL
        GROUP BY t.merchant_key
        HAVING month_count >= 3
        ORDER BY tx_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    proposals: list[dict[str, Any]] = []
    for row in rows:
        mk = str(row["merchant_key"])
        try:
            proposals.append(propose_cadence(conn, merchant_key=mk))
        except ValueError:
            continue
    return {"count": len(proposals), "proposals": proposals}


def cadence_proposal_from_trace(trace: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for entry in reversed(trace or []):
        if entry.get("tool") != "propose_cadence_rule":
            continue
        result = entry.get("result") or {}
        if result.get("error"):
            continue
        if result.get("insight"):
            return result
    return None
