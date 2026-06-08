from __future__ import annotations

import sqlite3

DEFAULT_CATEGORIES = [
    "Housing",
    "Utilities",
    "Groceries",
    "Dining",
    "Restaurants",
    "Transportation",
    "Healthcare",
    "Insurance",
    "Entertainment",
    "Income",
    "Transfers",
    "Savings",
    "Subscriptions",
    "Pets",
    "Shopping",
    "Personal Care",
    "Education",
    "Charitable",
    "Fees",
    "Business Expenses",
    "Other",
]

FLOW_TYPES = ["Expense", "Income"]
EXPENSE_TYPES = ["Fixed", "Variable"]


def get_review_options(conn: sqlite3.Connection) -> dict:
    cat_rows = conn.execute(
        """
        SELECT DISTINCT ai_category FROM transactions
        WHERE ai_category IS NOT NULL AND TRIM(ai_category) != ''
        UNION
        SELECT DISTINCT ai_category FROM merchant_labels
        WHERE ai_category IS NOT NULL AND TRIM(ai_category) != ''
        ORDER BY 1
        """
    ).fetchall()
    sub_rows = conn.execute(
        """
        SELECT DISTINCT ai_sub_category FROM transactions
        WHERE ai_sub_category IS NOT NULL AND TRIM(ai_sub_category) != ''
        UNION
        SELECT DISTINCT ai_sub_category FROM merchant_labels
        WHERE ai_sub_category IS NOT NULL AND TRIM(ai_sub_category) != ''
        ORDER BY 1
        """
    ).fetchall()
    class_rows = conn.execute(
        """
        SELECT DISTINCT classification FROM transactions
        WHERE classification IS NOT NULL AND TRIM(classification) != ''
        ORDER BY 1
        """
    ).fetchall()

    categories = sorted({*DEFAULT_CATEGORIES, *(r[0] for r in cat_rows)})
    sub_categories = sorted({r[0] for r in sub_rows if r[0]})
    classifications = sorted({r[0] for r in class_rows if r[0]})

    return {
        "categories": categories,
        "sub_categories": sub_categories,
        "classifications": classifications,
        "flow_types": FLOW_TYPES,
        "expense_types": EXPENSE_TYPES,
        "tooltips": {
            "category": (
                "Top-level budget group (e.g. Groceries, Utilities). "
                "Pick from the list or type your own. Applies to every transaction for this merchant."
            ),
            "sub_category": (
                "More specific label under the category (e.g. Electric bill, Fast food). "
                "Pick from the list or type your own."
            ),
            "flow_type": (
                "Income = money coming in (payroll, interest). "
                "Expense = money out, including purchases and credit card payments."
            ),
            "expense_type": (
                "Fixed = recurring monthly obligation (rent, utilities, subscriptions). "
                "Variable = discretionary or fluctuating spend (groceries, dining, shopping)."
            ),
            "confidence": (
                "How confident the AI was (0–1). Lower values appear here for you to confirm."
            ),
        },
    }
