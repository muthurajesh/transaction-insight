# Custom Rules

**Status:** Shipped — dedicated **Custom Rules** tab with preview, Flow Type, and id-stable CRUD.

**Related:** [EDIT_INSIGHTS.md](EDIT_INSIGHTS.md) · [CONFIRM_CATEGORIES.md](../classification/CONFIRM_CATEGORIES.md)

## Overview

Custom Rules are plain-English **if/then** patterns. The LLM compiles each rule to JSON once; Python applies that JSON on every **Run processing** (final pipeline pass) or when you **Apply** from the tab.

Use Custom Rules when one merchant needs **different labels by amount or description**, or when you need **monthly split** logic — cases Confirm Categories cannot express (one label per merchant).

## Tab workflow

1. **Build a Simple Rule** (optional) — collapsible helper: pick When conditions (Generated Description / Description / Amount), Then set fields, **Insert** or **Replace** into the composer. **What can I use?** expands a cheatsheet with examples, match keys, operators, and settable fields.
2. **Rule (plain English)** — multiline composer at the top.
3. **Run preview** — compiles (LLM) and lists matching transactions with **Current** vs **Proposed** labels.
4. **Compiled JSON** — read-only view of the compiled rule.
5. **Saved rules** — click to load; **Apply** one rule, **Disable**, or **Delete**. **New rule** clears selection so **Save** creates a separate rule (editing an existing rule updates it in place).
6. **Save** — stores as Pending (no DB apply). **Save & apply** — compile + update matching rows. **Apply all rules** — compile pending + apply every Active rule.

Composer hint shows **Editing saved rule #N** vs **New rule — not saved yet**.

Storage: SQLite `pipeline_custom_rules` (stable numeric `id` per rule).

**Apply behavior:** matching rows get `label_status = confirmed` and drop off **Confirm Categories**, even when labels were already correct before apply.

## Rule types (compiled JSON)

| `rule_type` | Purpose |
|-------------|---------|
| `assign` | When all match criteria match → set fields on those rows |
| `monthly_split_max` | Same merchant in a month; largest \|amount\| gets `when_max`, others get `when_other` |

### Match keys (AND)

| Key | Matches |
|-----|---------|
| `description` | Generated + Original + Simple + User text (wildcards `*text*`, `prefix*`, `*suffix`) |
| `generated_description` | Merchant label only |
| `amount` | Absolute dollar value — **exact** match (`-36` matches amount `36`). JSON **array** = OR (e.g. `["36","69.31"]`). **Not supported:** greater than, less than, or ranges — use explicit amounts or `description` patterns. |

### Set fields

`category`, `ai_category`, `ai_sub_category`, `classification`, `type` (Fixed/Variable), `sub_type`, `budget_tier`, **`flow_type`** (`Expense` \| `Income` \| `Transfer` \| `Adjustment`).

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/custom-rules` | List rules (includes `id`, `compiled_rule` when Active) |
| POST | `/api/custom-rules` | Add rule (Pending) |
| GET | `/api/custom-rules/{id}` | Rule detail |
| PUT | `/api/custom-rules/{id}` | Update text and/or status |
| DELETE | `/api/custom-rules/{id}` | Remove rule |
| POST | `/api/custom-rules/preview` | `{ rule_text?, rule_id?, limit?, offset? }` |
| POST | `/api/custom-rules/{id}/apply` | Compile if needed + apply one rule |
| POST | `/api/custom-rules/compile-apply` | Compile all pending + apply all Active |
| POST | `/api/custom-rules/save-apply` | Add + apply new rule |

## Files

| File | Role |
|------|------|
| `webapp/processing/custom_rules.py` | Compile, match, apply, preview |
| `webapp/services/custom_rules.py` | CRUD, preview API, apply to SQLite |
| `webapp/static/app.js` | Custom Rules tab UI |
| `webapp/llm/prompts.py` | `CUSTOM_RULE_COMPILER_PROMPT` |

## Tests

`tests/test_custom_rules.py` (match/apply engine) · `tests/test_custom_rules_service.py` (CRUD, preview, apply-one).
