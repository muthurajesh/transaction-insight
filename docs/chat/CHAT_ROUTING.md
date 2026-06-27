# Chat routing fix — top categories vs transaction list

**Status:** Implemented  
**Priority:** High (quick win; blocks correct monthly analysis in chat)

## Problem (reproduced)

User query:

```text
Can you analyze April 2026 expenses and show me the top 10 high expense categories?
```

**Expected:** `top_categories(month=2026-04, limit=10)` → category rollup for full April (~$8,500, 150 txs).

**Actual:** `_maybe_list_transactions_answer` in `webapp/agent/chat.py` intercepts **before the LLM**:

| Trigger | Misread as |
|---------|------------|
| `"show me"` in message | “List transactions” intent |
| `"top 10"` | `limit=10` on **transactions**, not categories |
| `"categories"` | Ignored |

Result: `list_transactions(month=2026-04, limit=10)` → **10 newest rows**, total **$450.00**, not top categories.

## Root cause

```text
chat() → _maybe_direct_answer() → _maybe_list_transactions_answer()  [returns early]
```

LLM never runs. `_chat_payload` replaces prose with table `title + summary` when `display` exists.

## Fix requirements

### 1. Category / aggregation intent wins over list shortcut

Before `_maybe_list_transactions_answer`, detect:

```python
_CATEGORY_INTENT = (
    "top categor", "categories", "by category", "spending by category",
    "expense categor", "breakdown", "group by category",
)
```

If month resolvable + expense flow + category intent → call `top_categories` (or `month_total` + `top_categories` for “analyze”).

### 2. Tighten list-transaction triggers

Do **not** fire on bare `"show me"`. Require stronger signals, e.g.:

- `"list"` / `"show all"` / `"transactions"` / `"table format"` / `"in a table"`
- AND NOT category intent
- OR explicit `"transactions for"` / `"list transactions"`

### 3. Parse `top N` for categories

When category intent detected, `limit = _parse_limit_from_message(msg, default=10)` applies to **categories**, not transactions.

### 4. Optional: `_maybe_top_categories_answer`

New helper mirroring list shortcut:

```python
def _maybe_top_categories_answer(conn, user_message) -> dict | None:
    if not _has_category_intent(user_message):
        return None
    month = _resolve_month_from_message(conn, user_message)
    if not month:
        return None
    limit = _parse_limit_from_message(user_message, default=10)
    result = run_tool(conn, "top_categories", {"month": month, "limit": limit})
    ...
```

Insert in `_maybe_direct_answer` **before** `_maybe_list_transactions_answer`.

## Regression tests (manual or scripted)

| Query | Expected tool |
|-------|----------------|
| `top 10 categories April 2026` | `top_categories` |
| `show me top 10 expense categories for April 2026` | `top_categories` |
| `show me all Insurance transactions for April 2026` | `list_transactions` + category filter |
| `show me 50 transactions last month` | `list_transactions`, limit=50 |

## Files to change

| File | Change |
|------|--------|
| `webapp/agent/chat.py` | Intent helpers, reorder `_maybe_direct_answer`, optional `_maybe_top_categories_answer` |
| `webapp/agent/DATA_CHEATSHEET.md` | Example row for “top categories” vs “list transactions” |

## Verification

1. Ask chat: `top 10 expense categories for April 2026`
2. Tool trace shows `top_categories`, not `list_transactions`
3. Insurance ~$1,200, Groceries ~$800 — not $599 total
4. Mark `[x]` in `docs/product/ROADMAP.md` §2

## Context source

Discussed in project chat when user saw wrong April analysis; bug confirmed via `curl` and `_maybe_direct_answer` reproduction.
