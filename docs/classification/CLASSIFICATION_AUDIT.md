# Classification audit — sampled quality check

**Status:** Shipped  
**Related:** [CLASSIFICATION_TAXONOMY.md](CLASSIFICATION_TAXONOMY.md) · [CONFIRM_CATEGORIES.md](CONFIRM_CATEGORIES.md)

## Purpose

The pipeline uses a fast local model (e.g. `PIPELINE_MODEL=qwen2.5:14b`) for bulk classification. **Classification audit** samples merchants and cross-checks labels using:

1. **Heuristic rules** (no LLM) — obvious category/sub mismatches (e.g. `Food/Dining` + `Home Services`).
2. **Audit LLM** — a stronger local model re-classifies one transaction per sampled merchant; flags disagreements when `confidence >= CLASSIFICATION_AUDIT_MIN_CONFIDENCE`.

Findings are **propose-only** — verify or fix labels in **Edit Transactions** (alerts auto-clear when live labels match the suggestion). No web lookup; same privacy boundary as pipeline classify.

## When it runs

| Tier | Trigger | Sample size (default) |
|------|---------|------------------------|
| **Post-import** | After `Run processing` completes (background thread) | 8 merchants from the file(s) just processed |
| **Scheduled** | CLI / `launchd` | 30 merchants (spend-weighted + not recently audited) |
| **Manual** | Import tab → **Run deep audit** or `POST /api/classification-audit/run` | 30 merchants |

Confirmed `merchant_labels` that match transaction rows are skipped for LLM audit unless heuristics detect drift.

**Vocabulary:** When `CLASSIFY_VOCABULARY_HINT=1`, audit uses the same known labels as pipeline classify (`webapp/services/classification_vocabulary.py`). Comparisons use normalized spellings so `Grocery` vs `Groceries` does not false-alert.

## Environment (`config/.env`)

```env
CLASSIFICATION_AUDIT_ENABLED=1
CLASSIFICATION_AUDIT_MODEL=qwen2.5-coder:32b
CLASSIFICATION_AUDIT_POST_IMPORT_MERCHANTS=8
CLASSIFICATION_AUDIT_SCHEDULED_MERCHANTS=30
CLASSIFICATION_AUDIT_MIN_CONFIDENCE=0.85

CLASSIFY_VOCABULARY_HINT=1
```

If `CLASSIFICATION_AUDIT_MODEL` is unset, the audit uses `PIPELINE_MODEL`.

## APIs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/classification-audit/summary` | Open finding count + last run |
| GET | `/api/classification-audit/findings?status=open` | List findings |
| POST | `/api/classification-audit/run` | Manual deep audit |
| POST | `/api/classification-audit/findings/{id}/dismiss` | Dismiss false alarm |
| POST | `/api/classification-audit/findings/{id}/apply` | Apply suggested category/sub to merchant; resolve finding |
| GET | `/api/classification-audit/findings/{id}/open-merchant` | Merchant key + Edit tab search query |

## Scheduled audit (macOS launchd example)

From the project root, with venv activated in the plist or wrapper script:

```bash
PYTHONPATH=. .venv/bin/python -m webapp.services.classification_audit_cli
```

Example `~/Library/LaunchAgents/com.transactioninsight.audit.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.transactioninsight.audit</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/Transaction Insight/.venv/bin/python</string>
    <string>-m</string>
    <string>webapp.services.classification_audit_cli</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/Transaction Insight</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key>
    <string>/path/to/Transaction Insight</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>2</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/path/to/Transaction Insight/data/audit.log</string>
  <key>StandardErrorPath</key>
  <string>/path/to/Transaction Insight/data/audit.log</string>
</dict>
</plist>
```

Load: `launchctl load ~/Library/LaunchAgents/com.transactioninsight.audit.plist`

Requires LM Studio / Ollama running if the audit model is local.

## UI

**Import & Categorize** tab shows a badge and **Classification alerts** panel when open findings exist. Each alert shows **At audit** (snapshot when flagged), **Suggested**, and **Current in DB** (live). **View in Edit Transactions** opens Edit with the merchant pre-searched; **Dismiss** clears a false positive. In the **Workspace** inbox, **Approve** applies the suggestion; **Reject** dismisses. Open findings auto-resolve when current labels already match the suggestion.

## Storage

- `classification_audit_runs` — run metadata
- `classification_audit_findings` — open/dismissed/resolved items
- `agent_runs` — coarse log (`run_type=classification_audit`)

## Module

`webapp/services/classification_audit.py` · CLI: `python -m webapp.services.classification_audit_cli`
