# Expense cadence — Phase B (analytics & chat views)

**Status:** Implemented  
**Depends on:** [EXPENSE_CADENCE.md](EXPENSE_CADENCE.md) (Phase A — done)

## Goal

Answer “how much did I spend in May?” in three ways:

| View | InsurerCo $900.00 in May | Semi-annual $600 in May |
|------|------------------------|-------------------------|
| **cash** | $900.00 (bank reality) | $600.00 |
| **core** | $0 (excluded from run-rate) | $0 |
| **normalized** | $75.00/mo (`/12`) | $100/mo (`/6`) |

User example from chat: **InsurerCo Prem Pay** — annual premium should not dominate “monthly expense” views.

## Non-goals (Phase B)

- Edit UI for cadence (Phase C)
- AI layer authoring (Phase D)
- Baking / materialized cache (Phase E)

## Core APIs (already exist)

- `webapp/services/expense_cadence.py`
  - `resolve_effective_cadence(conn, transaction_id=..., merchant_key=...)`
  - `effective_amount(amount, view=..., kind=..., period_count=..., period_unit=..., include_in_run_rate=...)`
- `GET /api/transactions/{id}/cadence` — per-row effective amounts

## Implementation plan

### 1. Shared aggregation helper

Add to `webapp/services/expense_cadence.py`:

```python
def sum_expenses_for_view(
    conn: sqlite3.Connection,
    *,
    view: str = "cash",
    budget_month: str | None = None,
    budget_months: list[str] | None = None,
    category: str | None = None,
) -> tuple[float, int]:
    """Iterate matching expense rows; sum effective_amount per row."""
```

Query base:

```sql
SELECT transaction_id, amount, merchant_key,
       cadence_kind, period_count, period_unit, include_in_run_rate
FROM transactions
WHERE flow_type = 'Expense' AND amount < 0
  AND budget_month = ?   -- or IN (...)
  [AND ai_category = ?]
```

For each row: resolve cadence (tx columns → `cadence_rules` → default), then `effective_amount`.

**Do not** only change SQL `SUM(-amount)` — normalized/core require per-row logic.

### 2. Update analytics tools

| Tool | File | Change |
|------|------|--------|
| `month_total` | `webapp/analytics/queries.py` | Add `expense_view: str = "cash"`; use helper for Expense flow |
| `top_categories` | same | Group by `ai_category` using effective amounts per row |
| `flow_totals_by_month` | same | Monthly totals per view |
| `month_vs_avg`, `list_outliers` | same | Optional in B; defer if scope tight |

### 3. Wire `expense_view` in chat tools

`webapp/agent/tools.py` — add optional arg:

```json
{ "month": "2026-05", "limit": 10, "expense_view": "normalized" }
```

Default: **`cash`** (backward compatible).

`TOOL_DEFINITIONS` descriptions: explain when user asks “monthly budget”, “normalized”, “run-rate”, “exclude annual”.

### 4. Agent prompts

Update:

- `webapp/agent/chat.py` — `CHAT_SYSTEM` + examples
- `webapp/agent/DATA_CHEATSHEET.md` — view semantics, example questions

Example mapping:

| User says | `expense_view` |
|-----------|----------------|
| “how much did I spend” (default) | `cash` |
| “monthly run-rate”, “core spending” | `core` |
| “normalized monthly”, “spread annual”, “budget impact” | `normalized` |

### 5. Display layer

`webapp/agent/display.py`:

- Append view to chart/table title: `Top categories — 2026-04 (Normalized)`
- Summary line: `View: Normalized monthly equivalent`

### 6. Custom reports parameter

`webapp/services/custom_reports.py`:

- Add `expense_view` to `_ALLOWED_PARAMS` with validation `cash|core|normalized`
- **Note:** Saved SQL templates cannot use per-row normalization unless reports call Python or use pre-built views. Phase B options:
  - **A (simple):** Document that `run_custom_report` with raw SQL stays **cash**; normalized reports use chat tools / new `run_expense_report` helper later
  - **B (better):** Add named report templates in code for normalized top-N

Recommend **A** for B, document limitation in [CHAT_CUSTOM_REPORTS.md](../chat/CHAT_CUSTOM_REPORTS.md).

### 7. Chat routing fix

Ship [CHAT_ROUTING.md](../chat/CHAT_ROUTING.md) with or before Phase B.

## Test plan

| Case | cash | normalized |
|------|------|--------------|
| April 2026 total expenses | ~$8,500 | Lower (annual lumps spread) |
| InsurerCo ~$975 in April | full amount in cash | ~$81/mo in normalized |
| Groceries monthly spend | ≈ same | ≈ same |

```bash
# After implementation
curl -s 'http://127.0.0.1:8000/api/...'  # or chat: "normalized top categories April 2026"
```

## Files touched (expected)

- `webapp/analytics/queries.py`
- `webapp/agent/tools.py`
- `webapp/agent/chat.py`
- `webapp/agent/display.py`
- `webapp/agent/DATA_CHEATSHEET.md`
- `docs/product/ROADMAP.md` — mark Phase B items `[x]`

## Context from project chat

- User wants clarity on **monthly** vs **yearly** vs **one-time** (InsurerCo insurance)
- Semi-annual auto insurance → `period_count=6`, `period_unit=months`
- Bi-weekly → `period_count=2`, `period_unit=weeks`
- Layer 0 amounts never mutated; views are read-time lenses
