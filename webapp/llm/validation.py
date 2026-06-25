"""Text-based guards: merchant labels must be supported by bank fields (not category taxonomy)."""

from __future__ import annotations

import re

import pandas as pd

from webapp.processing.parse import _clean_original_for_display

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)


def description_haystack(row: pd.Series) -> str:
    parts = [
        str(row.get("User Description", "") or ""),
        str(row.get("Simple Description", "") or ""),
        _clean_original_for_display(str(row.get("Original Description", "") or "")),
    ]
    return " ".join(parts).lower()


def generated_description_plausible(generated: str, row: pd.Series) -> bool:
    """
    Reject cached/LLM merchant labels that do not appear in bank text.
    Category assignment is left to the LLM and your saved rules — not hardcoded here.
    """
    gen = str(generated or "").strip()
    if not gen or gen.lower() == "unknown":
        return False
    hay = description_haystack(row)
    if not hay:
        return True
    gen_lower = gen.lower()
    if gen_lower in hay or hay in gen_lower:
        return True
    gen_tokens = [t.lower() for t in _TOKEN_RE.findall(gen_lower)]
    if not gen_tokens:
        return gen_lower in hay
    return any(token in hay for token in gen_tokens)
