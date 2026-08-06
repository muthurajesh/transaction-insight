# Agent Workspace UI

**Status:** Shipped (sidebar shell + Simple/Expert mode)  
**Flags:** `UI_AGENT_WORKSPACE=1` (default); `UI_MODE=simple|expert` (default **`simple`**)

**Default UI (Simple):** Left **sidebar** — **Import**, **Ask**, **Check labels**, **Settings**.  
**Expert adds:** **Find & edit**, **Automate** (and legacy Cadence / AI Rules when those flags allow).

Legacy horizontal tabs (**Import & Categorize**, **AI Rules**, **Cadence**) stay hidden when `UI_AGENT_WORKSPACE=1`. (Legacy **Confirm Categories** was removed; use **Check labels**.)

## User workflow (Simple)

| Step | Where |
|------|--------|
| Add bank CSV → process | **Import** — 3-step wizard (Choose → Process → Done) |
| Approve unsure labels | **Check labels** — Needs a look inbox; **Looks good** / Reject / Cancel; badge count on nav only |
| Same store, different spellings | Check labels → expandable **N possible duplicates — review** banner → Combine |
| Questions about spending | **Ask** — no duplicate Pending banner; “review pending” routes to Check labels |
| Mode / paths / models | **Settings** — Simple/Expert toggle (browser override of `UI_MODE`) |

Processing core: [`webapp/pipeline/`](../../webapp/pipeline/) orchestrates [`webapp/processing/`](../../webapp/processing/) (LLM, rules, cadence). Lookups load and save from SQLite (`finance.db`) — see [pipeline/PIPELINE_DB_LOOKUPS.md](../pipeline/PIPELINE_DB_LOOKUPS.md).

## Navigation

| Sidebar item | Mode | Content |
|--------------|------|---------|
| **Import** | Both | Wizard: choose CSV, process, Done → Check labels or Ask |
| **Ask** | Both | Chat / analytics; context meter; Clear screen |
| **Check labels** | Both | Needs a look (primary); optional collapsed **possible duplicates** banner for Combine; badge = payees needing a look |
| **Find & edit** | Expert | Transaction search, merchant groups, bulk label |
| **Automate** | Expert | Custom Rules (English if/then) |
| **Settings** | Both | Display mode, AI models, data store, Learning Agent; Clear data / Cadence rules in Expert |

Top bar: breadcrumb, page title, compact status (**N payees need a look**).

### Copy rules

User-facing chrome prefers: payee/store, needs a look, approved, Combine as one, Looks good. Avoid `merchant_key`, canonical, Pending (as a second badge on Ask).

### Default landing (first load)

1. **Check labels** if inbox count &gt; 0  
2. Else **Import** if no transactions  
3. Else **Ask**

After import Done, user chooses Check labels or Ask (wizard step 3).

## Hidden tabs (legacy, still in DOM)

- AI Rules
- Import & Categorize
- Cadence (`UI_SHOW_CADENCE=0` default with workspace)

## Config

```bash
UI_AGENT_WORKSPACE=1
UI_MODE=simple   # or expert
UI_SHOW_CADENCE=0
EDIT_INSIGHT_ENABLED=1   # 0 = skip post-edit AI insight LLM
```

Settings → **Display mode** stores a browser override in `localStorage` (`ti_ui_mode`).

## Inbox item types

Clicking an item opens a confirm modal with a plain-language summary and affected-transactions preview.

| `confirmation_type` | Source |
|-----------------------|--------|
| `merchant_label` | Label queue |
| `quality_flag` | Classification audit findings |
| `pattern_insight` | Learning Agent |
| `category_rename` | Learning Agent (category rename / merge hints) |
| `cadence_rule` | Learning Agent (cadence candidates) |
| `custom_rule` | Chat `propose_custom_rule` |

Modal actions: **Looks good** · **Reject** · **Cancel** · **Find & edit** (Expert; label and quality only)

| Action | Behavior |
|--------|----------|
| **Looks good** | Apply labels, audit fix, Automate draft, cadence (when enabled), or AI Rules for renames |
| **Reject** | Dismiss audit finding or reject Learning Agent insight |
| **Cancel** | Close; item stays in Needs a look |
| **Find & edit** | Pre-search payee (Expert only) |

Learning Agent `proposal_json.suggested_action` values: `rename_category`, `review_cadence`, `create_rule`, `apply_labels` (legacy tokens normalized automatically).

**From Ask:** Assistant messages may show **Review in Workspace** buttons — opens **Check labels** and the same confirm modal.

## Chat workspace tools

| Tool | Effect |
|------|--------|
| `list_open_insights` | Read open `ai_insights` |
| `run_decision_analysis` | Run Learning Agent → inbox |
| `propose_custom_rule` | Preview rule → Check labels confirm → Automate draft + preview |
| `accept_insight` / `reject_insight` | User explicitly closes an insight by id |

`query_sql` may also read `decision_events`, `ai_insights`, `pipeline_custom_rules`, `category_rules`, `description_lookup`.

Phrases like “review pending” / “check labels” short-circuit to a Check labels pointer (no spending charts).

## APIs

| Method | Path |
|--------|------|
| GET | `/api/pending-confirmations` |
| POST | `/api/pending-confirmations/preview` |
| GET | `/api/review/merchant-aliases` |
| POST | `/api/classification-audit/findings/{id}/apply` |
| GET | `/api/learning-agent/status` |
| POST | `/api/learning-agent/run` |
| POST | `/api/learning-agent/insights/{id}/accept` |
| POST | `/api/learning-agent/insights/{id}/reject` |

## Cadence UX

When `UI_SHOW_CADENCE=0` (default with workspace): no Cadence tab and no cadence rows in Check labels or chat proposal buttons. When `UI_SHOW_CADENCE=1`: cadence appears in the inbox (Learning Agent) and via chat `propose_cadence_rule`; user confirms before `cadence_rules` is written.

## Suggested workflow (monthly)

**Simple (`UI_MODE=simple`):**

1. **Import** — wizard through Done.
2. **Check labels** — Needs a look first; expand **possible duplicates** only when combining names.
3. **Ask** — explore spend.

**Expert:** same plus **Find & edit** and **Automate**; cadence when `UI_SHOW_CADENCE=1`.

**Legacy tabs (`UI_AGENT_WORKSPACE=0`):** Import & Categorize → Check labels → AI Rules → Cadence → Edit → Chat.

Chat dollar amounts use the same spend rules as the pipeline (negative outflows only). Label queue detail → [classification/CONFIRM_CATEGORIES.md](../classification/CONFIRM_CATEGORIES.md).
