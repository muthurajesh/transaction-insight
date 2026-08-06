# Check labels — merchant confirm flow

**Status:** Implemented (web) — **Check labels** (Agent Workspace inbox)  
**Related:** [PIPELINE_MERCHANT_LABELS.md](../pipeline/PIPELINE_MERCHANT_LABELS.md) · [AGENT_WORKSPACE.md](../product/AGENT_WORKSPACE.md)

> **Removed:** The legacy **Confirm Categories** page (bulk **Suggest labels (AI)** queue UI) and its suggest APIs (`POST /api/review/suggest-batch`, `POST /api/review/{merchant}/suggest-labels`) were removed. Label approval now runs only through **Check labels**.

## Overview

**Check labels** shows payees (and related inbox items) the AI marked needing a look (`needs_review` / `pending`). Approving with **Looks good** saves durable labels and updates the database.

## Merchant confirm (Check labels)

1. Open a payee from the **Needs a look** inbox.
2. Adjust labels if needed, then **Looks good**.
3. Confirm APIs:
   - Preview: `POST /api/review/{merchant_key}/confirm-preview`
   - Confirm: `POST /api/review/{merchant_key}/confirm`
4. Confirm upserts **`merchant_labels`** and updates matching **`transactions`** (pending or all, per scope).

## Same-merchant merge

Heuristic spelling groups (`GET /api/review/merchant-aliases`) appear as **possible duplicates**. **Combine as one** applies `merchant_alias` taxonomy proposals so multiple payee names become one before you approve labels.

## Check Payment

`Check Payment` is in `SPLIT_REVIEW_MERCHANT_KEYS`. Each check is confirmed as a single transaction (no merchant-wide rule).

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
  "ai_category": "Subscriptions",
  "ai_sub_category": "Streaming",
  "expense_type": "Fixed",
  "flow_type": "Expense",
  "classification": "Personal",
  "scope": "pending"
}
```

Also: `GET /api/review/options`, `GET /api/review/merchant-aliases`, workspace `GET /api/pending-confirmations`.

## Files

| Path | Role |
|------|------|
| `webapp/services/review_confirm.py` | Confirm + preview |
| `webapp/services/pending_confirmations.py` | Check labels inbox |
| `webapp/static/app.js` | Workspace confirm UI |
