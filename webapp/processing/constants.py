from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCRIPTS_DIR = PROJECT_ROOT / "scripts"

CONFIG_DIR = PROJECT_ROOT / "config"

INPUT_DIR = PROJECT_ROOT / "input"

OUTPUT_DIR = PROJECT_ROOT / "output"

load_dotenv(CONFIG_DIR / ".env")

LOOKUP_FILENAME = os.getenv("LOOKUP_FILE", "transaction-lookups.xlsx")

PAYROLL_SPILLOVER_DAYS = int(os.getenv("PAYROLL_SPILLOVER_DAYS", "7"))

BASELINE_VARIABLE_BUFFER_PCT = float(os.getenv("BASELINE_VARIABLE_BUFFER_PCT", "10"))

BASELINE_FIXED_BUFFER_PCT = float(os.getenv("BASELINE_FIXED_BUFFER_PCT", "5"))

BASELINE_RECOMMENDED_MONTHS = int(os.getenv("BASELINE_RECOMMENDED_MONTHS", "3"))

CADENCE_SPIKE_RATIO = float(os.getenv("CADENCE_SPIKE_RATIO", "2.0"))

CADENCE_MIN_HISTORY_MONTHS = int(os.getenv("CADENCE_MIN_HISTORY_MONTHS", "3"))

CADENCE_YEARLY_GAP_MIN = int(os.getenv("CADENCE_YEARLY_GAP_MIN", "10"))

CADENCE_YEARLY_GAP_MAX = int(os.getenv("CADENCE_YEARLY_GAP_MAX", "14"))

DROP_OUTPUT_COLUMNS = ("Status", "Split Type", "Currency", "Memo")

HIDDEN_DESCRIPTION_COLUMNS = ("Original Description", "User Description", "Simple Description")

TRANSACTION_SHEETS = ("Raw Data", "Income", "Expenses", "Adjustments")

DETAIL_TRANSACTION_SHEETS = ("Income", "Expenses", "Adjustments")

EXCEL_CURRENCY_FORMAT = "$#,##0.00"

EXCEL_PERCENT_FORMAT = "0.0%"

EXCEL_SHORT_DATE_FORMAT = "m/d/yyyy"

EXCEL_AUTOFIT_COLUMNS = True  # applied to every sheet on all workbooks this script writes

EXCEL_AUTOFIT_MIN_WIDTH = 8

EXCEL_AUTOFIT_MAX_WIDTH = 55

EXCEL_AUTOFIT_PADDING = 1.5

SUMMARY_INSIGHTS_SHEET = "Summary"

SUMMARY_OVERVIEW_CURRENCY_COLUMNS = (
    "Gross Income",
    "Total Expenses",
    "Avg Monthly Expenses",
    "vs Avg",
)

SUMMARY_OVERVIEW_PERCENT_COLUMNS = ("vs Avg %",)

SUMMARY_DETAIL_CURRENCY_COLUMNS = (
    "Month Spend",
    "Avg Monthly Spend",
    "vs Avg",
)

SUMMARY_DETAIL_PERCENT_COLUMNS = ("vs Avg %", "% of Month")

BASELINE_CATEGORY_CURRENCY_COLUMNS = (
    "Total Spend",
    "Typical Monthly Spend (Core)",
    "Avg Monthly Spend",
    "Min Month Spend",
    "Max Month Spend",
    "Suggested Monthly Budget",
)

BASELINE_MONTHLY_CURRENCY_COLUMNS = (
    "Total Spend",
    "Core Monthly Spend",
    "vs Monthly Avg",
)

BASELINE_OVERVIEW_CURRENCY_METRICS = frozenset(
    {
        "Overall avg monthly spend",
        "Overall typical core monthly spend",
        "Sum of suggested category budgets",
    }
)

LOOKUP_SHEETS = (
    "Categories",
    "CategoryRules",
    "Types",
    "BusinessCategoryRules",
    "DescriptionLookup",
    "MerchantCategories",
)

MERCHANT_CATEGORIES_SHEET = "MerchantCategories"

MERCHANT_CATEGORY_COLUMNS = (
    "Merchant Key",
    "AI Category",
    "AI Sub-Category",
    "Budget Tier",
    "Type",
    "Flow Type",
    "Classification",
    "Transaction Count",
    "Notes",
)

EXPENSE_CADENCE_RULES_SHEET = "ExpenseCadenceRules"

EXPENSE_CADENCE_RULE_COLUMNS = (
    "Generated Description",
    "Cadence",
    "In Monthly Run-Rate?",
    "Notes",
)

CADENCE_MONTHLY = "Monthly"

CADENCE_YEARLY = "Yearly"

CADENCE_ONETIME = "One-time"

CADENCE_UNPLANNED = "Unplanned"

CADENCE_UNKNOWN = "Unknown"

CADENCE_VALID = frozenset(
    {CADENCE_MONTHLY, CADENCE_YEARLY, CADENCE_ONETIME, CADENCE_UNPLANNED, CADENCE_UNKNOWN}
)

CADENCE_SOURCE_LOOKUP = "Lookup"

CADENCE_SOURCE_DETECTED = "Detected"

CADENCE_SOURCE_DEFAULT = "Default"

CADENCE_DEFAULT_RUNRATE: dict[str, str] = {
    CADENCE_MONTHLY: "Y",
    CADENCE_YEARLY: "N",
    CADENCE_ONETIME: "N",
    CADENCE_UNPLANNED: "N",
    CADENCE_UNKNOWN: "Y",
}

CADENCE_REVIEW_SHEET = "Cadence Review"

CUSTOM_RULES_SHEET = "CustomRules"

CUSTOM_RULES_COLUMNS = ("Rule", "Status", "Compiled Rule", "Last Error", "Updated At")

CUSTOM_RULE_STATUS_PENDING = "Pending"

CUSTOM_RULE_STATUS_ACTIVE = "Active"

CUSTOM_RULE_STATUS_ERROR = "Error"

CUSTOM_RULE_STATUS_DISABLED = "Disabled"

CUSTOM_RULE_FIELD_MAP = {
    "category": "Category",
    "classification": "Classification",
    "ai_category": "AI Category",
    "ai_sub_category": "AI Sub-Category",
    "type": "Type",
    "sub_type": "Sub-Type",
    "budget_tier": "Budget Tier",
    "flow_type": "Flow Type",
}

DESCRIPTION_LOOKUP_COLUMNS = (
    "Source Key",
    "User Description",
    "Simple Description",
    "Original Description",
    "Generated Description",
    "Source",
    "Model",
    "Updated At",
)
