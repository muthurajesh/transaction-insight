from __future__ import annotations

import sqlite3

from webapp.services.expense_cadence import (
    CADENCE_KIND_LABELS,
    CADENCE_PERIOD_PRESETS,
    RUN_RATE_FILTER_OPTIONS,
    expense_cadence_period_label,
)

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

    period_rows = conn.execute(
        """
        SELECT DISTINCT period_count, period_unit FROM (
            SELECT period_count, period_unit
            FROM transactions
            WHERE period_count IS NOT NULL AND period_unit IS NOT NULL AND TRIM(period_unit) != ''
            UNION
            SELECT period_count, period_unit
            FROM cadence_rules
            WHERE period_count IS NOT NULL AND period_unit IS NOT NULL AND TRIM(period_unit) != ''
        )
        ORDER BY 2, 1
        """
    ).fetchall()

    cadence_periods: list[dict[str, str]] = [
        {"value": value, "label": label} for value, label in CADENCE_PERIOD_PRESETS
    ]
    seen_periods = {item["value"] for item in cadence_periods if item["value"] != "unset"}
    for count, unit in period_rows:
        if count is None or not unit:
            continue
        key = f"{int(count)}:{str(unit).strip().lower()}"
        if key in seen_periods:
            continue
        seen_periods.add(key)
        cadence_periods.append(
            {
                "value": key,
                "label": expense_cadence_period_label("recurring", int(count), str(unit).strip().lower()),
            }
        )

    cadence_kinds = [
        {"value": kind, "label": label} for kind, label in sorted(CADENCE_KIND_LABELS.items())
    ]

    run_rate_filters = [
        {"value": value, "label": label} for value, label in RUN_RATE_FILTER_OPTIONS
    ]

    return {
        "categories": categories,
        "sub_categories": sub_categories,
        "classifications": classifications,
        "cadence_kinds": cadence_kinds,
        "cadence_periods": cadence_periods,
        "run_rate_filters": run_rate_filters,
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
