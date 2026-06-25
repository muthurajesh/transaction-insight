"""Guardrails for description lookup cache and merchant category application."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from webapp.processing import (
    MERCHANT_CATEGORIES_SHEET,
    apply_merchant_category_lookup,
    build_description_lookup_map,
    description_source_key,
    fill_generated_descriptions,
    generated_description_plausible,
)


def _amazon_synchrony_row() -> pd.Series:
    return pd.Series(
        {
            "User Description": "",
            "Simple Description": "Synchrony Bank",
            "Original Description": (
                "MERCHANT CORP      DES:CARD PAYMNT ID:xxxxxxxxxxx0202 "
                "INDN:JANE DOE             CO ID:9069"
            ),
            "Category": "Online Services",
        }
    )


def test_generated_description_plausible_rejects_mcdonalds_for_amazon():
    row = _amazon_synchrony_row()
    assert not generated_description_plausible("McDonald's", row)
    assert generated_description_plausible("Synchrony Bank", row)
    assert generated_description_plausible("Amazon", row)


def test_build_description_lookup_map_skips_implausible_cache():
    row = _amazon_synchrony_row()
    key = description_source_key(row)
    lookups = {
        "DescriptionLookup": pd.DataFrame(
            [
                {
                    "Source Key": key,
                    "User Description": "",
                    "Simple Description": "Synchrony Bank",
                    "Original Description": row["Original Description"],
                    "Generated Description": "McDonald's",
                    "Source": "llm",
                    "Model": "qwen",
                    "Updated At": "2026-06-09",
                }
            ]
        )
    }
    assert build_description_lookup_map(lookups) == {}


def test_fill_generated_descriptions_uses_llm_when_cache_implausible():
    row = _amazon_synchrony_row()
    key = description_source_key(row)
    df = pd.DataFrame([row])
    client = MagicMock()
    response = MagicMock()
    response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"results": [{"index": 0, "generated_description": "Amazon"}]}'
            )
        )
    ]
    client.chat.completions.create.return_value = response

    out, new_rows = fill_generated_descriptions(
        df,
        client,
        "test-model",
        batch_size=10,
        use_json_mode=False,
        description_lookup={key: "McDonald's"},
    )

    assert out.at[0, "Generated Description"] == "Amazon"
    assert new_rows.iloc[0]["Source"] == "llm"
    client.chat.completions.create.assert_called_once()


def test_fill_generated_descriptions_uses_plausible_cache():
    row = _amazon_synchrony_row()
    key = description_source_key(row)
    df = pd.DataFrame([row])
    client = MagicMock()

    out, new_rows = fill_generated_descriptions(
        df,
        client,
        "test-model",
        batch_size=10,
        use_json_mode=False,
        description_lookup={key: "Amazon"},
    )

    assert out.at[0, "Generated Description"] == "Amazon"
    assert new_rows.empty
    client.chat.completions.create.assert_not_called()


def test_apply_merchant_category_lookup_skips_wrong_merchant_key():
    df = pd.DataFrame(
        [
            {
                "Merchant Key": "McDonald's",
                "Generated Description": "McDonald's",
                "Simple Description": "Synchrony Bank",
                "Original Description": "AMAZON CORP DES:SYF PAYMNT",
                "AI Category": "Online Services",
                "AI Sub-Category": "",
                "Type": "Variable",
                "Budget Tier": "N/A",
                "Flow Type": "Expense",
                "Classification": "Personal",
                "Include in Spend?": "Y",
            }
        ]
    )
    lookups = {
        MERCHANT_CATEGORIES_SHEET: pd.DataFrame(
            [
                {
                    "Merchant Key": "McDonald's",
                    "AI Category": "Dining",
                    "AI Sub-Category": "Fast food",
                    "Budget Tier": "Discretionary",
                    "Type": "Variable",
                    "Flow Type": "Expense",
                    "Classification": "Personal",
                    "Transaction Count": 100,
                    "Notes": "",
                }
            ]
        )
    }
    updated = apply_merchant_category_lookup(df, lookups)
    assert updated == 0
    assert df.at[0, "AI Category"] == "Online Services"
