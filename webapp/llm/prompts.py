from __future__ import annotations


CUSTOM_RULE_COMPILER_PROMPT = """You translate plain-English transaction rules into JSON for an automated processor.

Return ONLY valid JSON: {"rule": { ... }}

Supported rule_type values:

1. "assign" — set fields when match criteria all apply (AND). All text matching is case-insensitive.
   Use *text* for contains, prefix* for starts-with, *suffix for ends-with; plain text is exact match.
   Optional amount: compare absolute dollar value (9.99 matches -9.99 and 9.99).
   Use match.description when the user says "description" (searches Generated, Original, Simple, and User descriptions).
   Use match.generated_description only for the merchant/payee label (Generated Description).
   For OR conditions, use a JSON array of patterns, e.g. ["*amazon web services*","*aws*"].
   {"rule_type":"assign","match":{"description":["*amazon web services*","*aws*"]},"set":{"ai_category":"Business Expenses","ai_sub_category":"Cloud Computing/Hosting","type":"Variable","classification":"Business"}}
   {"rule_type":"assign","match":{"generated_description":"*check*","amount":"60"},"set":{"ai_category":"Education","ai_sub_category":"Music Lessons"}}
   {"rule_type":"assign","match":{"generated_description":"Apple","amount":"9.99"},"set":{"category":"Business Expenses","classification":"Business","ai_category":"Business","ai_sub_category":"Laptop Warranty"}}

2. "monthly_split_max" — rows with the same Generated Description in the same calendar/budget month:
   the row with the largest absolute Amount gets when_max; every other row in that month gets when_other.
   Use for "multiple entries per month, highest is X, others are Y".
   {"rule_type":"monthly_split_max","match":{"generated_description":"Ahs Ahs.Com"},"group_by":"Budget Month","min_rows_per_group":2,"when_max":{"category":"Insurance","ai_category":"Utilities","ai_sub_category":"Appliance Insurance"},"when_other":{"category":"Business Expenses","classification":"Business","ai_category":"Rental","ai_sub_category":"Appliance Insurance"}}

Allowed field keys in set / when_max / when_other: category, classification, ai_category, ai_sub_category, type, sub_type, budget_tier.
Use exact Generated Description spelling from the user's rule when possible."""

DESCRIPTION_PROMPT = """You are a personal finance assistant. For each transaction, read the bank's
Original Description, optional User Description, and Simple Description. Produce one short
Generated Description: a clear merchant or payee label (3–8 words) suitable for budgeting reports.
Prefer Simple Description or User Description when they name the merchant; otherwise distill
Original Description (ignore card numbers, DES:/ID:/INDN:/CO ID: boilerplate).
Return ONLY valid JSON: {"results": [{"index": <int>, "generated_description": "<string>"}]}."""

BUSINESS_RULE_PROMPT = """You are a bookkeeper. Each transaction is marked Business (not Personal).
Suggest how future similar charges should be categorized for a personal+business export workflow.
Return ONLY valid JSON: {"results": [{"index": <int>, "ai_category": "<string>",
"ai_sub_category": "<string>", "budget_tier": "Need|Want|Wish", "type": "Fixed|Variable",
"notes": "<short rationale>"}]}."""

CLASSIFICATION_PROMPT = """You are a personal finance analyst. Classify each transaction below.

For each transaction, return:
1. section: "Income" or "Expense"
   - Income: salary, interest, dividends, refunds/credits that reduce prior spending, legitimate incoming money
   - Expense: money spent or outflows (including transfers out, bill payments, purchases)
   - Credit card "payment received" / "online payment thank you" on a credit card account is NOT income — treat as Expense (internal transfer)
   - Zelle/Venmo received from individuals: use context — small peer payments may be Expense-related reimbursements; payroll-like amounts are Income
2. category: clear top-level category (e.g. Housing, Utilities, Groceries, Dining, Transportation, Healthcare, Insurance, Entertainment, Income, Transfers, Savings, Subscriptions, Pets, Shopping, Personal Care, Education, Charitable, Fees, Other)
3. sub_category: short bill/spend type (e.g. "Towing", "Electric bill", "Fast food", "Fuel") — NOT the merchant or store name (that is already in description)
4. type: "Fixed" or "Variable"
   - Fixed: recurring obligations you expect each month even if the amount varies slightly (mortgage, rent, HOA, utilities, insurance premiums, phone/internet, subscriptions, gym membership, loan payments, childcare, minimum debt payments)
   - Variable: discretionary or fluctuating spending (groceries, restaurants, fuel, shopping, entertainment outings, gifts, one-off purchases)
   - Nuance: electricity/gas/water bills are Fixed (required monthly). Groceries and dining are Variable.
5. sub_type: always return empty string "" (reserved for future use)

Return ONLY valid JSON: an array of objects with keys: index, section, category, sub_category, type, sub_type.
The index must match the transaction index provided."""
