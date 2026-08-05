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
    looks_like_bank_noise,
    merchant_key,
    scrub_bank_text,
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


def test_scrub_bank_text_examples():
    assert scrub_bank_text(" *nature's nectar") == "nature's nectar"
    assert scrub_bank_text(" SQ *NATURE'S NECTAR SANTA ANA CA") == "NATURE'S NECTAR SANTA ANA"
    assert "XX4188" not in scrub_bank_text("18/8 RANCH* 188 RANCH XX4188 CA")
    assert scrub_bank_text("18/8 RANCH* 188 RANCH XX4188 CA") == "18/8 RANCH 188 RANCH"
    scrubbed_leaves = scrub_bank_text(
        " CHECKCARD XX19 7 LEAVES TUSTIN EST 19 TUSTIN CA XX2139"
    )
    assert "CHECKCARD" not in scrubbed_leaves.upper()
    assert "XX2139" not in scrubbed_leaves
    assert "7 LEAVES" in scrubbed_leaves.upper()
    scrubbed_acct = scrub_bank_text(
        "ACCT INTEGRATORS DES:Assn Dues ID:XX8678 INDN:Rajesh Muthu CO ID:1454"
    )
    assert scrubbed_acct == "ACCT INTEGRATORS"
    assert not looks_like_bank_noise(scrubbed_acct)
    assert looks_like_bank_noise(
        "ACCT Integrators DES:Assn Dues ID:XX8678 INDN:Rajesh Muthu CO ID:1454"
    )


def test_scrub_bank_text_checkcard_and_mobile_refs():
    assert (
        scrub_bank_text("CHECKCARD 0122 SELMA'S CHICAGO PIZZERI RANCHO")
        == "SELMA'S CHICAGO PIZZERI RANCHO"
    )
    assert scrub_bank_text("CHECKCARD 0126 TUTTO FRESCO KITCHEN XX3360 CA") == (
        "TUTTO FRESCO KITCHEN"
    )
    assert scrub_bank_text("MOBILE 0126 OAKLANDNEWSST2663 OAKLAND CA XX5649") == (
        "OAKLANDNEWSST2663 OAKLAND"
    )
    assert scrub_bank_text("MOBILE XX 365 VEND LLC 3 TROY MI XX5711") == (
        "365 VEND LLC 3 TROY"
    )
    # Brand names containing "Mobile" must not be mangled
    assert scrub_bank_text("T-Mobile Transfer") == "T-Mobile Transfer"
    assert scrub_bank_text("Wave Mobile Money Inc") == "Wave Mobile Money Inc"
    assert scrub_bank_text("Mobile Banking Payment") == "Mobile Banking Payment"
    assert scrub_bank_text("Mobile Purchase Ninas Indian & British LAKE FOREST CA") == (
        "Ninas Indian & British LAKE FOREST"
    )


def test_merchant_key_skips_noisy_generated_description():
    row = pd.Series(
        {
            "Merchant Key": "",
            "Generated Description": (
                "ACCT Integrators DES:Assn Dues ID:XX8678 INDN:Rajesh Muthu CO ID:1454"
            ),
            "Simple Description": "ACCT INTEGRATORS DES:Assn Dues ID:XX8678 INDN:Rajesh ",
            "Original Description": (
                "ACCT INTEGRATORS DES:Assn Dues ID:xx8678 INDN:Rajesh Muthu CO ID:1454"
            ),
        }
    )
    assert merchant_key(row) == "ACCT INTEGRATORS"


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
    assert payload["index"] == 0
    assert "xxxxxxx3001" not in payload["original_description"].lower()
    assert "FUEL STOP" in payload["original_description"]
    assert not payload["original_description"].endswith(" CA")
    assert payload["user_description"] == " "
    assert payload["simple_description"] == "Fuel Stop"
    assert "category" not in payload
    assert "amount" not in payload


def test_generated_description_plausible_rejects_wrong_merchant():
    row = _card_payment_row()
    assert not generated_description_plausible("Cafe Downtown", row)
    assert generated_description_plausible("Store Card Bank", row)
    assert generated_description_plausible("Merchant Corp", row)


def test_generated_description_plausible_rejects_bank_noise():
    row = _card_payment_row()
    assert not generated_description_plausible(
        "MERCHANT CORP DES:CARD PAYMNT ID:XX0202", row
    )


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


def test_fill_generated_descriptions_rejects_noisy_llm_keeps_scrubbed():
    row = pd.Series(
        {
            "User Description": "",
            "Simple Description": "ACCT INTEGRATORS DES:Assn Dues ID:XX8678",
            "Original Description": (
                "ACCT INTEGRATORS DES:Assn Dues ID:xx8678 INDN:Rajesh Muthu CO ID:1454"
            ),
            "Category": "Business Expenses",
        }
    )
    df = pd.DataFrame([row])
    client = MagicMock()
    response = MagicMock()
    response.choices = [
        MagicMock(
            message=MagicMock(
                content=(
                    '{"results": [{"index": 0, "generated_description": '
                    '"ACCT Integrators DES:Assn Dues ID:XX8678"}]}'
                )
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
        description_lookup={},
    )

    assert out.at[0, "Generated Description"] == "ACCT INTEGRATORS"
    assert new_rows.empty


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
