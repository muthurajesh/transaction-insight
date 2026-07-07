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


def test_description_match_includes_merchant_key_columns():
    df = _df(
        **{
            "Merchant Key": ["Payee One", ""],
            "merchant_key": ["", "Payee Two"],
            "Generated Description": ["Other", "Other"],
            "Original Description": ["", ""],
            "Simple Description": ["", ""],
            "User Description": ["", ""],
        }
    )
    mask = _custom_rule_match_mask(df, {"description": "*payee*"})
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
            "Flow Type": ["Expense", "Expense"],
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


def test_amount_or_list_matches_either_value():
    df = _df(
        **{
            "Generated Description": ["Capital One", "Capital One", "Capital One"],
            "Original Description": ["", "", ""],
            "Amount_Numeric": [-36.0, -69.31, -25.0],
        }
    )
    mask = _custom_rule_match_mask(
        df,
        {"generated_description": "*capital one*", "amount": ["36", "69.31"]},
    )
    assert mask.tolist() == [True, True, False]


def test_amount_sign_positive_and_negative():
    df = _df(
        **{
            "Generated Description": ["Shell", "Shell", "Shell"],
            "Original Description": ["", "", ""],
            "Amount_Numeric": [-48.97, 9.0, 0.0],
        }
    )
    neg = _custom_rule_match_mask(df, {"description": "Shell", "amount_sign": "negative"})
    pos = _custom_rule_match_mask(df, {"description": "Shell", "amount_sign": "positive"})
    zero = _custom_rule_match_mask(df, {"description": "Shell", "amount_sign": "zero"})
    assert neg.tolist() == [True, False, False]
    assert pos.tolist() == [False, True, False]
    assert zero.tolist() == [False, False, True]


def test_amount_comparison_operators():
    df = _df(
        **{
            "Generated Description": ["A", "A", "A", "A"],
            "Original Description": ["", "", "", ""],
            "Amount_Numeric": [-10.0, -50.0, 50.0, -100.0],
        }
    )
    gt = _custom_rule_match_mask(df, {"amount": ">50"})
    gte = _custom_rule_match_mask(df, {"amount": {"op": ">=", "value": "50"}})
    lt = _custom_rule_match_mask(df, {"amount": "<50"})
    ne = _custom_rule_match_mask(df, {"amount": "!=50"})
    # Comparisons use absolute value: |-100| and |50| and |-50|
    assert gt.tolist() == [False, False, False, True]
    assert gte.tolist() == [False, True, True, True]
    assert lt.tolist() == [True, False, False, False]
    assert ne.tolist() == [True, False, False, True]


def test_amount_sign_and_comparison_combine():
    df = _df(
        **{
            "Generated Description": ["Shell", "Shell", "Shell"],
            "Original Description": ["", "", ""],
            "Amount_Numeric": [-120.0, 120.0, -40.0],
        }
    )
    mask = _custom_rule_match_mask(
        df,
        {"description": "Shell", "amount_sign": "negative", "amount": ">=100"},
    )
    assert mask.tolist() == [True, False, False]


def test_assign_rule_sets_flow_type():
    from webapp.processing import apply_custom_rules

    df = _df(
        **{
            "Generated Description": ["Internal Transfer", "Store"],
            "Original Description": ["", ""],
            "Simple Description": ["", ""],
            "User Description": ["", ""],
            "Amount_Numeric": [-100.0, -5.0],
            "Flow Type": ["Expense", "Expense"],
            "Category": ["", ""],
            "Classification": ["Personal", "Personal"],
            "AI Category": ["", "Shopping"],
            "AI Sub-Category": ["", ""],
            "Type": ["Variable", "Variable"],
            "Budget Month": ["2026-01", "2026-01"],
        }
    )
    rule = {
        "rule_type": "assign",
        "match": {"generated_description": "Internal Transfer"},
        "set": {"flow_type": "Transfer", "ai_category": "Transfers"},
    }
    apply_custom_rules(df, [rule])
    assert df.at[0, "Flow Type"] == "Transfer"
    assert df.at[0, "AI Category"] == "Transfers"
    assert df.at[1, "Flow Type"] == "Expense"
