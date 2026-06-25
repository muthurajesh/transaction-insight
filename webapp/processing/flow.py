from __future__ import annotations

import pandas as pd


def _abs_spend_sum(spend: pd.DataFrame, row_mask: pd.Series) -> float:
    if spend.empty or not row_mask.any():
        return 0.0
    return float(spend.loc[row_mask, "Amount_Numeric"].abs().sum())


def budget_tier_from_category(category: str) -> str:
    """Neutral default until the LLM or user assigns Need/Want/Wish."""
    _ = category
    return "Review"


def cost_type_from_category(category: str) -> str:
    """Neutral default until the LLM or user assigns Fixed/Variable."""
    _ = category
    return "Variable"


def is_paycheck_row(row: pd.Series) -> bool:
    cat = str(row.get("Category", "") or "")
    amt = float(row.get("Amount_Numeric", 0.0))
    if cat == "Paychecks/Salary" and amt > 0:
        return True
    combined = " ".join(
        [str(row.get("Original Description", "") or ""), str(row.get("Simple Description", "") or "")]
    ).lower()
    return "payroll" in combined and amt > 0


def _description_suggests_refund(row: pd.Series) -> bool:
    """Heuristic: bank text looks like a return/refund/reversal."""
    text = " ".join(
        [
            str(row.get("Original Description", "") or ""),
            str(row.get("Simple Description", "") or ""),
            str(row.get("User Description", "") or ""),
            str(row.get("Generated Description", "") or ""),
        ]
    ).lower()
    hints = (
        "refund",
        "return",
        "reversal",
        "reversed",
        "chargeback",
        "credit adj",
        "purchase return",
        "merchandise return",
    )
    return any(h in text for h in hints)


def flow_type_from_row(row: pd.Series) -> str:
    """Classify how the amount should be summarized from bank export fields."""
    cat = str(row.get("Category", "") or "").strip()
    amt = float(row.get("Amount_Numeric", 0.0))

    internal = {"Transfers", "Credit Card Payments", "Savings", "Securities Trades"}
    true_income = {"Paychecks/Salary", "Interest"}
    adjustment = {"Refunds/Adjustments", "Rewards", "Other Income", "Expense Reimbursement", "Deposits"}

    if cat in internal:
        return "Transfer"

    if amt > 0:
        if cat in true_income:
            return "Income"
        if cat in adjustment:
            return "Adjustment"
        if _description_suggests_refund(row):
            return "Adjustment"
        # Positive amount on a spending category is a credit against prior spend.
        return "Adjustment"

    # amt < 0
    return "Expense"


def _spend_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Expense rows included in budget spend analysis."""
    mask = (
        (df["Flow Type"] == "Expense")
        & (df.get("Include in Spend?", "N") == "Y")
        & (df["Amount_Numeric"] < 0)
    )
    spend = df.loc[mask].copy()
    if not spend.empty:
        spend["Spend Amount"] = spend["Amount_Numeric"].abs()
    return spend
