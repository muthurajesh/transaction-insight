from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCRIPTS_DIR = PROJECT_ROOT / "scripts"

CONFIG_DIR = PROJECT_ROOT / "config"

INPUT_DIR = PROJECT_ROOT / "input"

load_dotenv(CONFIG_DIR / ".env")

PAYROLL_SPILLOVER_DAYS = int(os.getenv("PAYROLL_SPILLOVER_DAYS", "7"))

CADENCE_SPIKE_RATIO = float(os.getenv("CADENCE_SPIKE_RATIO", "2.0"))

CADENCE_MIN_HISTORY_MONTHS = int(os.getenv("CADENCE_MIN_HISTORY_MONTHS", "3"))

CADENCE_YEARLY_GAP_MIN = int(os.getenv("CADENCE_YEARLY_GAP_MIN", "10"))

CADENCE_YEARLY_GAP_MAX = int(os.getenv("CADENCE_YEARLY_GAP_MAX", "14"))

DROP_OUTPUT_COLUMNS = ("Status", "Split Type", "Currency", "Memo")

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
