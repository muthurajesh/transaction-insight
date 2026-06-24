# Rich chat UI plan (Option A)

Transaction Insight chat is **our code** (`webapp/static/` + `webapp/agent/chat.py`). Ollama only provides the LLM. This document plans a richer presentation layer without replacing the agent, database tools, or review workflow.

## Original problem (solved in Tier 1)

Assistant replies were markdown (tables, bold, totals) but the UI escaped HTML and showed raw `| Date | Amount |` text. Tool traces appeared as a one-line footer. Tier 1 fixed rendering, tables, and collapsible traces.

## Design principles

1. **Keep** `/api/chat`, tools (`query_sql`, custom reports, etc.), and Ollama as-is.
2. **Enhance** the browser layer with small, CDN-friendly libraries (no React rewrite).
3. **Optional structured `display`** in API responses for interactive widgets; markdown remains the fallback.
4. **Write access** stays limited to `custom_reports` — not transactions.

## Architecture

```text
User → Chat UI (app.js) → POST /api/chat → chat.py (agent + tools) → SQLite
                ↑
         answer (markdown prose)
         display (optional JSON: table | chart)
         tool_trace (collapsible)
```

## Option A tiers

### Tier 1 — Foundation (implemented)

| Item | Library / API | Purpose |
|------|----------------|---------|
| Markdown rendering | [marked](https://marked.js.org/) + [DOMPurify](https://github.com/cure53/DOMPurify) | Tables, bold, lists in assistant messages |
| Interactive tables | [Tabulator](https://tabulator.info/) | Sort, filter, scroll when `display.type === "table"` |
| Wider layout | CSS | Chat panel matches review width (~1280px) |
| Voice input | Web Speech API | Mic — continuous listen; 3s silence or 30s cap; auto-send when done (Chrome / Edge) |
| Tool trace | Collapsible `<details>` | Show which tools ran without clutter |
| Loading state | UI only | “Thinking…” while waiting for Ollama |

**Backend:** `display` payload attached when the last tool returns tabular data (`list_transactions`, `query_sql`, `run_custom_report`).

**Also shipped (post–Tier 1):** **Help panel** — side panel with abbreviated commands, expand-for-description, and **+** insert into chat (`CHAT_HELP_COMMANDS` in `app.js`). Covers the Tier 3 “suggested prompts” idea in a richer form; inline chips are **not planned**. **Voice input UX** — mic focuses the composer, streams transcript into the field, stops on 3s silence or 30s max, and auto-submits when listening ends (manual **Listening…** stop leaves text for edit).

### Tier 2 — Charts & summaries (implemented)

| Item | Library | Purpose | Status |
|------|---------|---------|--------|
| Charts | [Chart.js](https://www.chartjs.org/) | Bar charts for `flow_totals_by_month`, `top_categories` | **Done** |
| Summary chips | CSS | Pill badges for month · category · total · row count | **Skipped** — markdown summary above widgets |
| Export CSV | Tabulator built-in | Download visible table rows | **Done** |
| Chat history on load | `GET /api/chat/history` | Restore thread on refresh; `display` rebuilt from `tool_trace` | **Done** |

**Backend:** `display.type === "chart"` with `{ labels, datasets, chartType }`.

### Tier 3 — Workflow polish

| Item | Purpose | Status |
|------|---------|--------|
| “Save as report” button | On table messages → `save_custom_report` | **Still needed** |
| ~~Suggested prompts~~ | ~~Chips in composer~~ | **Superseded by Help panel** (labels, descriptions, insert) |
| Multiline composer | Shift+Enter; taller input for long questions | **Still needed** |
| Stream tokens | SSE from `/api/chat` (optional; needs backend streaming) | **Optional** — nice-to-have, not required for finance Q&A |

### Tier 4 — Only if vanilla JS becomes painful

Consider **assistant-ui** (React) or a dedicated chat shell — see comparison below. Not required if Tiers 1–3 meet the product goal.

## API shape (Tier 1+)

```json
{
  "answer": "8 Insurance transactions in April 2026. Total: $1,200.00",
  "tool_trace": [{ "tool": "list_transactions", "args": {}, "result": {} }],
  "display": {
    "type": "table",
    "title": "Expense — Insurance — 2026-04",
    "columns": [
      { "field": "date", "title": "Date" },
      { "field": "amount", "title": "Amount", "formatter": "money" }
    ],
    "rows": [{ "date": "2026-04-01", "amount": -80.96, "merchant_key": "..." }]
  }
}
```

Tier 2 chart example:

```json
{
  "display": {
    "type": "chart",
    "chartType": "bar",
    "title": "Expense by month",
    "labels": ["2026-03", "2026-04"],
    "datasets": [{ "label": "Total", "data": [4200, 5100] }]
  }
}
```

## Alternatives (not upgrades by default)

| Path | When justified |
|------|----------------|
| **Option B** — React (assistant-ui) | Chat becomes the main product; need streaming, threads, tool cards at scale |
| **Option C** — Open WebUI | Willing to split “pipeline app” vs “generic Ollama chat” |
| **Option D** — LiteLLM `/chat` | Building a multi-provider LLM gateway, not finance-specific chat |

Option A Tier 1–3 is the right default for an integrated local finance app on GitHub.

## Files touched

| Tier | Files |
|------|--------|
| 1 + Help | `webapp/static/index.html`, `app.js`, `styles.css`; `webapp/agent/display.py`, `chat.py` |
| 2 | Same + Chart.js; extend `display.py` for chart tools; optional `GET /api/chat/history` |
| 3 | `app.js` (save-from-table, multiline composer); optional SSE in `chat.py` / `main.py` |

## Voice input notes

- Uses `SpeechRecognition` / `webkitSpeechRecognition` (Chromium).
- On unsupported browsers the mic button is disabled with a tooltip.
- Clicking **Mic** focuses the chat input and starts listening; transcript appears in the field as you speak.
- Stops automatically after **3 seconds** of silence or **30 seconds** of continuous listening (whichever comes first). Click **Listening…** to stop early without sending.
- When listening ends on its own and there is transcribed text, the message is sent automatically (same as **Send**).
- Audio may be processed by the browser vendor’s speech service when using Web Speech API.

## Verification

After each tier: open Chat tab, ask e.g. “Show all Insurance transactions for April 2026”, confirm rendered table, sort/filter, mic (Chrome), tool trace expand, and Help **+** insert.

## Revised scope before Tier 2 & 3

**Drop or defer**

- Tier 3 suggested-prompt chips (Help panel replaces them).
- Tier 2 summary chips unless you want extra visual polish (markdown summary already exists).
- Tier 3 streaming unless chat latency becomes a pain point.

**Keep for Tier 2**

- Chart.js + `display.type === "chart"`.
- Tabulator CSV export.
- Chat history restore (wire existing `chat_messages` to UI).

**Keep for Tier 3**

- Save-as-report on table replies.
- Multiline composer (Shift+Enter).
