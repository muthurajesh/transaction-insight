# Agent Workspace UI

**Status:** Shipped (MVP)  
**Flag:** `UI_AGENT_WORKSPACE=1` (default in `config/.env.example`)

**Default UI:** **Workspace** — import strip, chat/analytics, and a **pending inbox** (review queue, audit flags, Learning Agent insights). Tabs: Workspace, Edit Transactions, Custom Rules, Settings. Legacy **Import & Categorize**, **Confirm Categories**, **AI Rules**, and **Cadence** are hidden (`UI_AGENT_WORKSPACE=0` restores them).

## User workflow

| Step | Where |
|------|--------|
| Upload & process CSV | **Workspace** import strip (or **Import & Categorize** when legacy UI) — progress bar shows elapsed time and ETA |
| Pending confirmations | **Workspace** inbox — grouped by type (Labels, Quality flags, Insights, …) with counts; click a row for a human-readable proposal summary → **Approve** / **Reject** / **Cancel** (cadence group when `UI_SHOW_CADENCE=1`) |
| Classification quality alerts | Inbox (`quality_flag`) or **Import & Categorize** alerts panel → **View in Edit Transactions** |
| First visit | Guided **quick tour** (once per browser; skipped when data already exists) |
| Fix uncertain merchants | Inbox (`merchant_label`) or **Confirm Categories** (legacy) |
| Clean duplicate labels | **AI Rules** (legacy tab) — merge synonyms (user confirms before apply) |
| Edit rows, custom rules | **Edit Transactions**, **Custom Rules** |
| Ask questions | **Workspace** chat — type or **Mic** (Chrome/Edge); voice stops after 3s silence or 30s max and sends automatically |

Processing core: [`webapp/pipeline/`](../../webapp/pipeline/) orchestrates [`webapp/processing/`](../../webapp/processing/) (LLM, rules, cadence). Lookups load and save from SQLite (`finance.db`) — see [pipeline/PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md).

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

The **Pending** panel groups items by type (Labels, Quality flags, Cadence, Custom rules, Category & taxonomy, Insights). Each group header shows a count; only one group is expanded at a time.

Clicking an item opens **Review AI proposal** with a plain-language summary (label status, AI rationale, proposed labels) and an affected-transactions preview.

| `confirmation_type` | Source |
|-----------------------|--------|
| `merchant_label` | Review queue |
| `quality_flag` | Classification audit findings |
| `pattern_insight` | Learning Agent |
| `category_rename` | Learning Agent (category rename / merge hints) |
| `cadence_rule` | Learning Agent (cadence candidates) |
| `custom_rule` | Chat `propose_custom_rule` |

Unified modal: **Approve** · **Reject** · **Cancel** · **Edit in Transactions** (label and quality only)

| Action | Behavior |
|--------|----------|
| **Approve** | Apply labels (review confirm), apply audit fix, open Custom Rules draft + preview, open cadence modal (when `UI_SHOW_CADENCE=1`), or open AI Rules for category renames |
| **Reject** | Dismiss audit finding or reject Learning Agent insight |
| **Cancel** | Close; item stays in inbox |
| **Edit in Transactions** | Pre-search merchant (label / quality only) |

Learning Agent `proposal_json.suggested_action` values: `rename_category`, `review_cadence`, `create_rule`, `apply_labels` (legacy tokens normalized automatically).

**From Chat:** Assistant messages may show **Review in Workspace** buttons for custom rule drafts and new Decision Analyst insights — same modal and APIs as the pending panel.

## Chat workspace tools

| Tool | Effect |
|------|--------|
| `list_open_insights` | Read open `ai_insights` |
| `run_decision_analysis` | Run Learning Agent → inbox |
| `propose_custom_rule` | Preview rule → Workspace confirm → Custom Rules draft + preview |
| `accept_insight` / `reject_insight` | User explicitly closes an insight by id |

`query_sql` may also read `decision_events`, `ai_insights`, `pipeline_custom_rules`, `category_rules`, `description_lookup`.

## APIs

| Method | Path |
|--------|------|
| GET | `/api/pending-confirmations` |
| POST | `/api/pending-confirmations/preview` |
| POST | `/api/classification-audit/findings/{id}/apply` |
| GET | `/api/learning-agent/status` |
| POST | `/api/learning-agent/run` |
| POST | `/api/learning-agent/insights/{id}/accept` |
| POST | `/api/learning-agent/insights/{id}/reject` |

## Cadence UX

When `UI_SHOW_CADENCE=0` (default with workspace): no Cadence tab and no cadence rows in the Pending inbox or chat proposal buttons. When `UI_SHOW_CADENCE=1`: cadence appears in the inbox (Learning Agent) and via chat `propose_cadence_rule`; user confirms before `cadence_rules` is written.

## Suggested workflow (monthly)

**Workspace (default):**

1. **Workspace** — upload/process CSV(s); work the **pending inbox** (labels, audit flags, Learning Agent insights).
2. **Edit Transactions** / **Custom Rules** — fix one-offs; save rules when a pattern repeats.
3. **Workspace chat** — explore spend; cadence keywords + merchant name still open cadence proposals when `UI_SHOW_CADENCE=1`. **Mic** transcribes (3s silence or 30s cap) and auto-sends.

**Legacy tabs (`UI_AGENT_WORKSPACE=0`):** Import & Categorize → Confirm Categories → AI Rules → Cadence → Edit → Chat (same order as before).

Chat dollar amounts use the same spend rules as the pipeline (negative outflows only). Label queue detail → [classification/CONFIRM_CATEGORIES.md](../classification/CONFIRM_CATEGORIES.md).
