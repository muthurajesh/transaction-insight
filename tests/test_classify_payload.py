"""Classification payload shape."""

from __future__ import annotations

import pandas as pd

from webapp.llm.classify import build_classification_payload


def test_build_classification_payload_minimal_fields():
    row = pd.Series(
        {
            "Date": "2021-07-02",
            "Amount": "-200.0",
            "Amount_Numeric": -200.0,
            "Category": "Securities Trades",
            "User Description": "should not appear",
            "Simple Description": "Robinhood",
            "Original Description": "ROBINHOOD DES:DEBITS",
            "Generated Description": "Robinhood",
            "Account Name": "Bank of America - Checking",
        }
    )
    payload = build_classification_payload(row, 3)
    assert payload == {
        "index": 3,
        "date": "2021-07-02",
        "amount": -200.0,
        "original_category": "Securities Trades",
        "generated_description": "Robinhood",
        "account": "Bank of America - Checking",
    }
    assert "user_description" not in payload
    assert "simple_description" not in payload
    assert "original_description" not in payload
    assert "amount_numeric" not in payload
