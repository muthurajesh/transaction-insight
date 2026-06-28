# Agent Workspace UI

**Status:** Shipped (MVP)  
**Flag:** `UI_AGENT_WORKSPACE=1` (default in `config/.env.example`)

## Layout

When enabled, the default tab is **Workspace** (formerly Chat-only):

| Region | Content |
|--------|---------|
| Import strip | Choose CSV + Run processing |
| Conversation | Chat, analytics, saved reports |
| Pending panel | Unified inbox from `GET /api/pending-confirmations` |

## Hidden tabs (legacy, still in DOM)

- Confirm Categories
- AI Rules
- Import & Categorize
- Cadence (`UI_SHOW_CADENCE=0` default with workspace)

## Visible tabs

- **Workspace**
- **Edit Transactions**
- **Custom Rules**
- **Settings** (includes Learning Agent status + Run now)

## Inbox item types

| `confirmation_type` | Source |
|-----------------------|--------|
| `merchant_label` | Review queue |
| `quality_flag` | Classification audit findings |
| `pattern_insight` | Learning Agent |
| `cadence_rule` | Learning Agent (cadence candidates) |

Unified modal: Approve · Dismiss · Defer · Edit in Transactions

## APIs

| Method | Path |
|--------|------|
| GET | `/api/pending-confirmations` |
| GET | `/api/learning-agent/status` |
| POST | `/api/learning-agent/run` |
| POST | `/api/learning-agent/insights/{id}/accept` |
| POST | `/api/learning-agent/insights/{id}/reject` |

## Cadence UX

No Cadence tab. Cadence is inferred by the Learning Agent and proposed in the inbox; user confirms before `cadence_rules` is written. Corrections via Edit Transactions or chat.
