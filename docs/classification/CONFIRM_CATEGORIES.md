# Confirm Categories — merchant confirm flow

**Status:** Implemented (web)  
**Related:** [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md)

## Overview

The **Confirm Categories** tab shows merchants (or single checks) the AI marked `needs_review` / `pending`. Confirming a **merchant group** saves a durable rule and updates the database.

## Merchant group confirm (default)

1. User edits labels and clicks **Confirm pending in this group**.
2. **Preview API** (`POST /api/review/{merchant_key}/confirm-preview`) returns:
   - Pending vs total transaction counts
   - How many already-confirmed rows differ from the proposed labels
   - Breakdown of differing category/sub-category pairs
   - Lookup conflicts (`merchant_labels`, `BusinessCategoryRules`, active `CustomRules`)
   - Whether to **suggest a custom rule** (≥2 distinct category patterns for the merchant)
3. Modal lets the user choose:
   - **Update pending only** — `scope=pending` (default)
   - **Update all transactions** — `scope=all`
   - **Replace conflicting saved rule** when lookup conflicts exist
4. **Confirm API** (`POST /api/review/{merchant_key}/confirm`):
   - Upserts **`merchant_labels`** in SQLite
   - Updates matching **`transactions`** rows per scope

## Bulk AI suggest (lookup-first)

A toolbar above the review list fills labels for the **first 10–100** groups without auto-confirming.

1. Choose batch size (**10**, **25**, **50**, or **100** groups).
2. Click **✨ Suggest labels (AI)**.
3. For each group (in queue order):
   - **Lookup-first:** saved merchant rules, business rules, active `CustomRules`, or confirmed `merchant_labels` in SQLite.
   - **LLM fallback** when no lookup matches (merchant context, similar confirmed merchants, recent confirms).
4. Form fields update on each card; a short rationale line appears under the merchant meta.
5. **Classification** is aligned with category: business category names → **Business** classification.
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

## Same-merchant merge (Check labels)

Heuristic spelling groups (`GET /api/review/merchant-aliases`) appear as **Same place, different names?** **Combine as one** applies `merchant_alias` taxonomy proposals so multiple payee names become one before you approve labels.

## Check Payment (unchanged)

`Check Payment` is in `SPLIT_REVIEW_MERCHANT_KEYS`. Each check is its own card; **Confirm this transaction** updates a single row only (no merchant-wide rule).

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
  "replace_conflicting_rule": false
}
```

For single-transaction confirm, include `"transaction_id": "..."` (Check Payment); `scope` / `replace_conflicting_rule` are ignored.

## Files

| File | Role |
|------|------|
| `webapp/services/review_confirm.py` | Preview, lookup sync, scoped DB apply |
| `webapp/services/review_suggest.py` | Lookup-first + LLM label suggestions (single + batch) |
| `webapp/services/categorize.py` | Review queue listing; single-tx confirm |
| `webapp/static/app.js` | Confirm modal UI; bulk suggest toolbar |
| `webapp/adapters/lookup_store.py` | Load/save merchant labels and rules |

## Pipeline integration

- Pipeline reads SQLite `merchant_labels` before LLM on re-import ([PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md))
