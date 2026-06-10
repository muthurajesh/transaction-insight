# Expense cadence — Phase D (AI suggestions)

**Status:** Slice D1 implemented  
**Depends on:** [EXPENSE_CADENCE.md](./EXPENSE_CADENCE.md) (Phase A), [EXPENSE_CADENCE_PHASE_C.md](./EXPENSE_CADENCE_PHASE_C.md) (manual Edit UI)

## Goal

Replace “code a new rule per merchant” with **AI judgment + user confirm**. The model sees merchant history and plain-English context, proposes cadence, user approves once → `cadence_rules` (and optional tx columns).

Phase C = **manual** Edit UI. Phase D = **AI-suggested** cadence (same save path, different entry point).

## Why (product)

Pipeline **Detected** cadence (`analyze_merchant_cadence_profiles`) is rule-based math — no LLM, no confidence, brittle on new patterns. User direction: AI owns ambiguous judgment; rules/SQL own exact math (Layer 0 amounts, dedup, `effective_amount`).

## Slice D1 — AI cadence propose + confirm (implement first)

### Triggers

| Entry | Example |
|-------|---------|
| **Chat** | “Mercury is yearly insurance — spread monthly” |
| **Post-import review** (optional v1.1) | After Run processing on large history file, queue merchants with `cadence_kind=unknown` and 3+ months of data |

### LLM input (per merchant)

Reuse stats pattern from [EDIT_INSIGHTS.md](./EDIT_INSIGHTS.md) `_gather_stats`:

- `merchant_key`, transaction count, months active
- Top amounts + counts, same-amount count
- Existing `cadence_rules` row (if any) — do not duplicate
- Sample dates/amounts for spike months (cap rows sent)

### LLM output (JSON)

```json
{
  "insight": "2 charges ~$900–$975 about 12 months apart; typical month near $0.",
  "cadence_kind": "lump",
  "period_count": 12,
  "period_unit": "months",
  "include_in_run_rate": false,
  "cadence_note": "Annual auto premium",
  "confidence": "high",
  "recommend_save_rule": true,
  "apply_scope": "merchant"
}
```

Kinds: `recurring`, `lump`, `one_time`, `exclude`, `unknown` (same as Phase C / `expense_cadence.py`).

### UX (mirror edit insights)

1. Assistant shows rationale + preview amounts (cash / core / normalized)
2. Modal: **Save merchant rule** / **This transaction only** / **Dismiss**
3. On save → existing `POST /api/cadence-rules` or bulk-label cadence payload
4. If similar rule exists → hide save, show existing rule (reuse `custom_rule_similarity` pattern)

### APIs (new)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/cadence-rules/propose` | Body: `merchant_key`, optional `transaction_id`, optional user `hint` → proposal JSON |
| `POST /api/cadence-rules/propose-batch` | Optional: top N merchants needing cadence after import |

Chat may call propose via tool `propose_cadence_rule` instead of direct HTTP from browser.

### Files (Slice D1)

| File | Change |
|------|--------|
| `webapp/services/cadence_insights.py` | **New** — gather stats, LLM prompt, fallback heuristic, duplicate check |
| `webapp/main.py` | Propose endpoint(s) |
| `webapp/agent/tools.py` | `propose_cadence_rule` tool |
| `webapp/agent/chat.py` | Route cadence-explanation messages to tool + confirm flow |
| `webapp/static/app.js` | Cadence confirm modal (reuse edit-insight overlay pattern) |
| `webapp/static/index.html` | Modal markup if not shared |
| `tests/test_cadence_insights.py` | Proposal parsing, duplicate suppression |

### Pipeline change (after D1 ships)

| Option | Behavior |
|--------|----------|
| **Demote Detected** | `skip_cadence_detection=true` by default in web process; rely on lookup + user + AI |
| **Keep Detected** | Run as pre-fill only when `cadence_kind` still unknown; AI review queue overrides |

Decision at implement time — default recommendation: **demote Detected** once D1 batch review exists.

### Verification (Slice D1)

1. Chat: “Mercury Ins is annual” → proposal lump 12 mo, normalized ~$77 on sample tx
2. Confirm → `GET /api/cadence-rules/Mercury Ins Mcc Ppa` returns rule
3. Re-ask → no duplicate save; shows existing rule
4. Normalized April categories (Phase B) reflect change without Edit UI

---

## Slice D2 — Report layers (defer)

Saved **report layers** beyond cadence — e.g. “exclude transfers from spend lens.” Full spec: [REPORT_LAYERS.md](./REPORT_LAYERS.md) § Phase D (stories 2–3).

| Item | Doc |
|------|-----|
| `report_layers` table + `config_json` | REPORT_LAYERS § Data model |
| `save_report_layer` / `list_report_layers` chat tools | REPORT_LAYERS § Chat tools |
| List / enable / disable UI | REPORT_LAYERS § List / enable |

## Slice D3 — Layered report execution (defer)

`run_layered_report` stack: base query → layers → `expense_view`. Depends on D2. See REPORT_LAYERS.

## Relationship to Phase E

[E](./REPORT_LAYERS.md) **bake** = promote stable AI layers into columns/snapshots for speed. Build after D1 proves cadence AI loop.

## Context

User wants AI-first cadence after loading Jul 2021–May 2026 history (~12k rows) — not per-merchant code changes. Mercury manual rule (Phase C) is the reference outcome D1 should reproduce via chat + confirm.
