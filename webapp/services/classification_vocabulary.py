"""Shared category vocabulary for pipeline classify and classification audit."""

from __future__ import annotations

import json
import os
import sqlite3
from difflib import SequenceMatcher
from typing import Any

MAX_CATEGORIES = int(os.getenv("CLASSIFY_VOCABULARY_MAX_CATEGORIES", "50"))
MAX_SUBS = int(os.getenv("CLASSIFY_VOCABULARY_MAX_SUBS", "120"))
SIMILARITY_THRESHOLD = float(os.getenv("CLASSIFY_VOCABULARY_SIMILARITY", "0.75"))


def vocabulary_hint_enabled() -> bool:
    raw = os.getenv("CLASSIFY_VOCABULARY_HINT", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on", "")


def load_classification_vocabulary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Distinct ai_category / ai_sub_category from transactions and merchant_labels."""
    from webapp.services.review_options import get_review_options

    options = get_review_options(conn)
    categories = list(options.get("categories") or [])[:MAX_CATEGORIES]
    sub_categories = list(options.get("sub_categories") or [])[:MAX_SUBS]
    subs_by_cat: dict[str, list[str]] = {}
    for cat, subs in (options.get("sub_categories_by_category") or {}).items():
        if cat not in categories and len(categories) < MAX_CATEGORIES:
            categories.append(cat)
        subs_by_cat[cat] = list(subs)[:MAX_SUBS]
    categories = sorted(set(categories))[:MAX_CATEGORIES]
    return {
        "categories": categories,
        "sub_categories": sub_categories,
        "sub_categories_by_category": subs_by_cat,
    }


def format_vocabulary_prompt_block(vocabulary: dict[str, Any] | None) -> str:
    if not vocabulary:
        return ""
    categories = vocabulary.get("categories") or []
    if not categories:
        return ""
    subs_by_cat = vocabulary.get("sub_categories_by_category") or {}
    payload: dict[str, Any] = {"categories": categories}
    if subs_by_cat:
        payload["sub_categories_by_category"] = subs_by_cat
    return (
        "Known vocabulary — prefer these exact spellings for category and sub_category; "
        "only add a new label when nothing fits:\n"
        f"{json.dumps(payload, separators=(',', ':'))}"
    )


def _norm_key(value: str) -> str:
    return (value or "").strip().casefold()


def _best_match(label: str, candidates: list[str], *, threshold: float) -> str:
    text = (label or "").strip()
    if not text or not candidates:
        return text
    key = _norm_key(text)
    for candidate in candidates:
        cand_key = _norm_key(candidate)
        if cand_key == key:
            return candidate.strip()
        if cand_key == key + "s" or key == cand_key + "s":
            return candidate.strip()
        if cand_key.startswith(key) and len(cand_key) - len(key) <= 3:
            return candidate.strip()
        if key.startswith(cand_key) and len(key) - len(cand_key) <= 3:
            return candidate.strip()
        if key in cand_key or cand_key in key:
            if abs(len(cand_key) - len(key)) <= 4:
                return candidate.strip()
    best = text
    best_score = 0.0
    for candidate in candidates:
        score = SequenceMatcher(None, key, _norm_key(candidate)).ratio()
        if score > best_score:
            best_score = score
            best = candidate.strip()
    return best if best_score >= threshold else text


def normalize_classify_labels(
    category: str,
    sub_category: str,
    vocabulary: dict[str, Any] | None,
    *,
    threshold: float | None = None,
) -> tuple[str, str]:
    """Map near-duplicate labels to canonical spellings from the vocabulary."""
    cat = (category or "").strip()
    sub = (sub_category or "").strip()
    if not vocabulary:
        return cat, sub

    sim = SIMILARITY_THRESHOLD if threshold is None else threshold
    categories = vocabulary.get("categories") or []
    all_subs = vocabulary.get("sub_categories") or []
    subs_by_cat: dict[str, list[str]] = vocabulary.get("sub_categories_by_category") or {}

    if cat and categories:
        cat = _best_match(cat, categories, threshold=sim)

    sub_pool = list(subs_by_cat.get(cat, []))
    if sub:
        if sub_pool:
            sub = _best_match(sub, sub_pool, threshold=sim)
        elif all_subs:
            sub = _best_match(sub, all_subs, threshold=sim)

    return cat, sub


def labels_equivalent(
    prod_cat: str,
    prod_sub: str,
    sugg_cat: str,
    sugg_sub: str,
    vocabulary: dict[str, Any] | None,
) -> bool:
    """True when labels match after canonical normalization."""
    pc, ps = normalize_classify_labels(prod_cat, prod_sub, vocabulary)
    sc, ss = normalize_classify_labels(sugg_cat, sugg_sub, vocabulary)
    return _norm_key(pc) == _norm_key(sc) and _norm_key(ps) == _norm_key(ss)
