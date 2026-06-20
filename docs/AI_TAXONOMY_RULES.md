# AI taxonomy rules (label health UI)

**Status:** Shipped (manual confirm only)  
**Tab:** AI Rules  
**Service:** `webapp/services/taxonomy_rules.py`

## What this is

**Taxonomy rules** merge duplicate or synonym labels across your database:

| Rule type | Example |
|-----------|---------|
| `category_merge` | Charitable Giving → Charitable |
| `sub_category_merge` | ATM withdrawal → ATM Withdrawal (under Banking) |
| `merchant_alias` | 365 Vend LLC → 365 VEND LLC |

This is **not** the same as **Custom Rules** (per-merchant if/then at pipeline time). Taxonomy rules clean vocabulary after categorization.

## Flow (no automation)

1. **Analyze labels** — fast heuristics (string similarity, alias groups)
2. **Suggest with AI** — LLM proposes semantic merges with confidence + rationale
3. User **selects** proposals → **Preview** (dry run + sample before/after transactions) or **Apply** (writes SQLite)

Nothing runs without explicit user selection and `confirm: "APPLY"`.

## Future: confidence-based automation

Each proposal includes `confidence` (0–1) and `automation_ready` when confidence is 1.0 (case/punctuation only). A later phase could auto-apply only when confidence reaches a user threshold.

## APIs

| Endpoint | Role |
|----------|------|
| `GET /api/taxonomy-rules/analyze` | Heuristic proposals |
| `POST /api/taxonomy-rules/suggest` | Heuristic + LLM proposals |
| `POST /api/taxonomy-rules/preview` | Dry-run stats for selected proposals |
| `POST /api/taxonomy-rules/apply` | Apply selected (`confirm: "APPLY"`) |

## Related

- `webapp/services/label_health.py` — underlying merge engine
- `docs/EDIT_INSIGHTS.md` — per-edit CustomRule suggestions (different scope)
