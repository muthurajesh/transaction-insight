from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import pandas as pd
from openai import OpenAI

from webapp.llm.client import json_for_prompt
from webapp.llm.classify import extract_json_payload
from webapp.llm.prompts import DESCRIPTION_PROMPT
from webapp.processing.constants import DESCRIPTION_LOOKUP_COLUMNS
from webapp.processing.parse import (
    _clean_original_for_display,
    _norm_description_part,
    heuristic_generated_description,
)


def description_source_key(row: pd.Series) -> str:
    """Stable hash key from normalized User, Simple, and cleaned Original descriptions."""
    user = _norm_description_part(row.get("User Description", ""))
    simple = _norm_description_part(row.get("Simple Description", ""))
    original = _norm_description_part(
        _clean_original_for_display(str(row.get("Original Description", "") or ""))
    )
    payload = f"u:{user}|s:{simple}|o:{original[:300]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

def build_description_lookup_map(lookups: dict[str, pd.DataFrame]) -> dict[str, str]:
    """Source Key -> Generated Description from the DescriptionLookup sheet."""
    sheet = lookups.get("DescriptionLookup")
    if sheet is None or sheet.empty:
        return {}
    if "Source Key" not in sheet.columns or "Generated Description" not in sheet.columns:
        return {}
    result: dict[str, str] = {}
    for _, row in sheet.iterrows():
        key = str(row.get("Source Key", "") or "").strip()
        desc = str(row.get("Generated Description", "") or "").strip()
        if key and desc:
            result[key] = desc[:120]
    return result

def make_description_lookup_row(
    row: pd.Series,
    source_key: str,
    generated: str,
    source: str,
    model: str,
) -> dict[str, str]:
    return {
        "Source Key": source_key,
        "User Description": str(row.get("User Description", "") or "")[:200],
        "Simple Description": str(row.get("Simple Description", "") or "")[:200],
        "Original Description": str(row.get("Original Description", "") or "")[:300],
        "Generated Description": generated[:120],
        "Source": source,
        "Model": model if source == "llm" else "",
        "Updated At": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def merge_description_lookup(
    existing: pd.DataFrame | None,
    new_rows: pd.DataFrame | None,
    *,
    rebuild: bool = False,
) -> pd.DataFrame:
    """Merge description lookup rows. Default: append new keys only. Rebuild: upsert by Source Key."""
    cols = list(DESCRIPTION_LOOKUP_COLUMNS)
    if new_rows is None or new_rows.empty:
        if existing is not None and not existing.empty:
            out = existing.copy()
            for col in cols:
                if col not in out.columns:
                    out[col] = ""
            return out[cols].sort_values("Source Key").reset_index(drop=True)
        return pd.DataFrame(columns=cols)

    new_rows = new_rows.copy()
    for col in cols:
        if col not in new_rows.columns:
            new_rows[col] = ""

    if existing is None or existing.empty:
        return (
            new_rows[cols]
            .drop_duplicates(subset=["Source Key"], keep="last")
            .sort_values("Source Key")
            .reset_index(drop=True)
        )

    existing = existing.copy()
    for col in cols:
        if col not in existing.columns:
            existing[col] = ""

    if rebuild:
        merged = pd.concat([existing[cols], new_rows[cols]], ignore_index=True)
        return (
            merged.drop_duplicates(subset=["Source Key"], keep="last")
            .sort_values("Source Key")
            .reset_index(drop=True)
        )

    existing_keys = set(existing["Source Key"].fillna("").astype(str).str.strip())
    append = new_rows[
        ~new_rows["Source Key"].fillna("").astype(str).str.strip().isin(existing_keys)
    ]
    merged = pd.concat([existing[cols], append[cols]], ignore_index=True)
    return merged.sort_values("Source Key").reset_index(drop=True)

def build_description_payload(row: pd.Series, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "original_description": str(row.get("Original Description", "") or "")[:300],
        "user_description": str(row.get("User Description", "") or "")[:200],
        "simple_description": str(row.get("Simple Description", "") or "")[:200],
        "category": str(row.get("Category", "") or ""),
        "amount": str(row.get("Amount", "") or ""),
    }

def generate_descriptions_batch(
    client: OpenAI,
    transactions: list[dict[str, Any]],
    model: str,
    *,
    use_json_mode: bool,
) -> list[dict[str, Any]]:
    user_content = json_for_prompt(transactions)
    messages = [
        {"role": "system", "content": DESCRIPTION_PROMPT},
        {
            "role": "user",
            "content": (
                "Generate descriptions for these transactions. Respond with JSON "
                '{"results": [...]} only.\n\n'
                f"Transactions:\n{user_content}"
            ),
        },
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0.1,
        "messages": messages,
    }
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    raw = response.choices[0].message.content or "{}"
    parsed = extract_json_payload(raw)
    results = parsed.get("results", parsed if isinstance(parsed, list) else [])
    if not isinstance(results, list):
        raise ValueError(f"Unexpected description response: {raw[:500]}")
    return results

def fill_generated_descriptions(
    df: pd.DataFrame,
    client: OpenAI,
    model: str,
    *,
    batch_size: int,
    use_json_mode: bool,
    description_lookup: dict[str, str] | None = None,
    rebuild_lookup: bool = False,
    on_batch_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Set Generated Description using shared lookup, User Description, then LLM.
    When rebuild_lookup is True, cache is ignored and LLM runs for all non-user rows.
    Returns (dataframe, new lookup rows to merge into transaction-lookups.xlsx).
    """
    df = df.copy()
    lookup = {} if rebuild_lookup else dict(description_lookup or {})
    new_entries: list[dict[str, str]] = []
    llm_indices: list[int] = []
    lookup_hits = 0
    user_hits = 0

    for idx, row in df.iterrows():
        key = description_source_key(row)
        cached = lookup.get(key, "")
        if cached and not rebuild_lookup:
            df.at[idx, "Generated Description"] = cached
            lookup_hits += 1
            continue

        user = str(row.get("User Description", "") or "").strip()
        if user:
            desc = user[:120]
            df.at[idx, "Generated Description"] = desc
            new_entries.append(make_description_lookup_row(row, key, desc, "user", model))
            lookup[key] = desc
            user_hits += 1
            continue

        llm_indices.append(int(idx))

    if rebuild_lookup:
        print(
            f"  Rebuilding description lookup: {user_hits} from User Description, "
            f"{len(llm_indices)} row(s) need LLM",
            flush=True,
        )
    else:
        print(
            f"  Description lookup: {lookup_hits} hit(s), {user_hits} from User Description, "
            f"{len(llm_indices)} row(s) need LLM",
            flush=True,
        )

    for idx in llm_indices:
        df.at[idx, "Generated Description"] = heuristic_generated_description(df.loc[idx])

    if llm_indices:
        key_to_indices: dict[str, list[int]] = {}
        for idx in llm_indices:
            key = description_source_key(df.loc[idx])
            key_to_indices.setdefault(key, []).append(idx)

        llm_tasks = [(key, indices[0]) for key, indices in key_to_indices.items()]
        if len(llm_tasks) < len(llm_indices):
            print(
                f"  Deduped description LLM: {len(llm_indices)} rows -> "
                f"{len(llm_tasks)} unique pattern(s)",
                flush=True,
            )

        payloads = [
            build_description_payload(df.loc[rep_idx], batch_idx)
            for batch_idx, (_, rep_idx) in enumerate(llm_tasks)
        ]
        total_payloads = len(payloads)
        for start in range(0, total_payloads, batch_size):
            batch = payloads[start : start + batch_size]
            end = start + len(batch)
            batch_msg = f"Descriptions (LLM) {start + 1}–{end} of {total_payloads}…"
            print(f"  {batch_msg}", flush=True)
            if on_batch_progress:
                on_batch_progress(
                    start,
                    total_payloads,
                    f"{batch_msg} (calling model)",
                )
            try:
                batch_results = generate_descriptions_batch(
                    client, batch, model, use_json_mode=use_json_mode
                )
            except Exception as exc:
                print(f"  Description LLM failed; keeping heuristics: {exc}", flush=True)
                break
            if on_batch_progress:
                on_batch_progress(end, total_payloads, batch_msg)
            for item in batch_results:
                batch_idx = int(item.get("index", -1))
                desc = str(item.get("generated_description", "") or "").strip()
                if batch_idx < 0 or batch_idx >= len(llm_tasks) or not desc:
                    continue
                key, rep_idx = llm_tasks[batch_idx]
                for idx in key_to_indices[key]:
                    df.at[idx, "Generated Description"] = desc[:120]
                new_entries.append(
                    make_description_lookup_row(
                        df.loc[rep_idx], key, desc, "llm", model
                    )
                )
                lookup[key] = desc[:120]

    new_lookup_df = (
        pd.DataFrame(new_entries, columns=list(DESCRIPTION_LOOKUP_COLUMNS))
        if new_entries
        else pd.DataFrame(columns=list(DESCRIPTION_LOOKUP_COLUMNS))
    )
    return df, new_lookup_df
