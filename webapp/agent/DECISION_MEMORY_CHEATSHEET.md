# Decision memory — SQL cheat sheet (Learning Agent)

Read-only `query_sql` for meta-analysis: how the user responds to AI proposals and what is already saved.

## Tables (Learning Agent may query)

| Table | Purpose |
|-------|---------|
| `decision_events` | Append-only HITL log — what AI proposed vs what the user chose |
| `ai_insights` | Prior Learning Agent proposals (`status`: open, accepted, rejected) |
| `transactions` | Row-level labels, merchants, amounts (evidence only) |
| `merchant_labels` | Confirmed per-merchant defaults |
| `pipeline_custom_rules` | Compiled custom rules (`rule_text`, `status`, compiled JSON) |
| `cadence_rules` | Merchant cadence for run-rate views |
| `category_rules` | Bank category → AI label mappings |
| `description_lookup` | Cached bank text → generated description |

## `decision_events` columns

| Column | Meaning |
|--------|---------|
| `source` | `confirm_categories`, `classification_audit`, `taxonomy_rules`, `edit_transactions`, `learning_agent` |
| `entity_type` | `merchant`, `transaction`, `finding`, `insight`, `taxonomy` |
| `entity_key` | Merchant key, finding id, etc. |
| `action` | `accepted`, `edited`, `rejected`, `dismissed`, `deferred` |
| `ai_proposal_json` | JSON string — AI suggestion |
| `user_outcome_json` | JSON string — user choice |
| `context_json` | Extra metadata |
| `created_at` | ISO timestamp |

Parse JSON in SQL with `json_extract(ai_proposal_json, '$.ai_category')` when aggregating.

## `ai_insights` columns

| Column | Meaning |
|--------|---------|
| `insight_type` | `pattern_insight`, `cadence_rule`, `custom_rule`, `category_rename`, `merchant_label` |
| `pattern_summary` | Short user-facing summary |
| `confidence` | 0.0–1.0 |
| `merchant_key` | When insight is merchant-specific |
| `proposal_json` | Structured next-step hint |
| `status` | `open`, `accepted`, `rejected` |

Do not re-propose insights that match open or rejected rows with the same type + merchant + summary.

## Analysis patterns (examples)

Count category flips on confirm:

```sql
SELECT json_extract(ai_proposal_json, '$.ai_category') AS from_cat,
       json_extract(user_outcome_json, '$.ai_category') AS to_cat,
       COUNT(*) AS n
FROM decision_events
WHERE source = 'confirm_categories'
  AND action IN ('edited', 'accepted')
GROUP BY 1, 2
HAVING n >= 2
ORDER BY n DESC
```

Merchants with expense history but no cadence rule:

```sql
SELECT t.merchant_key, COUNT(*) AS tx_count
FROM transactions t
LEFT JOIN cadence_rules c ON c.merchant_key = t.merchant_key
WHERE t.flow_type = 'Expense' AND t.amount < 0
  AND c.merchant_key IS NULL
  AND t.merchant_key != ''
GROUP BY t.merchant_key
HAVING tx_count >= 3
ORDER BY tx_count DESC
LIMIT 20
```

## Expense SQL reminders

- Expenses: `flow_type = 'Expense' AND amount < 0`; totals use `SUM(-amount)`.
- Monthly rollups: `budget_month` (`YYYY-MM`).
