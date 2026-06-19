import pandas as pd

from webapp.processing import _custom_rule_match_mask


def _df(**rows):
    return pd.DataFrame(rows)


def test_description_match_is_case_insensitive():
    df = _df(
        **{
            "Generated Description": ["Amazon Web Services", "OTHER"],
            "Original Description": ["", "Amazon web services aws.amazon.co WA"],
            "Simple Description": ["", ""],
            "User Description": ["", ""],
        }
    )
    mask = _custom_rule_match_mask(
        df,
        {"description": ["*AMAZON WEB SERVICES*", "*Aws*"]},
    )
    assert mask.tolist() == [True, True]


def test_generated_description_or_patterns():
    df = _df(
        **{
            "Generated Description": ["Check Payment", "Netflix", "CHECK PAYMENT"],
            "Original Description": ["", "", ""],
        }
    )
    mask = _custom_rule_match_mask(df, {"generated_description": ["*check*", "netflix"]})
    assert mask.tolist() == [True, True, True]


def test_assign_rule_matches_merchant_and_bank_text():
    df = _df(
        **{
            "Generated Description": ["Amazon Web Services", "Random Merchant"],
            "Original Description": [
                "Amazon web services SEATTLE WA",
                "Totally different",
            ],
            "Simple Description": ["", ""],
            "User Description": ["", ""],
            "Amount_Numeric": [-12.34, -9.99],
        }
    )
    rule = {
        "rule_type": "assign",
        "match": {"description": ["*amazon web services*", "*aws*"]},
        "set": {
            "ai_category": "Business Expenses",
            "ai_sub_category": "Cloud Computing/Hosting",
            "type": "Variable",
            "classification": "Business",
        },
    }
    mask = _custom_rule_match_mask(df, rule["match"])
    assert mask.tolist() == [True, False]
