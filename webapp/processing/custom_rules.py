from __future__ import annotations

import json
from typing import Any

import pandas as pd
from openai import OpenAI

from webapp.llm.classify import extract_json_payload
from webapp.llm.client import PIPELINE_LLM_TEMPERATURE
from webapp.llm.prompts import CUSTOM_RULE_COMPILER_PROMPT
from webapp.processing.constants import (
    CUSTOM_RULE_FIELD_MAP,
    CUSTOM_RULE_STATUS_ACTIVE,
    CUSTOM_RULE_STATUS_DISABLED,
    CUSTOM_RULE_STATUS_ERROR,
    CUSTOM_RULE_STATUS_PENDING,
    CUSTOM_RULES_COLUMNS,
)
from webapp.processing.parse import parse_amount


def empty_custom_rules_sheet() -> pd.DataFrame:
    # object columns: Excel empty cells otherwise become float64 and reject JSON strings
    return pd.DataFrame({col: pd.Series(dtype=object) for col in CUSTOM_RULES_COLUMNS})

def _custom_rules_cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text

def normalize_custom_rules_sheet(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_custom_rules_sheet()
    out = frame.copy()
    if "Rule" not in out.columns:
        # Legacy or mistaken header row
        return empty_custom_rules_sheet()
    for col in CUSTOM_RULES_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out = out[list(CUSTOM_RULES_COLUMNS)]
    for col in CUSTOM_RULES_COLUMNS:
        out[col] = out[col].map(_custom_rules_cell_str)
    return out

def compile_custom_rule_text(
    client: OpenAI,
    rule_text: str,
    model: str,
    *,
    use_json_mode: bool,
) -> tuple[dict[str, Any] | None, str]:
    """Ask LLM to convert freeform Rule column text into executable JSON."""
    messages = [
        {"role": "system", "content": CUSTOM_RULE_COMPILER_PROMPT},
        {
            "role": "user",
            "content": (
                "Convert this rule to JSON.\n\n"
                f"Rule:\n{rule_text.strip()}"
            ),
        },
    ]
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": PIPELINE_LLM_TEMPERATURE,
        "messages": messages,
    }
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    from webapp.llm.request_log import logged_chat_completions_create

    response = logged_chat_completions_create(
        client, caller="pipeline.custom_rule_compile", **kwargs
    )
    raw = response.choices[0].message.content or "{}"
    try:
        parsed = extract_json_payload(raw)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON from model: {exc}"

    rule = parsed.get("rule", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(rule, dict):
        return None, f"Expected object with 'rule' key; got: {str(parsed)[:300]}"
    if "rule_type" not in rule:
        return None, "Compiled rule missing rule_type"
    return rule, ""

def compile_custom_rules_sheet(
    sheet: pd.DataFrame,
    client: OpenAI,
    model: str,
    *,
    use_json_mode: bool,
) -> pd.DataFrame:
    """Compile Pending rows via LLM; update Compiled Rule, Status, Last Error."""
    out = normalize_custom_rules_sheet(sheet)
    if out.empty:
        return out

    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    for idx, row in out.iterrows():
        status = str(row.get("Status", "") or "").strip().lower()
        rule_text = str(row.get("Rule", "") or "").strip()
        if not rule_text:
            continue
        if status == CUSTOM_RULE_STATUS_DISABLED.lower():
            continue
        compiled_existing = str(row.get("Compiled Rule", "") or "").strip()
        if status == CUSTOM_RULE_STATUS_ACTIVE.lower() and compiled_existing:
            continue
        if status not in (
            "",
            CUSTOM_RULE_STATUS_PENDING.lower(),
            "compile",
            CUSTOM_RULE_STATUS_ERROR.lower(),
        ):
            continue

        print(f"  Compiling custom rule row {idx + 1}...", flush=True)
        try:
            compiled, err = compile_custom_rule_text(
                client, rule_text, model, use_json_mode=use_json_mode
            )
        except Exception as exc:
            compiled, err = None, str(exc)

        if compiled is not None:
            out.at[idx, "Compiled Rule"] = json.dumps(compiled, separators=(",", ":"))
            out.at[idx, "Status"] = CUSTOM_RULE_STATUS_ACTIVE
            out.at[idx, "Last Error"] = ""
        else:
            out.at[idx, "Status"] = CUSTOM_RULE_STATUS_ERROR
            out.at[idx, "Last Error"] = err[:500]
        out.at[idx, "Updated At"] = now

    return out

def load_active_custom_rules(sheet: pd.DataFrame | None) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    out = normalize_custom_rules_sheet(sheet)
    for _, row in out.iterrows():
        if str(row.get("Status", "") or "").strip().lower() != CUSTOM_RULE_STATUS_ACTIVE.lower():
            continue
        raw = str(row.get("Compiled Rule", "") or "").strip()
        if not raw:
            continue
        try:
            rule = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(rule, dict) and rule.get("rule_type"):
            rules.append(rule)
    return rules

def _apply_custom_field_spec(df: pd.DataFrame, idx: int, spec: dict[str, Any]) -> None:
    for key, val in spec.items():
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        col = CUSTOM_RULE_FIELD_MAP.get(str(key).strip().lower(), str(key))
        if col not in df.columns:
            continue
        text = str(val).strip()
        if text and text.lower() != "nan":
            df.at[idx, col] = text

def _normalize_match_patterns(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    patterns: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text.lower() != "nan":
            patterns.append(text)
    return patterns

def _match_text_pattern(series: pd.Series, pattern: str) -> pd.Series:
    """Case-insensitive match of one pattern against a lowercase text series."""
    target = str(pattern or "").strip().lower()
    if not target:
        return pd.Series(False, index=series.index)
    if target.startswith("*") and target.endswith("*") and len(target) > 2:
        needle = target[1:-1]
        return series.str.contains(needle, regex=False) if needle else pd.Series(False, index=series.index)
    if target.endswith("*") and len(target) > 1:
        return series.str.startswith(target[:-1])
    if target.startswith("*") and len(target) > 1:
        return series.str.endswith(target[1:])
    return series == target

def _match_patterns_on_series(series: pd.Series, patterns: Any) -> pd.Series:
    normalized = _normalize_match_patterns(patterns)
    if not normalized:
        return pd.Series(False, index=series.index)
    mask = pd.Series(False, index=series.index)
    lowered = series.fillna("").astype(str).str.strip().str.lower()
    for pattern in normalized:
        mask |= _match_text_pattern(lowered, pattern)
    return mask

def _match_generated_description(df: pd.DataFrame, desc: Any) -> pd.Series:
    if "Generated Description" not in df.columns:
        return pd.Series(False, index=df.index)
    series = df["Generated Description"]
    return _match_patterns_on_series(series, desc)

def _match_any_description(df: pd.DataFrame, desc: Any) -> pd.Series:
    columns = (
        "Generated Description",
        "Original Description",
        "Simple Description",
        "User Description",
    )
    present = [col for col in columns if col in df.columns]
    if not present:
        return pd.Series(False, index=df.index)
    mask = pd.Series(False, index=df.index)
    for col in present:
        mask |= _match_patterns_on_series(df[col], desc)
    return mask

def _match_custom_rule_amount(df: pd.DataFrame, amount_spec: Any) -> pd.Series:
    """Match rows where |Amount| equals the target (expenses are often negative)."""
    if amount_spec is None or (isinstance(amount_spec, float) and pd.isna(amount_spec)):
        return pd.Series(True, index=df.index)
    text = str(amount_spec).strip().replace("$", "").replace(",", "")
    if not text or text.lower() == "nan":
        return pd.Series(True, index=df.index)
    try:
        target = abs(parse_amount(text))
    except (TypeError, ValueError):
        return pd.Series(False, index=df.index)
    if "Amount_Numeric" not in df.columns:
        amounts = df.get("Amount", pd.Series("", index=df.index)).apply(parse_amount)
    else:
        amounts = df["Amount_Numeric"]
    return amounts.apply(lambda a: abs(float(a)) == target)

def _custom_rule_match_mask(df: pd.DataFrame, match: dict[str, Any]) -> pd.Series:
    """AND of all match keys in a compiled custom rule."""
    match = match or {}
    mask = pd.Series(True, index=df.index)
    if "description" in match:
        mask &= _match_any_description(df, match.get("description"))
    if "generated_description" in match:
        mask &= _match_generated_description(df, match.get("generated_description"))
    if "amount" in match:
        mask &= _match_custom_rule_amount(df, match.get("amount"))
    return mask

def _apply_custom_rule_assign(df: pd.DataFrame, rule: dict[str, Any]) -> int:
    match = rule.get("match", {}) or {}
    mask = _custom_rule_match_mask(df, match)
    spec = rule.get("set", {}) or {}
    if not mask.any() or not spec:
        return 0
    for idx in df[mask].index:
        _apply_custom_field_spec(df, int(idx), spec)
    return int(mask.sum())

def _apply_custom_rule_monthly_split_max(df: pd.DataFrame, rule: dict[str, Any]) -> int:
    match = rule.get("match", {}) or {}
    mask = _custom_rule_match_mask(df, match)
    if not mask.any():
        return 0

    group_col = str(rule.get("group_by", "Budget Month") or "Budget Month")
    if group_col not in df.columns:
        group_col = "Calendar Month"
    min_rows = int(rule.get("min_rows_per_group", 2))
    when_max = rule.get("when_max", {}) or {}
    when_other = rule.get("when_other", {}) or {}

    updated = 0
    subset = df[mask]
    for _, grp in subset.groupby(group_col, dropna=False):
        if len(grp) < min_rows:
            continue
        amounts = grp["Amount_Numeric"].abs() if "Amount_Numeric" in grp.columns else grp.index.to_series()
        idx_max = amounts.idxmax()
        for idx in grp.index:
            spec = when_max if idx == idx_max else when_other
            if spec:
                _apply_custom_field_spec(df, int(idx), spec)
                updated += 1
    return updated

def apply_custom_rules(df: pd.DataFrame, compiled_rules: list[dict[str, Any]]) -> int:
    """
    Apply Active compiled custom rules.

    Called last in the pipeline so CustomRules override CategoryRules, Categories,
    BusinessCategoryRules, LLM review, and expense cadence lookup on shared fields.
    """
    total = 0
    for rule in compiled_rules:
        rule_type = str(rule.get("rule_type", "") or "").strip().lower()
        if rule_type == "assign":
            total += _apply_custom_rule_assign(df, rule)
        elif rule_type in ("monthly_split_max", "monthly_highest_split"):
            total += _apply_custom_rule_monthly_split_max(df, rule)
    return total
