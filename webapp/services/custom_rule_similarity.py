from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_FIELD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "ai_category": [
        re.compile(r"ai[_\s-]*category\s+([^,;]+)", re.I),
        re.compile(r"ai\s+category\s*=\s*([^,;]+)", re.I),
    ],
    "ai_sub_category": [
        re.compile(r"ai[_\s-]*sub[_\s-]*category\s+([^,;]+)", re.I),
        re.compile(r"ai\s+sub[_\s-]*category\s*=\s*([^,;]+)", re.I),
    ],
    "expense_type": [
        re.compile(r"\btype\s+([^,;]+)", re.I),
        re.compile(r"\btype\s*=\s*([^,;]+)", re.I),
    ],
    "classification": [
        re.compile(r"\bclassification\s+([^,;]+)", re.I),
        re.compile(r"\bclassification\s*=\s*([^,;]+)", re.I),
    ],
}

_AMOUNT_PATTERN = re.compile(r"\bamount\s+is\s+([\d.]+)", re.I)


def normalize_rule_text(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace(""", '"').replace(""", '"').replace("'", '"')
    t = re.sub(r'["\']', "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_field_from_rule(rule_text: str, field: str) -> str:
    for pattern in _FIELD_PATTERNS.get(field, []):
        match = pattern.search(rule_text or "")
        if match:
            return (match.group(1) or "").strip()
    return ""


def extract_amount_from_rule(rule_text: str) -> str | None:
    match = _AMOUNT_PATTERN.search(rule_text or "")
    if not match:
        return None
    return match.group(1).strip()


def merchant_referenced_in_rule(rule_text: str, merchant_key: str) -> bool:
    mk = (merchant_key or "").strip()
    if not mk:
        return False
    rule_lower = (rule_text or "").lower()
    mk_lower = mk.lower()
    if mk_lower in rule_lower:
        return True
    words = [w for w in re.split(r"\W+", mk_lower) if len(w) >= 4]
    if len(words) >= 2 and words[0] in rule_lower and words[1] in rule_lower:
        return True
    if len(words) == 1 and len(words[0]) >= 6 and words[0] in rule_lower:
        return True
    return False


def _text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer and len(shorter) / max(len(longer), 1) >= 0.55:
        return max(ratio, 0.88)
    return ratio


def _entry_result(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule": str(entry.get("rule") or "").strip(),
        "status": str(entry.get("status") or "").strip() or "Pending",
        "index": entry.get("index"),
    }


def find_similar_custom_rule(
    *,
    suggested_rule: str = "",
    merchant_key: str = "",
    after_labels: dict[str, str] | None = None,
    amount: float | None = None,
    existing_rules: list[dict[str, Any]] | None = None,
    similarity_threshold: float = 0.82,
) -> dict[str, Any] | None:
    """Return an existing custom rule that overlaps the suggested rule, if any."""
    if existing_rules is None:
        from webapp.services.custom_rules import list_custom_rules

        existing_rules = list_custom_rules().get("rules") or []

    after_labels = after_labels or {}
    norm_suggested = normalize_rule_text(suggested_rule)
    target_cat = normalize_label(after_labels.get("ai_category") or "")
    target_sub = normalize_label(after_labels.get("ai_sub_category") or "")
    target_type = normalize_label(after_labels.get("expense_type") or "")
    target_amount = None
    if amount is not None:
        target_amount = f"{abs(float(amount)):.2f}".rstrip("0").rstrip(".")

    for entry in existing_rules:
        rule_text = str(entry.get("rule") or "").strip()
        if not rule_text:
            continue
        norm_existing = normalize_rule_text(rule_text)

        existing_amount = extract_amount_from_rule(rule_text)
        suggested_amount = extract_amount_from_rule(suggested_rule) if suggested_rule else None
        if target_amount and not suggested_amount:
            suggested_amount = target_amount

        if norm_suggested:
            if norm_suggested == norm_existing:
                return _entry_result(entry)
            if existing_amount and suggested_amount:
                ex_norm = existing_amount.rstrip("0").rstrip(".")
                sug_norm = suggested_amount.rstrip("0").rstrip(".")
                if ex_norm != sug_norm:
                    continue
            if _text_similarity(norm_suggested, norm_existing) >= similarity_threshold:
                return _entry_result(entry)

        if not merchant_key or not merchant_referenced_in_rule(rule_text, merchant_key):
            continue

        existing_cat = normalize_label(extract_field_from_rule(rule_text, "ai_category"))
        if target_cat and existing_cat and existing_cat == target_cat:
            existing_sub = normalize_label(extract_field_from_rule(rule_text, "ai_sub_category"))
            if target_sub and existing_sub and target_sub != existing_sub:
                continue
            existing_type = normalize_label(extract_field_from_rule(rule_text, "expense_type"))
            if target_type and existing_type and target_type != existing_type:
                continue
            if existing_amount and suggested_amount:
                rule_amt_norm = existing_amount.rstrip("0").rstrip(".")
                sug_amt_norm = suggested_amount.rstrip("0").rstrip(".")
                if rule_amt_norm != sug_amt_norm:
                    continue
            return _entry_result(entry)

    return None


def suppress_duplicate_rule_suggestion(
    result: dict[str, Any],
    *,
    merchant_key: str,
    after_labels: dict[str, str],
    amount: float | None = None,
    existing_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Clear recommend_save_rule when a similar custom rule already exists."""
    if not result.get("recommend_save_rule") and not (result.get("suggested_rule") or "").strip():
        return result

    similar = find_similar_custom_rule(
        suggested_rule=str(result.get("suggested_rule") or ""),
        merchant_key=merchant_key,
        after_labels=after_labels,
        amount=amount,
        existing_rules=existing_rules,
    )
    if not similar:
        return result

    updated = dict(result)
    updated["recommend_save_rule"] = False
    updated["existing_similar_rule"] = similar["rule"]
    updated["existing_rule_status"] = similar.get("status", "")
    updated["duplicate_rule_skipped"] = True

    preview = similar["rule"]
    if len(preview) > 180:
        preview = preview[:177] + "…"
    status = similar.get("status") or "unknown"
    note = f"Similar custom rule already exists ({status}): «{preview}»"
    updated["data_notes"] = list(updated.get("data_notes") or []) + [note]

    future = (updated.get("future_note") or "").strip()
    suffix = "A similar custom rule is already in your workbook — no duplicate needed."
    updated["future_note"] = f"{future} {suffix}".strip() if future else suffix
    return updated
