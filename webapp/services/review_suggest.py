from __future__ import annotations

import json
import re
import sqlite3
from difflib import SequenceMatcher
from typing import Any

from webapp.processing import (
    CUSTOM_RULES_SHEET,
    MERCHANT_CATEGORIES_SHEET,
    load_active_custom_rules,
    load_lookup_workbook,
    normalize_custom_rules_sheet,
)
from webapp.services.categorize import list_review_items
from webapp.services.llm import chat_completion, extract_json
from webapp.services.review_confirm import (
    FLOW_TYPES,
    _custom_rule_targets_merchant,
    _labels_from_business_row,
    _labels_from_custom_set,
    _labels_from_merchant_row,
    _norm,
    align_classification_with_category,
    classification_adjusted_for_category,
    lookup_workbook_path,
)
from webapp.services.review_options import get_review_options

REVIEW_SUGGEST_BATCH_LIMITS = frozenset({10, 25, 50, 100})

SUGGEST_SYSTEM_PROMPT = """You suggest transaction labels for a personal finance app.

Use the merchant name, bank descriptions, amounts, existing pending labels, lookup rules,
similar confirmed merchants, and recently confirmed merchants to propose labels.

Return ONLY valid JSON:
{
  "ai_category": "<required>",
  "ai_sub_category": "<string or empty>",
  "flow_type": "Expense|Income|Transfer|Adjustment",
  "expense_type": "Fixed|Variable",
  "classification": "Personal|Business",
  "confidence": "high|medium|low",
  "rationale": "<one short sentence>"
}

Guidelines:
- Prefer lookup_rules and similar_confirmed_merchants when they clearly match.
- flow_type: Expense for normal spending; Income for payroll/interest; Transfer for account moves;
  Adjustment for refunds/credits.
- expense_type: Fixed for subscriptions/rent/utilities; Variable for discretionary spend.
- classification: Business when ai_category is Business Expenses or Business; Personal otherwise.
- Use only category names from allowed_categories when possible; sub_category from allowed_sub_categories or invent a sensible one.
- Be concise in rationale."""


def _normalize_flow(value: str) -> str:
    flow = _norm(value) or "Expense"
    return flow if flow in FLOW_TYPES else "Expense"


def _normalize_expense_type(value: str) -> str:
    t = _norm(value) or "Variable"
    return t if t in ("Fixed", "Variable") else "Variable"


def _normalize_classification(value: str) -> str:
    c = _norm(value) or "Personal"
    return c if c in ("Personal", "Business") else "Personal"


def _labels_dict(
    *,
    ai_category: str,
    ai_sub_category: str = "",
    flow_type: str = "Expense",
    expense_type: str = "Variable",
    classification: str = "Personal",
) -> dict[str, str]:
    return align_classification_with_category(
        {
            "ai_category": _norm(ai_category),
            "ai_sub_category": _norm(ai_sub_category),
            "flow_type": _normalize_flow(flow_type),
            "expense_type": _normalize_expense_type(expense_type),
            "classification": _normalize_classification(classification),
        }
    )


def _finalize_suggestion(
    result: dict[str, Any],
    *,
    before_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    labels = align_classification_with_category(dict(result.get("labels") or {}))
    out = dict(result)
    out["labels"] = labels
    prior = before_labels or dict(result.get("labels") or {})
    if classification_adjusted_for_category(prior, labels):
        note = "Classification set to Business for business category."
        rationale = str(out.get("rationale") or "").strip()
        out["rationale"] = f"{rationale} {note}".strip() if rationale else note
    return out


def _merchant_row_from_excel(merchant_key: str) -> tuple[dict[str, str], str] | None:
    path = lookup_workbook_path()
    if not path.is_file():
        return None
    lookups = load_lookup_workbook(path)
    mk_lower = merchant_key.strip().lower()

    sheet = lookups.get(MERCHANT_CATEGORIES_SHEET)
    if sheet is not None and not sheet.empty and "Merchant Key" in sheet.columns:
        for _, row in sheet.iterrows():
            if _norm(row.get("Merchant Key")).lower() != mk_lower:
                continue
            labels = _labels_from_merchant_row(row)
            if labels["ai_category"]:
                return labels, MERCHANT_CATEGORIES_SHEET

    business = lookups.get("BusinessCategoryRules")
    if business is not None and not business.empty and "Generated Description" in business.columns:
        for _, row in business.iterrows():
            if _norm(row.get("Generated Description")).lower() != mk_lower:
                continue
            labels = _labels_from_business_row(row)
            if labels["ai_category"]:
                return labels, "BusinessCategoryRules"

    custom_sheet = normalize_custom_rules_sheet(lookups.get(CUSTOM_RULES_SHEET))
    for rule in load_active_custom_rules(custom_sheet):
        if str(rule.get("rule_type", "")).lower() != "assign":
            continue
        if not _custom_rule_targets_merchant(rule, merchant_key):
            continue
        spec = rule.get("set") or {}
        labels = _labels_from_custom_set(spec)
        if labels["ai_category"]:
            return labels, CUSTOM_RULES_SHEET

    return None


def _sqlite_merchant_label(conn: sqlite3.Connection, merchant_key: str) -> dict[str, str] | None:
    row = conn.execute(
        """
        SELECT ai_category, ai_sub_category, expense_type, label_status
        FROM merchant_labels
        WHERE merchant_key = ? AND label_status = 'confirmed'
        """,
        (merchant_key,),
    ).fetchone()
    if not row or not _norm(row["ai_category"]):
        return None
    tx = conn.execute(
        """
        SELECT flow_type, classification
        FROM transactions
        WHERE merchant_key = ?
        ORDER BY date DESC, id DESC
        LIMIT 1
        """,
        (merchant_key,),
    ).fetchone()
    return _labels_dict(
        ai_category=row["ai_category"],
        ai_sub_category=row["ai_sub_category"] or "",
        expense_type=row["expense_type"] or "Variable",
        flow_type=tx["flow_type"] if tx else "Expense",
        classification=tx["classification"] if tx else "Personal",
    )


def _lookup_suggestion(
    conn: sqlite3.Connection,
    merchant_key: str,
) -> dict[str, Any] | None:
    excel = _merchant_row_from_excel(merchant_key)
    if excel:
        labels, source = excel
        return {
            "labels": labels,
            "confidence": "high",
            "source": source,
            "rationale": f"Matched existing rule in {source}.",
        }

    sqlite_labels = _sqlite_merchant_label(conn, merchant_key)
    if sqlite_labels:
        return {
            "labels": sqlite_labels,
            "confidence": "high",
            "source": "merchant_labels",
            "rationale": "Matched confirmed merchant label in database.",
        }
    return None


def _tokenize_merchant(name: str) -> set[str]:
    parts = re.split(r"[\s/\-_.]+", name.lower())
    return {p for p in parts if len(p) >= 3}


def _similar_confirmed_merchants(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    mk_tokens = _tokenize_merchant(merchant_key)
    rows = conn.execute(
        """
        SELECT ml.merchant_key,
               ml.ai_category,
               ml.ai_sub_category,
               ml.expense_type,
               ml.updated_at,
               (
                 SELECT flow_type FROM transactions t
                 WHERE t.merchant_key = ml.merchant_key
                 ORDER BY t.date DESC, t.id DESC LIMIT 1
               ) AS flow_type,
               (
                 SELECT classification FROM transactions t
                 WHERE t.merchant_key = ml.merchant_key
                 ORDER BY t.date DESC, t.id DESC LIMIT 1
               ) AS classification
        FROM merchant_labels ml
        WHERE ml.label_status = 'confirmed'
          AND ml.merchant_key != ?
          AND ml.ai_category IS NOT NULL
          AND TRIM(ml.ai_category) != ''
        ORDER BY ml.updated_at DESC
        LIMIT 80
        """,
        (merchant_key,),
    ).fetchall()

    scored: list[tuple[float, dict[str, Any]]] = []
    mk_lower = merchant_key.lower()
    for row in rows:
        other = str(row["merchant_key"] or "")
        other_lower = other.lower()
        ratio = SequenceMatcher(None, mk_lower, other_lower).ratio()
        token_overlap = len(mk_tokens & _tokenize_merchant(other)) / max(len(mk_tokens), 1)
        score = max(ratio, token_overlap)
        if score < 0.35 and not (mk_tokens & _tokenize_merchant(other)):
            continue
        scored.append(
            (
                score,
                {
                    "merchant_key": other,
                    "ai_category": row["ai_category"],
                    "ai_sub_category": row["ai_sub_category"],
                    "expense_type": row["expense_type"],
                    "flow_type": row["flow_type"],
                    "classification": row["classification"],
                    "score": round(score, 2),
                },
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _gather_item_context(
    conn: sqlite3.Connection,
    item: dict[str, Any],
) -> dict[str, Any]:
    merchant_key = str(item.get("merchant_key") or "").strip()
    ctx: dict[str, Any] = {
        "merchant_key": merchant_key,
        "review_mode": item.get("review_mode"),
        "transaction_id": item.get("transaction_id"),
        "pending_labels": _labels_dict(
            ai_category=item.get("ai_category") or "",
            ai_sub_category=item.get("ai_sub_category") or "",
            flow_type=item.get("flow_type") or "Expense",
            expense_type=item.get("expense_type") or "Variable",
            classification=item.get("classification") or "Personal",
        ),
        "confidence": item.get("confidence"),
        "sample_description": item.get("sample_description"),
        "transaction_count": item.get("transaction_count"),
    }
    if item.get("amount") is not None:
        ctx["amount"] = item.get("amount")
    if item.get("date"):
        ctx["date"] = item.get("date")

    stats = conn.execute(
        """
        SELECT COUNT(*) AS c,
               MIN(source_category) AS sample_source_category
        FROM transactions
        WHERE merchant_key = ?
        """,
        (merchant_key,),
    ).fetchone()
    ctx["merchant_transaction_count"] = stats["c"] if stats else 0
    ctx["sample_source_category"] = stats["sample_source_category"] if stats else ""

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
    ctx["top_amounts"] = [{"amount": r["amount"], "count": r["c"]} for r in amounts]

    descriptions = conn.execute(
        """
        SELECT DISTINCT COALESCE(
                   NULLIF(simple_description, ''),
                   NULLIF(user_description, ''),
                   NULLIF(original_description, '')
               ) AS d
        FROM transactions
        WHERE merchant_key = ?
          AND COALESCE(
                NULLIF(simple_description, ''),
                NULLIF(user_description, ''),
                NULLIF(original_description, '')
              ) IS NOT NULL
        LIMIT 5
        """,
        (merchant_key,),
    ).fetchall()
    ctx["sample_descriptions"] = [r["d"] for r in descriptions if r["d"]]

    ctx["similar_confirmed_merchants"] = _similar_confirmed_merchants(conn, merchant_key)

    recent = conn.execute(
        """
        SELECT merchant_key, ai_category, ai_sub_category, expense_type, updated_at
        FROM merchant_labels
        WHERE label_status = 'confirmed'
        ORDER BY updated_at DESC
        LIMIT 12
        """,
    ).fetchall()
    ctx["recently_confirmed"] = [dict(r) for r in recent]

    lookup = _merchant_row_from_excel(merchant_key)
    if lookup:
        ctx["lookup_rules"] = {"source": lookup[1], "labels": lookup[0]}

    return ctx


def _llm_suggestion(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    options = get_review_options(conn)
    payload = {
        **context,
        "allowed_categories": options.get("categories") or [],
        "allowed_sub_categories": (options.get("sub_categories") or [])[:120],
        "allowed_flow_types": options.get("flow_types") or list(FLOW_TYPES),
        "allowed_expense_types": options.get("expense_types") or ["Fixed", "Variable"],
    }
    raw = chat_completion(
        [
            {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Suggest labels for this review queue item. Respond with JSON only.\n\n"
                    f"{json.dumps(payload, indent=2, default=str)}"
                ),
            },
        ],
        temperature=0.2,
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object from LLM")

    labels = _labels_dict(
        ai_category=str(parsed.get("ai_category") or ""),
        ai_sub_category=str(parsed.get("ai_sub_category") or ""),
        flow_type=str(parsed.get("flow_type") or "Expense"),
        expense_type=str(parsed.get("expense_type") or "Variable"),
        classification=str(parsed.get("classification") or "Personal"),
    )
    if not labels["ai_category"]:
        raise ValueError("LLM returned empty ai_category")

    return {
        "labels": labels,
        "confidence": str(parsed.get("confidence") or "medium").strip().lower(),
        "source": "llm",
        "rationale": str(parsed.get("rationale") or "AI suggestion from merchant context.").strip(),
    }


def _find_review_item(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    for item in list_review_items(conn):
        if str(item.get("merchant_key") or "").strip() != merchant_key.strip():
            continue
        if transaction_id:
            if str(item.get("transaction_id") or "") == str(transaction_id):
                return item
            continue
        if item.get("review_mode") == "transaction":
            continue
        return item
    raise ValueError(f"Review item not found for merchant_key={merchant_key!r}")


def suggest_labels_for_merchant(
    conn: sqlite3.Connection,
    merchant_key: str,
    *,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    item = _find_review_item(conn, merchant_key, transaction_id=transaction_id)
    return suggest_labels_for_item(conn, item)


def suggest_labels_for_item(
    conn: sqlite3.Connection,
    item: dict[str, Any],
) -> dict[str, Any]:
    merchant_key = str(item.get("merchant_key") or "").strip()
    if not merchant_key:
        raise ValueError("merchant_key is required")

    lookup = _lookup_suggestion(conn, merchant_key)
    if lookup and lookup["labels"].get("ai_category"):
        return _finalize_suggestion(
            {
                "merchant_key": merchant_key,
                "transaction_id": item.get("transaction_id"),
                "review_mode": item.get("review_mode"),
                **lookup,
            },
            before_labels=lookup.get("labels"),
        )

    context = _gather_item_context(conn, item)
    try:
        result = _llm_suggestion(conn, item, context)
    except Exception as exc:
        pending = context["pending_labels"]
        if pending.get("ai_category"):
            result = {
                "labels": pending,
                "confidence": "low",
                "source": "unchanged",
                "rationale": f"Kept pending labels (suggestion failed: {exc}).",
            }
        else:
            raise

    return _finalize_suggestion(
        {
            "merchant_key": merchant_key,
            "transaction_id": item.get("transaction_id"),
            "review_mode": item.get("review_mode"),
            **result,
        },
        before_labels=result.get("labels"),
    )


def suggest_labels_bulk(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    if limit not in REVIEW_SUGGEST_BATCH_LIMITS:
        raise ValueError("limit must be one of: 10, 25, 50, 100")

    items = list_review_items(conn)
    batch = items[:limit]
    suggestions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    lookup_count = 0
    llm_count = 0
    unchanged_count = 0

    for item in batch:
        mk = str(item.get("merchant_key") or "")
        try:
            sug = suggest_labels_for_item(conn, item)
            suggestions.append(sug)
            src = sug.get("source") or ""
            if src in (MERCHANT_CATEGORIES_SHEET, "BusinessCategoryRules", CUSTOM_RULES_SHEET, "merchant_labels"):
                lookup_count += 1
            elif src == "llm":
                llm_count += 1
            elif src == "unchanged":
                unchanged_count += 1
        except Exception as exc:
            errors.append({"merchant_key": mk, "error": str(exc)})

    return {
        "limit": limit,
        "processed": len(batch),
        "suggestion_count": len(suggestions),
        "lookup_count": lookup_count,
        "llm_count": llm_count,
        "unchanged_count": unchanged_count,
        "error_count": len(errors),
        "suggestions": suggestions,
        "errors": errors,
        "message": (
            f"Suggested labels for {len(suggestions)} of {len(batch)} group(s) "
            f"({lookup_count} from lookups, {llm_count} from AI)."
        ),
    }
