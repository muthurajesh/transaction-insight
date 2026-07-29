"""Hermetic parse and bank Category → flow_type tests."""

from __future__ import annotations

import pandas as pd

from webapp.parsing import transaction_id_from_row
from webapp.processing.flow import flow_type_from_row
from webapp.processing.parse import parse_amount, parse_transaction_dates


def test_parse_amount_handles_currency_and_sign():
    assert parse_amount("-12.34") == -12.34
    assert parse_amount("$1,234.50") == 1234.50
    assert parse_amount("40.00") == 40.0


def test_parse_transaction_dates():
    series = pd.Series(["01/05/2026", "2026-02-03", "bad"])
    parsed = parse_transaction_dates(series)
    assert str(parsed.iloc[0].date()) == "2026-01-05"
    assert str(parsed.iloc[1].date()) == "2026-02-03"
    assert pd.isna(parsed.iloc[2])


def test_transaction_id_stable_for_same_row():
    row = pd.Series(
        {
            "Date": "01/05/2026",
            "Amount": "-10.00",
            "Amount_Numeric": -10.0,
            "Account Name": "Checking",
            "Original Description": "CORNER MARKET #12",
        }
    )
    a = transaction_id_from_row(row, parsed_date=pd.Timestamp("2026-01-05"))
    b = transaction_id_from_row(row, parsed_date=pd.Timestamp("2026-01-05"))
    assert a == b
    assert len(a) == 24


def test_flow_type_paycheck_income():
    row = pd.Series(
        {
            "Category": "Paychecks/Salary",
            "Amount_Numeric": 3200.0,
            "Original Description": "ACH CREDIT EMPLOYER PAYROLL",
            "Simple Description": "Employer Direct Deposit",
        }
    )
    assert flow_type_from_row(row) == "Income"


def test_flow_type_transfer_and_expense():
    transfer = pd.Series(
        {
            "Category": "Transfers",
            "Amount_Numeric": -100.0,
            "Original Description": "ONLINE TRANSFER",
            "Simple Description": "Savings Transfer",
        }
    )
    expense = pd.Series(
        {
            "Category": "Shopping",
            "Amount_Numeric": -20.0,
            "Original Description": "CORNER MARKET",
            "Simple Description": "Corner Market",
        }
    )
    assert flow_type_from_row(transfer) == "Transfer"
    assert flow_type_from_row(expense) == "Expense"
