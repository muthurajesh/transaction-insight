from __future__ import annotations

import json
from typing import Any, Callable

import pandas as pd
from openai import OpenAI

from webapp.llm.client import BATCH_SIZE, json_for_prompt
from webapp.llm.prompts import BUSINESS_RULE_PROMPT, CLASSIFICATION_PROMPT
from webapp.processing.parse import (
    _row_merchant_key,
    ensure_merchant_key_column,
    parse_amount,
)


def suggest_business_rules_batch(
    client: OpenAI,
    transactions: list[dict[str, Any]],
    model: str,
    *,
    use_json_mode: bool,
) -> list[dict[str, Any]]:
    user_content = json_for_prompt(transactions)
    messages = [
        {"role": "system", "content": BUSINESS_RULE_PROMPT},
        {
            "role": "user",
            "content": (
                "Suggest categorization rules for these business transactions. "
                'JSON {"results": [...]} only.\n\n'
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
        raise ValueError(f"Unexpected business rule response: {raw[:500]}")
    return results

def classify_review_mask(df: pd.DataFrame) -> pd.Series:
    """Spend rows that still need LLM category + semantic sub-category."""
    df = ensure_merchant_key_column(df)
    spend = (
        (df["Flow Type"] == "Expense")
        & (df["Amount_Numeric"] < 0)
        & (df["Include in Spend?"] == "Y")
    )
    sub = df["AI Sub-Category"].fillna("").astype(str).str.strip()
    mk = df["Merchant Key"].fillna("").astype(str).str.strip()
    missing_sub = sub == ""
    legacy_sub = sub.str.lower() == mk.str.lower()
    review_tier = df["Budget Tier"].fillna("").astype(str).str.strip() == "Review"
    return spend & (missing_sub | legacy_sub | review_tier)

def build_transaction_summary(row: pd.Series, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "date": row.get("Date", ""),
        "amount": row.get("Amount", ""),
        "amount_numeric": row.get("Amount_Numeric", parse_amount(row.get("Amount", 0))),
        "original_category": row.get("Category", ""),
        "description": (
            row.get("Generated Description")
            or row.get("Simple Description")
            or row.get("Original Description")
            or ""
        )[:200],
        "account": row.get("Account Name", ""),
    }

def extract_json_payload(text: str) -> dict[str, Any] | list[Any]:
    """Parse JSON from model output, including ```json fenced blocks."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)

def classify_batch(
    client: OpenAI,
    transactions: list[dict[str, Any]],
    model: str,
    *,
    use_json_mode: bool,
) -> list[dict[str, Any]]:
    user_content = json_for_prompt(transactions)
    messages = [
        {"role": "system", "content": CLASSIFICATION_PROMPT},
        {
            "role": "user",
            "content": (
                "Classify these transactions. Respond with a single JSON object "
                '{"results": [ ... ]} where each item has index, section, '
                "category, sub_category, type, sub_type. No markdown, no commentary.\n\n"
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
    try:
        parsed = extract_json_payload(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse model JSON: {raw[:500]}") from exc

    results = parsed.get("results", parsed if isinstance(parsed, list) else [])
    if not isinstance(results, list):
        raise ValueError(f"Unexpected AI response shape: {raw[:500]}")
    return results

def classify_all(
    df: pd.DataFrame,
    client: OpenAI,
    model: str,
    batch_size: int = BATCH_SIZE,
    *,
    use_json_mode: bool,
) -> pd.DataFrame:
    summaries = [build_transaction_summary(row, i) for i, row in df.iterrows()]
    all_results: dict[int, dict[str, str]] = {}

    for start in range(0, len(summaries), batch_size):
        batch = summaries[start : start + batch_size]
        print(
            f"  Classifying transactions {start + 1}–{start + len(batch)} of {len(summaries)}...",
            flush=True,
        )
        batch_results = classify_batch(client, batch, model, use_json_mode=use_json_mode)
        for item in batch_results:
            idx = int(item.get("index", -1))
            if idx < 0:
                continue
            all_results[idx] = {
                "Section": item.get("section", "Expense"),
                "AI Category": item.get("category", "Other"),
                "AI Sub-Category": item.get("sub_category", ""),
                "Type": item.get("type", "Variable"),
                "Sub-Type": item.get("sub_type", "") or "",
            }

    # Fill any missing rows with sensible defaults from amount sign
    rows = []
    for i in range(len(df)):
        if i in all_results:
            rows.append(all_results[i])
        else:
            amt = summaries[i]["amount_numeric"]
            rows.append(
                {
                    "Section": "Income" if amt > 0 else "Expense",
                    "AI Category": summaries[i]["original_category"] or "Other",
                    "AI Sub-Category": "",
                    "Type": "Variable",
                    "Sub-Type": "",
                }
            )

    enrichment = pd.DataFrame(rows)
    return pd.concat([df.reset_index(drop=True), enrichment], axis=1)

def classify_review_rows(
    df: pd.DataFrame,
    client: OpenAI,
    model: str,
    *,
    batch_size: int,
    use_json_mode: bool,
    on_batch_progress: Callable[[int, int, str], None] | None = None,
) -> pd.DataFrame:
    """Refine AI category and semantic sub-category for spend rows that need it."""
    df = ensure_merchant_key_column(df)
    mask = classify_review_mask(df)
    if not mask.any():
        return df

    review_indices = df[mask].index.tolist()
    print(f"  LLM review rows: {len(review_indices)}", flush=True)

    all_results: dict[int, dict[str, str]] = {}
    summaries = [
        build_transaction_summary(df.loc[i], int(i))  # original index becomes the LLM 'index'
        for i in review_indices
    ]

    total_summaries = len(summaries)
    for start in range(0, total_summaries, batch_size):
        batch = summaries[start : start + batch_size]
        end = start + len(batch)
        batch_msg = f"Classifying review {start + 1}–{end} of {total_summaries}…"
        print(f"  {batch_msg}", flush=True)
        if on_batch_progress:
            on_batch_progress(
                start,
                total_summaries,
                f"{batch_msg} (calling model)",
            )
        try:
            batch_results = classify_batch(
                client, batch, model, use_json_mode=use_json_mode
            )
        except Exception as exc:
            print(f"  LLM failed; using defaults for remaining batches: {exc}", flush=True)
            break
        if on_batch_progress:
            on_batch_progress(end, total_summaries, batch_msg)
        for item in batch_results:
            idx = int(item.get("index", -1))
            if idx < 0:
                continue
            sub = str(item.get("sub_category", "") or "").strip()
            mk = _row_merchant_key(df.loc[idx])
            if sub.lower() == mk.lower():
                sub = ""
            all_results[idx] = {
                "AI Category": item.get("category", df.loc[idx].get("Category", "Other")),
                "AI Sub-Category": sub,
                "Type": item.get("type", df.loc[idx].get("Type", "Variable")),
                "Sub-Type": item.get("sub_type", "") or "",
            }

    # Apply results to df
    df = df.copy()
    for i in review_indices:
        if i in all_results:
            for col, val in all_results[i].items():
                df.at[i, col] = val
        else:
            # Keep defaults
            pass

    return df
