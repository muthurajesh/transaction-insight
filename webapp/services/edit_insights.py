from __future__ import annotations

import json
import sqlite3
from typing import Any

from webapp.services.custom_rule_similarity import suppress_duplicate_rule_suggestion
from webapp.services.custom_rules import list_custom_rules
from webapp.services.llm import chat_completion, extract_json

EDIT_INSIGHT_SYSTEM_PROMPT = """You are a personal finance assistant helping a user refine transaction labels.

The user just edited one or more transactions in their ledger. Your job is to:
1. Explain the pattern you see in their data (merchant frequency, amounts, months, scope of edit).
2. Advise on their workflow: when a CustomRule helps vs when a one-off edit is enough.
3. If appropriate, suggest ONE plain-English CustomRule for the user's custom rules.

CustomRule examples (plain English — another step compiles these to JSON):
- "When Generated Description is Merchant A set ai_category Category X, ai_sub_category Sub Y, type Fixed"
- "When Generated Description is *Vendor* and amount is 5.75 set ai_category Category Z, ai_sub_category Sub W"
- "When Generated Description is Merchant B monthly split: highest amount Category A / Sub 1, others Category C / Sub 2"

Rules use Generated Description (same as merchant_key in the app). Use *wildcards* for contains.
Set fields: ai_category, ai_sub_category, type (Fixed|Variable), classification (Personal|Business), category.

Return ONLY valid JSON:
{
  "insight": "<2-4 sentences, markdown ok, user-facing>",
  "pattern_summary": "<one short line>",
  "suggested_rule": "<plain English rule or empty string>",
  "recommend_save_rule": <boolean>,
  "confidence": "high|medium|low",
  "future_note": "<what happens on next CSV import / pipeline run>",
  "data_notes": ["<optional bullet about stats>"]
}

Guidelines:
- recommend_save_rule=true when scope is merchant or merchant_amount, or many similar rows exist.
- recommend_save_rule=false for a single one-off correction with no repeating pattern.
- If update_merchant_label was true, mention merchant_labels already covers future LLM categorization for that merchant.
- If existing_custom_rules already covers this merchant/pattern, set recommend_save_rule=false and suggested_rule="".
- Be concise and practical; no filler."""


def _labels_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    keys = ("ai_category", "ai_sub_category", "expense_type", "classification")
    for key in keys:
        if (before.get(key) or "").strip() != (after.get(key) or "").strip():
            return True
    return False


def _gather_stats(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    amount: float | None,
    before: dict[str, str],
) -> dict[str, Any]:
    stats: dict[str, Any] = {"merchant_key": merchant_key}
    stats["merchant_transaction_count"] = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE merchant_key = ?",
        (merchant_key,),
    ).fetchone()["c"]

    if amount is not None:
        stats["same_amount_count"] = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE merchant_key = ? AND amount = ?",
            (merchant_key, amount),
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

    old_cat = (before.get("ai_category") or "").strip()
    if old_cat:
        stats["still_old_category_count"] = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE merchant_key = ? AND ai_category = ?",
            (merchant_key, old_cat),
        ).fetchone()["c"]

    ml = conn.execute(
        "SELECT ai_category, ai_sub_category, expense_type, label_status FROM merchant_labels WHERE merchant_key = ?",
        (merchant_key,),
    ).fetchone()
    if ml:
        stats["merchant_label"] = dict(ml)

    amounts = conn.execute(
        """
        SELECT amount, COUNT(*) AS c
        FROM transactions
        WHERE merchant_key = ?
        GROUP BY amount
        ORDER BY c DESC
        LIMIT 5
        """,
        (merchant_key,),
    ).fetchall()
    stats["top_amounts"] = [{"amount": r["amount"], "count": r["c"]} for r in amounts]
    return stats


def _fallback_insight(
    *,
    merchant_key: str,
    scope: str,
    before: dict[str, str],
    after: dict[str, str],
    rows_updated: int,
    update_merchant_label: bool,
    stats: dict[str, Any],
) -> dict[str, Any]:
    scope_labels = {
        "single": "this transaction only",
        "merchant_amount": "same merchant and amount",
        "merchant": "all transactions for this merchant",
    }
    scope_text = scope_labels.get(scope, scope)
    parts = [
        f"You updated **{rows_updated}** row(s) for **{merchant_key}** ({scope_text}).",
        f"Labels changed from **{before.get('ai_category') or '—'}** to **{after.get('ai_category') or '—'}**.",
    ]
    if update_merchant_label:
        parts.append(
            "A merchant label was saved, so future pipeline runs can categorize this payee without re-editing."
        )
    elif scope in ("merchant", "merchant_amount") or stats.get("merchant_transaction_count", 0) > 3:
        parts.append(
            "Consider saving a CustomRule so new imports get the same labels automatically."
        )

    rule = ""
    recommend = scope in ("merchant", "merchant_amount") or stats.get("merchant_transaction_count", 0) > 2
    if recommend:
        desc = merchant_key if " " not in merchant_key else f"*{merchant_key}*"
        rule_parts = [f"When Generated Description is {desc}"]
        if scope == "merchant_amount" and stats.get("top_amounts"):
            amt = stats["top_amounts"][0]["amount"]
            rule_parts.append(f"and amount is {abs(float(amt)):.2f}")
        sets = [f"ai_category {after.get('ai_category', '')}"]
        if after.get("ai_sub_category"):
            sets.append(f"ai_sub_category {after['ai_sub_category']}")
        if after.get("expense_type"):
            sets.append(f"type {after['expense_type']}")
        if after.get("classification"):
            sets.append(f"classification {after['classification']}")
        rule = " ".join(rule_parts) + " set " + ", ".join(sets)

    return {
        "insight": " ".join(parts),
        "pattern_summary": f"{stats.get('merchant_transaction_count', 0)} transactions for this merchant",
        "suggested_rule": rule,
        "recommend_save_rule": recommend and bool(rule),
        "confidence": "medium",
        "future_note": (
            "Edits are in SQLite only until you add a CustomRule or merchant label; "
            "re-imported CSV rows are categorized by the pipeline (lookups + LLM)."
        ),
        "data_notes": [],
        "stats": stats,
        "source": "fallback",
    }


def analyze_edit(
    conn: sqlite3.Connection,
    *,
    merchant_key: str,
    scope: str,
    rows_updated: int,
    before: dict[str, str],
    after: dict[str, str],
    amount: float | None = None,
    update_merchant_label: bool = False,
) -> dict[str, Any]:
    merchant_key = (merchant_key or "").strip()
    if not merchant_key:
        raise ValueError("merchant_key is required")

    scope = (scope or "single").strip().lower()
    if scope not in ("single", "merchant_amount", "merchant"):
        raise ValueError("scope must be single, merchant_amount, or merchant")

    before_norm = {
        "ai_category": (before.get("ai_category") or "").strip(),
        "ai_sub_category": (before.get("ai_sub_category") or "").strip(),
        "expense_type": (before.get("expense_type") or "").strip(),
        "classification": (before.get("classification") or "").strip(),
    }
    after_norm = {
        "ai_category": (after.get("ai_category") or "").strip(),
        "ai_sub_category": (after.get("ai_sub_category") or "").strip(),
        "expense_type": (after.get("expense_type") or "").strip(),
        "classification": (after.get("classification") or "").strip(),
    }

    stats = _gather_stats(conn, merchant_key=merchant_key, amount=amount, before=before_norm)
    existing_rules = list_custom_rules(conn).get("rules") or []

    def _finalize(result: dict[str, Any]) -> dict[str, Any]:
        return suppress_duplicate_rule_suggestion(
            result,
            merchant_key=merchant_key,
            after_labels=after_norm,
            amount=amount,
            existing_rules=existing_rules,
        )

    if not _labels_changed(before_norm, after_norm):
        return {
            "insight": "Labels were unchanged — no pattern to learn from this edit.",
            "pattern_summary": "No label change",
            "suggested_rule": "",
            "recommend_save_rule": False,
            "confidence": "high",
            "future_note": "",
            "data_notes": [],
            "stats": stats,
            "source": "none",
        }

    user_payload = {
        "merchant_key": merchant_key,
        "scope": scope,
        "rows_updated": rows_updated,
        "update_merchant_label": update_merchant_label,
        "before_labels": before_norm,
        "after_labels": after_norm,
        "amount": amount,
        "data_stats": stats,
        "existing_custom_rules": [
            {"rule": r.get("rule"), "status": r.get("status")}
            for r in existing_rules[:25]
            if r.get("rule")
        ],
    }

    try:
        raw = chat_completion(
            [
                {"role": "system", "content": EDIT_INSIGHT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyze this edit and respond with JSON only.\n\n"
                        f"{json.dumps(user_payload, indent=2)}"
                    ),
                },
            ],
            temperature=0.3,
            caller="edit.insight",
        )
        parsed = extract_json(raw)
        if isinstance(parsed, dict) and "rule" in parsed and "insight" not in parsed:
            parsed = parsed.get("rule") or parsed
        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object")

        result = {
            "insight": str(parsed.get("insight") or "").strip(),
            "pattern_summary": str(parsed.get("pattern_summary") or "").strip(),
            "suggested_rule": str(parsed.get("suggested_rule") or "").strip(),
            "recommend_save_rule": bool(parsed.get("recommend_save_rule")),
            "confidence": str(parsed.get("confidence") or "medium").strip().lower(),
            "future_note": str(parsed.get("future_note") or "").strip(),
            "data_notes": list(parsed.get("data_notes") or []),
            "stats": stats,
            "source": "llm",
        }
        if not result["insight"]:
            raise ValueError("Empty insight from LLM")
        return _finalize(result)
    except Exception:
        return _finalize(
            _fallback_insight(
                merchant_key=merchant_key,
                scope=scope,
                before=before_norm,
                after=after_norm,
                rows_updated=rows_updated,
                update_merchant_label=update_merchant_label,
                stats=stats,
            )
        )
