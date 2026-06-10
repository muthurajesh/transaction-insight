# Pipeline: SQLite merchant_labels on re-import

**Status:** Not implemented  
**Roadmap:** §3 Edit transactions

## Problem (from project chat)

User edits labels in **Edit Transactions** (SQLite). On **next month Run processing**:

- Pipeline reads **`transaction-lookups.xlsx`** (BusinessCategoryRules, Categories, LLM)
- Pipeline does **not** read SQLite `merchant_labels`
- Web edits (except “same merchant all amounts” scope) may be **overwritten** on re-process

Q&A documented in chat: Apply updates SQLite only; Excel unchanged unless user saves CustomRule.

## Goal

When Run processing categorizes a row, **confirmed SQLite merchant labels** should apply **before or instead of** redundant LLM calls — same spirit as Excel BusinessCategoryRules.

## Current data flow

```text
CSV → run_pipeline (core.py) → dataframe_store.save_processed_dataframe → SQLite transactions
                                      ↓
                            merchant_labels upsert (from pipeline output only)
```

`merchant_labels` table columns: `merchant_key`, `ai_category`, `ai_sub_category`, `expense_type`, `label_status`, `confidence`, `rationale`, …

Populated by:

- Run processing (`dataframe_store.py`)
- Edit Transactions — scope “same merchant (all amounts)” (`transaction_edit.py`)
- Import lookups (`lookups_import.py`)

## Proposed behavior

### Precedence (highest wins)

1. **CustomRules** (unchanged — final pass)
2. **SQLite `merchant_labels`** where `label_status = 'confirmed'` and user/web source
3. **Excel** BusinessCategoryRules / Categories
4. **LLM** review for remaining `needs_review` rows

### Hook point

**Option A (recommended):** In `webapp/adapters/dataframe_store.py` **before** or **after** `save_processed_dataframe`, apply labels from DB to dataframe — does not require changing CLI `core.py`.

**Option B:** Inject SQLite labels inside `run_pipeline` via adapter callback — more invasive.

**Option C:** Export SQLite → temporary Excel sheet before pipeline — avoid.

### Implementation sketch (Option A)

New `webapp/services/merchant_label_apply.py`:

```python
def load_confirmed_merchant_labels(conn) -> dict[str, dict]:
    ...

def apply_merchant_labels_to_dataframe(df, labels_by_merchant) -> int:
    """Set AI Category, Sub-Category, Type, label_status on matching Generated Description / merchant_key."""
```

Call from `process_csv_file()`:

```python
labels = load_confirmed_merchant_labels(conn)
apply_merchant_labels_to_dataframe(result.dataframe, labels)
save_processed_dataframe(...)
```

Match key: `merchant_key` == `Generated Description` (same as web app).

### What to set on match

| Field | Source |
|-------|--------|
| `AI Category` | `merchant_labels.ai_category` |
| `AI Sub-Category` | `merchant_labels.ai_sub_category` |
| `Type` / expense_type | `merchant_labels.expense_type` |
| `label_status` | `confirmed` |
| `rationale` | `sqlite:merchant_labels` |

Skip LLM review queue for these rows if already confirmed.

### Edge cases

| Case | Rule |
|------|------|
| Excel BusinessCategoryRules conflicts | Document: SQLite user edits win for same merchant (or make configurable) |
| `needs_review` in DB | Do not auto-confirm; still send to review |
| New merchant in CSV | Normal pipeline |
| Re-process same file | Idempotent label apply |

## Optional: Excel sync (separate item)

See [PIPELINE_EXCEL_SYNC.md](./PIPELINE_EXCEL_SYNC.md) — exporting web edits **to** `transaction-lookups.xlsx` is a different feature (user-triggered export).

## Files to change

| File | Change |
|------|--------|
| `webapp/services/merchant_label_apply.py` | New |
| `webapp/services/process.py` | Call before save |
| `README.md` or ROADMAP | Note precedence |
| Tests | Merchant with DB label survives re-process |

## Verification

1. Edit merchant “Netflix” → Entertainment in web (merchant scope)
2. Re-run processing on new CSV containing Netflix
3. Row arrives as Entertainment without LLM re-guess
4. `merchant_labels` row still `confirmed`

## Context source

Explicit user Q&A in project chat about Edit Apply vs Excel vs next month import.
