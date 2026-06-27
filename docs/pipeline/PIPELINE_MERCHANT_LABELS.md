# Pipeline: SQLite merchant_labels on re-import

**Status:** Implemented — confirmed user/web-authored merchant labels are authoritative before LLM review.  
**Roadmap:** §3 Edit transactions

## Problem

User edits labels in **Edit Transactions** or **Confirm Categories**. On later **Run processing**, pipeline-generated merchant labels must not override those explicit decisions.

## Current behavior

Run processing loads SQLite lookup tables through `load_lookup_workbook_from_db()` and applies the resulting merchant rows through `apply_merchant_category_lookup()` before LLM classification.

That DB load now intentionally includes only `merchant_labels` that are:

- `label_status = 'confirmed'`, and
- explicitly user/web-authored, currently recognized by markers such as `rationale = 'user edited'`, `rationale = 'user confirmed'`, or confirmed-via-web notes.

Pipeline-generated merchant labels (`rationale = 'pipeline'`) remain persisted for review/metadata, but they are **not** loaded as authoritative re-import labels.

## Run-processing precedence

Highest priority wins:

1. **Custom Rules** — unchanged final pass.
2. **Confirmed user/web `merchant_labels`** — loaded from SQLite and applied by merchant before LLM review.
3. **Other DB lookup rules** — category rules and other lookup-derived behavior.
4. **LLM classification** — only for spend rows that still need review after lookup application.

When a confirmed user/web merchant label supplies category, semantic sub-category, type, and non-`Review` budget tier, the row no longer matches the LLM review mask and is not sent back for classification.

## Save protection

Both transaction persistence and lookup persistence skip merchant-label updates when the existing `merchant_labels` row is a confirmed user/web-authored label. This prevents pipeline-generated values from overwriting an explicit user decision during re-import or lookup-save.

`clear_existing` transaction imports clear transactions/ingested files, but do not delete durable merchant labels.

## Fields applied from `merchant_labels`

| DataFrame field | Source |
|---|---|
| `AI Category` | `merchant_labels.ai_category` |
| `AI Sub-Category` | `merchant_labels.ai_sub_category` |
| `Type` | `merchant_labels.expense_type` |
| `Budget Tier` | `merchant_labels.budget_tier` when non-empty and not `Review` |
| `Flow Type` | `merchant_labels.flow_type` when present |
| `Classification` | `merchant_labels.classification` when present |

## Edge cases

| Case | Rule |
|---|---|
| Pipeline-generated `merchant_labels` | Persisted, but not authoritative on re-import and cannot overwrite confirmed user/web labels |
| `needs_review` labels in DB | Not loaded as authoritative labels; normal review flow continues |
| New merchant in CSV | Normal pipeline + LLM review as needed |
| Custom Rule matches same row | Custom Rule still wins because it runs as the final pass |

## Verification

1. Confirm or edit merchant “Netflix” to Entertainment/Streaming in web.
2. Re-run processing on a CSV containing Netflix.
3. The row is labeled from `merchant_labels` before LLM review.
4. If fully classified, it is not sent to LLM classification.
5. The confirmed `merchant_labels` row remains confirmed and user-authored after save.
