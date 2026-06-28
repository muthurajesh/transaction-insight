# Decision memory

**Status:** Shipped (Phase A–C foundation)  
**Charter:** [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md) §3 Tier D  
**UI:** [AGENT_WORKSPACE.md](AGENT_WORKSPACE.md)

## Purpose

Record user responses to **AI proposals** (not every manual edit) so a background Learning Agent can detect patterns and improve future suggestions.

## Schema

### `decision_events`

Append-only log written at HITL boundaries.

| Column | Meaning |
|--------|---------|
| `source` | `confirm_categories`, `classification_audit`, `taxonomy_rules`, `edit_transactions`, `learning_agent` |
| `entity_type` | `merchant`, `transaction`, `finding`, `insight`, `taxonomy` |
| `entity_key` | Merchant key, finding id, etc. |
| `action` | `accepted`, `edited`, `rejected`, `dismissed`, `deferred` |
| `ai_proposal_json` | What AI suggested |
| `user_outcome_json` | What the user chose |
| `context_json` | Extra metadata (scope, row counts) |

Service: `webapp/services/decision_events.py`

### `ai_insights`

Learning Agent output awaiting user review in the Workspace inbox.

### `agent_runs`

Coarse log with `run_type=learning_agent`.

## Instrumentation

| Flow | When logged |
|------|-------------|
| Confirm Categories | `POST /api/review/{merchant}/confirm` with optional `suggested_labels` |
| Audit dismiss | `POST /api/classification-audit/findings/{id}/dismiss` |
| AI Rules apply | `POST /api/taxonomy-rules/apply` |
| Edit Transactions | `bulk_update_labels` when category/sub/type/classification changes |

## Learning Agent

Env (`config/.env`):

```env
LEARNING_AGENT_ENABLED=0
LEARNING_AGENT_INTERVAL_HOURS=3
```

- In-app scheduler starts on FastAPI lifespan when enabled
- CLI: `PYTHONPATH=. .venv/bin/python -m webapp.services.learning_agent_cli`
- Manual: `POST /api/learning-agent/run` (works even when env disabled)

Outputs open `ai_insights` rows; accept/reject via inbox or API.

## Review suggest feedback loop

Accepted insights are included in `review_suggest` prompt context as `recent_user_corrections`.
