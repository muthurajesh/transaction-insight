# Custom Rules

**Status:** Shipped — dedicated **Custom Rules** tab with preview, Flow Type, and id-stable CRUD.

**Related:** [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md) · [CONFIRM_CATEGORIES.md](./CONFIRM_CATEGORIES.md)

## Overview

Custom Rules are plain-English **if/then** patterns. The LLM compiles each rule to JSON once; Python applies that JSON on every **Run processing** (final pipeline pass) or when you **Apply** from the tab.

Use Custom Rules when one merchant needs **different labels by amount or description**, or when you need **monthly split** logic — cases Confirm Categories cannot express (one label per merchant).

## Tab workflow

1. **Rule (plain English)** — multiline composer at the top.
2. **Run preview** — compiles (LLM) and lists matching transactions with **Current** vs **Proposed** labels.
3. **Compiled JSON** — read-only view of the compiled rule.
4. **Saved rules** — click to load; **Apply** one rule, **Disable**, or **Delete**.
5. **Save** — stores as Pending (no DB apply). **Save & apply** — compile + update matching rows. **Apply all rules** — compile pending + apply every Active rule.

Storage: SQLite `pipeline_custom_rules` (stable numeric `id` per rule).

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
| `amount` | Absolute dollar value |

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
