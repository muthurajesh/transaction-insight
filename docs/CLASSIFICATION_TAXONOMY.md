# Classification — payload, prompt, and taxonomy goals

**Status:** Review-path payload + prompt shipped; vocabulary minimization is ongoing (AI Rules + future pipeline assist).

**Related:** [AI_TAXONOMY_RULES.md](./AI_TAXONOMY_RULES.md) · [CONFIRM_CATEGORIES.md](./CONFIRM_CATEGORIES.md)

## Pipeline classify (production)

`classify_review_rows` runs only for spend outflows that still need sub-category or `Budget Tier = Review` (`classify_review_mask`). Flow type (Income/Expense/Transfer) is already set in code before the LLM runs.

### LLM payload (minimal)

| Field | Source | Purpose |
|-------|--------|---------|
| `index` | Row index | Map results back |
| `date` | Transaction date | Context |
| `amount` | `Amount_Numeric` | Sign and magnitude (single numeric field) |
| `original_category` | Bank `Category` | Anchor / map into broad category |
| `generated_description` | `Generated Description` | Merchant or payee (from description cache + LLM) |
| `account` | `Account Name` | CC vs checking nuance |

Not sent: `user_description`, `simple_description`, `original_description`, duplicate amount strings.

### LLM output

`category`, `sub_category`, `type`, `budget_tier` → `AI Category`, `AI Sub-Category`, `Type`, `Budget Tier`.

## Taxonomy design goals

| Layer | Goal | Example |
|-------|------|---------|
| **AI Category** | Small, stable, **broad** set | `Food/Dining`, `Income`, `Transfers`, `Utilities` |
| **AI Sub-Category** | **Minimal** finer labels under the category | `Groceries`, `Restaurants`, `Salary` — not merchant names |

### Problems to avoid

- Duplicate labels at different levels: `Salary` (sub) vs `Paychecks/Salary` (category).
- Sub-category repeating the merchant (`Trader Joe's` as sub-category).
- Bank jargon copied verbatim when a broad label is clearer (`Restaurants/Dining` → `Food/Dining` + `Restaurants`).

The classify prompt instructs the model to prefer broad categories and minimal sub-categories. **Vocabulary hint + post-normalize** (shipped) load distinct labels from SQLite for both pipeline classify and classification audit.

## Vocabulary hint (shipped)

When `CLASSIFY_VOCABULARY_HINT=1` (default):

1. **Load** — `webapp/services/classification_vocabulary.py` reads distinct `ai_category` / `ai_sub_category` from `transactions` ∪ `merchant_labels` (same source as Confirm Categories options).
2. **Classify** — appended once per LLM batch in `classify_batch`; results passed through `normalize_classify_labels`.
3. **Audit** — same vocabulary block in `audit_classify_single`; production vs suggested compared with `labels_equivalent` after normalize.

Env: `CLASSIFY_VOCABULARY_MAX_CATEGORIES`, `CLASSIFY_VOCABULARY_MAX_SUBS`, `CLASSIFY_VOCABULARY_SIMILARITY`.

## Future work

| Approach | Where |
|----------|--------|
| **AI Rules** — merge synonym categories/sub-categories | **AI Rules** tab (shipped); user confirms before apply |
| **Auto-apply merges** at confidence threshold | [ROADMAP.md](./ROADMAP.md) §3 |

Goal: reports and charts show a **minimal** category tree without manual cleanup every import.
