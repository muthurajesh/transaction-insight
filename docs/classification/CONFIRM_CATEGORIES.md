# Confirm Categories — merchant confirm flow

**Status:** Implemented (web)  
**Related:** [PIPELINE_EXCEL_SYNC.md](../pipeline/PIPELINE_EXCEL_SYNC.md), [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md)

## Overview

The **Confirm Categories** tab shows merchants (or single checks) the AI marked `needs_review` / `pending`. Confirming a **merchant group** saves a durable rule and updates the database.

## Merchant group confirm (default)

1. User edits labels and clicks **Confirm pending in this group**.
2. **Preview API** (`POST /api/review/{merchant_key}/confirm-preview`) returns:
   - Pending vs total transaction counts
   - How many already-confirmed rows differ from the proposed labels
   - Breakdown of differing category/sub-category pairs
   - Excel conflicts (`MerchantCategories`, `BusinessCategoryRules`, active `CustomRules`)
   - Whether to **suggest a custom rule** (≥2 distinct category patterns for the merchant)
3. Modal lets the user choose:
   - **Update pending only** — `scope=pending` (default)
   - **Update all transactions** — `scope=all`
   - **Replace conflicting rule in transaction-lookups.xlsx** when Excel conflicts exist
4. **Confirm API** (`POST /api/review/{merchant_key}/confirm`):
   - Upserts **`merchant_labels`** in SQLite
   - Merges **`MerchantCategories`** in `transaction-lookups.xlsx` (and **`BusinessCategoryRules`** when Classification = Business)
   - Updates matching **`transactions`** rows per scope

### Excel columns (MerchantCategories)

| Column | Source |
|--------|--------|
| Merchant Key | `merchant_key` |
| AI Category / AI Sub-Category | Form |
| Budget Tier | Derived from category |
| Type | Expense type (Fixed/Variable) |
| **Flow Type** | Transaction kind |
| **Classification** | Personal / Business |
| Transaction Count | Total rows for merchant in DB |
| Notes | `confirmed via web {timestamp}` |

Pipeline **`apply_merchant_category_lookup`** applies Flow Type and Classification from this sheet on Run processing.

## Bulk AI suggest (lookup-first)

A toolbar above the review list fills labels for the **first 10–100** groups without auto-confirming.

1. Choose batch size (**10**, **25**, **50**, or **100** groups).
2. Click **✨ Suggest labels (AI)**.
3. For each group (in queue order):
   - **Lookup-first:** `MerchantCategories`, `BusinessCategoryRules`, active `CustomRules` assign rules, or confirmed `merchant_labels` in SQLite.
   - **LLM fallback** when no lookup matches (merchant context, similar confirmed merchants, recent confirms).
4. Form fields update on each card; a short rationale line appears under the merchant meta.
5. **Classification** is aligned with category: `Business Expenses` (or `Business`) → **Business** classification.
6. You still **Confirm** each group manually.

```http
POST /api/review/suggest-batch
Content-Type: application/json

{ "limit": 10 }
```

```http
POST /api/review/{merchant_key}/suggest-labels
Content-Type: application/json

{ "transaction_id": null }
```

Include `transaction_id` for **Check Payment** single-transaction cards.

## Check Payment (unchanged)

`Check Payment` is in `SPLIT_REVIEW_MERCHANT_KEYS`. Each check is its own card; **Confirm this transaction** updates a single row only (no Excel merchant rule).

## APIs

```http
POST /api/review/{merchant_key}/confirm-preview
Content-Type: application/json

{
  "ai_category": "Subscriptions",
  "ai_sub_category": "Streaming",
  "expense_type": "Fixed",
  "flow_type": "Expense",
  "classification": "Personal"
}
```

```http
POST /api/review/{merchant_key}/confirm
Content-Type: application/json

{
  "ai_category": "...",
  "ai_sub_category": "...",
  "expense_type": "Fixed",
  "flow_type": "Expense",
  "classification": "Personal",
  "scope": "pending",
  "replace_excel": false
}
```

For single-transaction confirm, include `"transaction_id": "..."` (Check Payment); `scope` / `replace_excel` are ignored.

## Files

| File | Role |
|------|------|
| `webapp/services/review_confirm.py` | Preview, Excel sync, scoped DB apply |
| `webapp/services/review_suggest.py` | Lookup-first + LLM label suggestions (single + batch) |
| `webapp/services/categorize.py` | Review queue listing; single-tx confirm |
| `webapp/static/app.js` | Confirm modal UI; bulk suggest toolbar |
| `webapp/processing/constants.py` | `MERCHANT_CATEGORY_COLUMNS`, pipeline apply |
| `webapp/processing/lookups.py` | Lookup merge and workbook helpers |

## Still open

- Pipeline reads SQLite `merchant_labels` before LLM on re-import ([PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md))
- Bulk export of all web labels to Excel from Settings ([PIPELINE_EXCEL_SYNC.md](../pipeline/PIPELINE_EXCEL_SYNC.md))
