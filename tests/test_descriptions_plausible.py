"""Guardrails for description lookup cache and merchant category application."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from webapp.processing import (
    MERCHANT_CATEGORIES_SHEET,
    apply_merchant_category_lookup,
    build_description_lookup_map,
    build_description_payload,
    description_source_key,
    fill_generated_descriptions,
    generated_description_plausible,
)


def _card_payment_row() -> pd.Series:
    return pd.Series(
        {
            "User Description": "",
            "Simple Description": "Store Card Bank",
            "Original Description": (
                "MERCHANT CORP      DES:CARD PAYMNT ID:xxxxxxxxxxx0202 "
                "INDN:JANE DOE             CO ID:1001"
            ),
            "Category": "Online Services",
        }
    )


def test_build_description_payload_excludes_category_and_amount():
    row = pd.Series(
        {
            "Original Description": " FUEL STOP xxxxxxx3001 ANYTOWN CA",
            "User Description": " ",
            "Simple Description": " Fuel Stop",
            "Category": "Gasoline/Fuel",
            "Amount": "-55.08",
        }
    )
    payload = build_description_payload(row, 0)
    assert payload == {
        "index": 0,
        "original_description": " FUEL STOP xxxxxxx3001 ANYTOWN CA",
        "user_description": " ",
        "simple_description": " Fuel Stop",
    }
    assert "category" not in payload
    assert "amount" not in payload


def test_generated_description_plausible_rejects_wrong_merchant():
    row = _card_payment_row()
    assert not generated_description_plausible("Cafe Downtown", row)
    assert generated_description_plausible("Store Card Bank", row)
    assert generated_description_plausible("Merchant Corp", row)


def test_build_description_lookup_map_skips_implausible_cache():
    row = _card_payment_row()
    key = description_source_key(row)
    lookups = {
        "DescriptionLookup": pd.DataFrame(
            [
                {
                    "Source Key": key,
                    "User Description": "",
                    "Simple Description": "Store Card Bank",
                    "Original Description": row["Original Description"],
                    "Generated Description": "Cafe Downtown",
                    "Source": "llm",
                    "Model": "qwen",
                    "Updated At": "2026-06-09",
                }
            ]
        )
    }
    assert build_description_lookup_map(lookups) == {}


def test_fill_generated_descriptions_uses_llm_when_cache_implausible():
    row = _card_payment_row()
    key = description_source_key(row)
    df = pd.DataFrame([row])
    client = MagicMock()
    response = MagicMock()
    response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"results": [{"index": 0, "generated_description": "Merchant Corp"}]}'
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
        description_lookup={key: "Cafe Downtown"},
    )

    assert out.at[0, "Generated Description"] == "Merchant Corp"
    assert new_rows.iloc[0]["Source"] == "llm"
    client.chat.completions.create.assert_called_once()


def test_fill_generated_descriptions_uses_plausible_cache():
    row = _card_payment_row()
    key = description_source_key(row)
    df = pd.DataFrame([row])
    client = MagicMock()

    out, new_rows = fill_generated_descriptions(
        df,
        client,
        "test-model",
        batch_size=10,
        use_json_mode=False,
        description_lookup={key: "Merchant Corp"},
    )

    assert out.at[0, "Generated Description"] == "Merchant Corp"
    assert new_rows.empty
    client.chat.completions.create.assert_not_called()


def test_apply_merchant_category_lookup_skips_wrong_merchant_key():
    df = pd.DataFrame(
        [
            {
                "Merchant Key": "Cafe Downtown",
                "Generated Description": "Cafe Downtown",
                "Simple Description": "Store Card Bank",
                "Original Description": "MERCHANT CORP DES:CARD PAYMNT",
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
                    "Merchant Key": "Cafe Downtown",
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
