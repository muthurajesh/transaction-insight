# Edit insights (AI after label changes)

After you **Apply** label changes in **Edit Transactions**, the app calls the LLM with edit context and database stats, then shows an **AI insight** modal.

## What the AI sees

- Merchant key, scope (`single` / `merchant_amount` / `merchant`), rows updated
- Before → after labels (category, sub-category, expense type, classification)
- Whether a **merchant label** was saved (`merchant` scope)
- Stats: transaction count for merchant, same-amount count, months, top amounts, remaining old-category rows

## What you get

| Field | Purpose |
|-------|---------|
| **Insight** | Plain-language explanation of the pattern and your workflow |
| **Pattern summary** | One-line headline |
| **Suggested rule** | Plain-English text for the **CustomRules** sheet (editable before save) |
| **Future note** | How this interacts with the next CSV import / pipeline |
| **Save as custom rule** | Appends to `transaction-lookups.xlsx` → CustomRules (Pending) |
| **Compile & apply** | Compiles pending rules via LLM and updates matching SQLite rows |

If the LLM is unavailable, a **fallback** heuristic still suggests a rule when scope or volume warrants it.

## What edits do *not* do automatically

- **SQLite edits** do not modify `transaction-lookups.xlsx` unless you save a custom rule.
- **Merchant labels** (merchant scope) are stored in SQLite and help future categorization for that payee.
- **New month CSV processing** still runs the pipeline (Excel lookups + LLM); align rules/labels if you want imports to match your edits.

## API

`POST /api/transactions/edit-insight`

```json
{
  "merchant_key": "Netflix",
  "scope": "merchant",
  "rows_updated": 12,
  "before": { "ai_category": "Shopping", "ai_sub_category": "", "expense_type": "Variable", "classification": "Personal" },
  "after": { "ai_category": "Entertainment", "ai_sub_category": "Streaming", "expense_type": "Fixed", "classification": "Personal" },
  "amount": null,
  "update_merchant_label": true
}
```

Implementation: `webapp/services/edit_insights.py`.
