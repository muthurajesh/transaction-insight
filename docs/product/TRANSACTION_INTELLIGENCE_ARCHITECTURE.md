# Transaction Intelligence — Architecture & Implementation Plan

**Status:** Agreed direction (Jul 2026) — **not implemented**  
**Audience:** Product owners (plain-language sections) + Cursor agents (implementation sections)  
**Related:** [ROADMAP.md](ROADMAP.md) §9 · [PRODUCT_CHARTER.md](PRODUCT_CHARTER.md) · [AGENTIC_AI_DESIGN.md](AGENTIC_AI_DESIGN.md)

---

## For users — what we're building and why

### The problem today

Transaction Insight classifies bank exports with a **local LLM** (7B–32B). On a multi-year personal export (thousands of rows):

| Stage | Typical result | Pain |
|-------|----------------|------|
| Generated Description (merchant label) | ~100% acceptable | Slow on first import; fast on re-import via cache |
| Classification (category, sub-category, type) | ~70–80% before review | Bulk of import time |
| End-to-end after user review | ~90% acceptable | **Several hours** per full historical import on local hardware |

Most rows marked `confirmed` in the database were **auto-approved by the pipeline** (LLM said “not Review tier”), not reviewed by a human. True human-verified labels are a smaller subset.

### The goal

A **hybrid transaction intelligence** stack that:

1. **Runs much faster** — small on-device classifier handles bulk rows; LLM only for hard cases.
2. **Gets more accurate over time** — every user confirm/edit teaches *your* taxonomy, not a generic one.
3. **Supports two LLM modes** — local (private, slow) or cloud (fast, PII leaves machine) as **fallback and teacher**, not as the primary classifier on every row.
4. **Stays in one app** — same UI, SQLite DB, Confirm Categories, custom rules, and chat.

We are **not** building or training a general-purpose finance LLM. We are building:

- Deterministic rules and merchant memory (mostly **already shipped**)
- A **small custom classifier** (base model + per-user overlay)
- A **high-parameter LLM (HPLLM)** used offline for learning from corrections — not on every import row
- The existing chat LLM for conversational analytics (unchanged role)

### How it will feel (cold start → steady state)

**New user (months 1–2):**

1. Import 1–2 months of CSV.
2. Base classifier + rules label what they can; uncertain rows go to **Review**.
3. User confirms or edits labels in the existing review UI.
4. Behind the scenes, corrections are analyzed (scheduled + batched) to improve the personal model.

**Established user (month 3+):**

1. Re-import or new months process in **minutes**, not hours.
2. Known merchants hit cache + `merchant_labels` + classifier — few LLM calls.
3. Corrections continue to refine the overlay; optional cloud teacher is opt-in.

### Cloud vs local LLM (your two modes)

| Mode | When used | PII | Cost | Speed |
|------|-----------|-----|------|-------|
| **Local LLM** (7B–128B) | Fallback for low-confidence rows; optional local teacher | Stays on machine | Hardware time | Slower |
| **Cloud LLM** (OpenAI, Gemini, etc.) | Fallback + teacher for correction analysis | Leaves machine | Per-token | Faster |

The **classifier** runs locally either way. Cloud is never required for core import.

---

## For implementers — decision record

### Integrate in current repo (not a fork)

| Option | Verdict | Reason |
|--------|---------|--------|
| New codebase / fork | **Reject** | Duplicates pipeline, HITL, chat, SQLite, years of lookup data |
| Domain module in same repo (`webapp/ml/`) | **Accept** | One product; feature flags; reuse `decision_events`, `merchant_labels` |
| Offline benchmark before pipeline hook | **Required** | Classifier value unproven until measured on real labels |

### Replace Learning Agent & Classification Audit?

| Component | Long-term | Near-term |
|-----------|-----------|-----------|
| **Pipeline LLM classify** (`classify_review_rows`) | **Replace** with classifier + LLM fallback | Primary target |
| **Learning Agent** | **Replace** with HPLLM teacher + distillation job | Keep until teacher ships |
| **Classification Audit** | **Reduce then retire** | Keep as safety net until high-confidence error rate is acceptable |

Do **not** train the classifier on 7k+ `pipeline` + `confirmed` rows as gold — that is LLM self-approval, not human truth.

### Label quality tiers (illustrative)

Example mix after a long personal import (orders of magnitude only — not a published dataset):

| label_status | rationale | share (approx.) | Use in ML |
|--------------|-----------|----------------:|-----------|
| confirmed | user confirmed | small | **Gold** |
| confirmed | user edited | small | **Gold** |
| confirmed | custom rule | medium | **Strong** (user-approved rules) |
| confirmed | pipeline | majority | **Silver only** — not human-verified |
| needs_review | pipeline | meaningful | Hard cases; LLM fallback target |

**Gold human labels** are typically a minority of rows. Plan cold start and benchmarks around that.

---
## Architecture

### High-level flow

```text
CSV Import
    │
    ▼
Layer 1 — Normalization (cache, lookup, text cleanup)
    │
    ▼
Layer 2 — Rules & merchant memory (category_rules, merchant_labels, custom rules)
    │
    ▼
Layer 3 — Custom classifier (base + user overlay)
    │     ├─ high confidence → accept
    │     ├─ medium confidence → accept + needs_review
    │     └─ low confidence → fall through
    ▼
LLM fallback (local or cloud) — review-mask rows only
    │
    ▼
Store results + provenance in SQLite
    │
    ▼
Chat assistant (14B–32B) — queries DB via tools; does NOT classify at import
```

**Precedence (unchanged):** Custom Rules (final) → confirmed `merchant_labels` → category/business rules → **classifier** → LLM fallback.

### Layer mapping to existing code

| Layer | Existing | Gap |
|-------|----------|-----|
| 1 Normalization | `llm/descriptions.py`, `description_lookup`, `merchant_key` | Deterministic noise stripping; DB aliases |
| 2 Rules & memory | `processing/lookups.py`, `merchant_labels`, `pipeline_custom_rules` | Solid |
| 3 Classifier | `llm/classify.py` → `classify_review_rows()` | **New** `webapp/ml/` |
| Store + audit | `dataframe_store.py`, `decision_events` | `classification_source`, model confidence, `classification_events` |
| Teacher loop | `learning_agent.py`, `classification_audit.py` | **New** distillation job |
| Chat | `webapp/agent/tools.py` | No MCP server yet; extend tools later |

**Pipeline hook (single integration point):**

```text
webapp/pipeline/run.py  (~line 219)
  today:  classify_review_rows()  → LLM for all review-mask rows
  hybrid: classify_review_rows_hybrid()
            → classifier first
            → LLM only for low-confidence subset of review mask
```

### Base classifier + user overlay

| Artifact | Purpose | Location |
|----------|---------|----------|
| **Base model** | Generic English transaction text → labels; shipped or downloadable | `data/models/base/` (gitignored artifacts; manifest in repo) |
| **User overlay** | Retrain / retrieval index from confirms + HPLLM distillation | `data/models/user/` or SQLite tables |
| **Rule promotion** | HPLLM pattern → approved `merchant_labels` / custom rules | Existing tables |

**Recommended v0 architecture:** `all-MiniLM-L6-v2` embeddings + **LightGBM** multi-output heads (category, sub_category, expense_type, budget_tier). Upgrade to fine-tuned `deberta-v3-small` when gold labels exceed ~5k.

**Three ways to apply HPLLM inference after user correction:**

1. **Retrieval** — embedding index of (merchant, labels, reasoning); k-NN before classifier inference.
2. **Scheduled retrain** — batch corrections → training manifest → new `model_version`.
3. **Rule promotion** — user approves HPLLM-suggested pattern → `category_rules` or custom rule (zero inference cost).

Avoid fragile online neural weight updates in v1; prefer retrain on schedule.

### Confidence routing (target)

| Source | confidence | label_status | Next |
|--------|------------|--------------|------|
| custom_rule | 1.0 | confirmed | — |
| merchant_memory | 1.0 | confirmed | — |
| category_rule | 1.0 | confirmed | — |
| classifier P ≥ 0.90 | model score | confirmed | — |
| classifier 0.70–0.89 | model score | needs_review | optional audit sample |
| classifier P < 0.70 | model score | needs_review | **LLM fallback** |
| llm_fallback | — | per tier rules | — |

Thresholds tuned on held-out **gold** merchants (split by `merchant_key`, not random rows).

### HPLLM teacher (replaces Learning Agent function)

**Triggers (async, not blocking import):**

- Single or batched user confirm/edit → distillation example
- Scheduled job: `decision_events` since last run → pattern mining

**Outputs (user approves before apply):**

- Training rows appended to dataset manifest
- Rule proposals (→ existing custom-rule / merchant-label HITL)
- Optional reasoning stored in `classification_events`

**Provider:** `TEACHER_MODEL` — local 32B+ or cloud (opt-in; PII warning in Settings).

---

## Go / no-go gate (benchmark before build)

Do **not** commit to full pipeline integration until offline benchmark passes.

### Phase 0 procedure

1. Export gold + strong labels from `finance.db` (see label tiers above).
2. Train v0 classifier offline (`scripts/` or `webapp/ml/train.py` — TBD).
3. Evaluate on held-out merchants against:
   - Current pipeline labels on same rows
   - Gold user confirmed/edited rows only
4. Measure simulated LLM call reduction (what % of review-mask rows classifier would resolve).

### Minimum success bars (adjust after first run)

| Metric | Bar |
|--------|-----|
| Accuracy vs gold holdout | ≥ current LLM on review rows, target **+10–15%** relative |
| LLM calls avoided (same accuracy) | **≥ 50%** of review-mask rows |
| Full large-export import time (projected) | **≥ 5× faster** vs legacy LLM-heavy path |

If v0 fails → do not integrate; tune rules/cache or narrow classifier scope (category + sub_category only).

### A/B isolation (branch + separate DB)

| Mechanism | Value |
|-----------|-------|
| Git branch | `feature/transaction-intelligence` |
| Benchmark DB | `FINANCE_DB_PATH=data/finance_ti.db` (copy production) |
| Engine flag | `CLASSIFICATION_ENGINE=legacy\|hybrid` (default `legacy`) |
| Shadow mode | Classifier runs, LLM still applies; log disagreement until trusted |

Switch back: `main` branch + `finance.db` + `CLASSIFICATION_ENGINE=legacy`.

---

## Phased implementation plan

### Phase 0 — Offline benchmark (no pipeline changes)

- [ ] Export script: gold/strong labels → JSONL manifest
- [ ] Train v0 MiniLM + LightGBM
- [ ] Evaluation report: accuracy, coverage, projected speedup
- [ ] **Go/no-go decision**

### Phase 1 — Schema & provenance

- [ ] `classification_events` (append-only audit per classification)
- [ ] `model_registry` (version, dataset_id, metrics, artifact path)
- [ ] Columns on `transactions`: `classification_source`, `model_version` (migration)
- [ ] Real `confidence` from model (replace tier-only 0.7/1.0 for classifier rows)

### Phase 2 — Classifier module + shadow mode

- [ ] `webapp/ml/classifier.py` — load model, `predict_batch()`
- [ ] `CLASSIFICATION_ENGINE=hybrid` flag in `webapp/config.py`
- [ ] Hook in `classify_review_rows()` — shadow: log only, LLM still wins
- [ ] `scripts/benchmark_classification.py` — same CSV, two DBs, `PhaseTimer` comparison

### Phase 3 — Hybrid live

- [ ] Apply classifier above threshold; LLM fallback for low confidence
- [ ] Persist provenance on save (`dataframe_store.py`)
- [ ] Review queue driven by model confidence + existing `needs_review`

### Phase 4 — HPLLM teacher & distillation

- [ ] `TEACHER_MODEL` + `TEACHER_PROVIDER` env (local or cloud, opt-in)
- [ ] Correction → distillation pipeline (batched)
- [ ] Scheduled bulk analysis over `decision_events` (replaces Learning Agent scheduler)
- [ ] Retrain job produces new `model_version`; user notified when promoted

### Phase 5 — Converge legacy AI processes

- [ ] Deprecate Learning Agent when teacher outputs rule proposals + training rows
- [ ] Reduce Classification Audit frequency; retire when high-confidence error rate acceptable
- [ ] Update [DECISION_MEMORY.md](DECISION_MEMORY.md) and [CLASSIFICATION_AUDIT.md](../classification/CLASSIFICATION_AUDIT.md) with migration notes

### Phase 6 — Chat intelligence tools (optional)

- [ ] `get_low_confidence_transactions` tool
- [ ] Structured wrappers: spending summary, compare periods, recurring payments
- [ ] MCP server (stdio) wrapping tool registry — per [AGENTIC_AI_DESIGN.md](AGENTIC_AI_DESIGN.md) Phase 7

---

## Planned configuration

Add to `config/.env.example` when implementing (not active today):

```bash
# Classification engine: legacy (LLM) | hybrid (classifier + LLM fallback)
CLASSIFICATION_ENGINE=legacy

# Classifier artifacts (gitignored)
CLASSIFIER_BASE_MODEL_PATH=data/models/base
CLASSIFIER_USER_MODEL_PATH=data/models/user

# Confidence thresholds (hybrid mode)
CLASSIFIER_ACCEPT_THRESHOLD=0.90
CLASSIFIER_REVIEW_THRESHOLD=0.70

# Teacher for distillation (opt-in; cloud sends PII)
TEACHER_PROVIDER=ollama
TEACHER_MODEL=qwen2.5:32b-instruct

# Benchmark / A/B
# FINANCE_DB_PATH=data/finance_ti.db
```

Existing roles unchanged: `PIPELINE_MODEL`, `CHAT_MODEL`, `CLASSIFICATION_AUDIT_MODEL`, `LEARNING_AGENT_MODEL` until Phase 5 convergence.

---

## Dataset format (incremental retraining)

JSONL under `data/training/datasets/<dataset_id>/`:

```json
{
  "schema_version": 1,
  "dataset_id": "ds-2026-07-13",
  "row_id": "tx_abc123",
  "features": {
    "merchant_key": "Merchant A",
    "source_category": "Category X",
    "account_name": "Checking",
    "amount_sign": "debit",
    "normalized_text": "merchant a"
  },
  "labels": {
    "ai_category": "...",
    "ai_sub_category": "...",
    "flow_type": "Expense",
    "expense_type": "Variable",
    "budget_tier": "Want"
  },
  "provenance": {
    "source": "user_confirmed",
    "decision_event_id": 42,
    "human_verified": true
  }
}
```

`manifest.json` per dataset: row counts, label distribution, parent dataset for incremental diffs.

---

## Constraints (Cursor agents)

From `.cursor/rules/no-data-logic-in-code.mdc`:

- No hardcoded merchant names or category taxonomies in Python/JS.
- Layer 1 regex and Layer 2 rules live in **DB tables** or user custom rules — not `if "brand" in description`.
- HPLLM prompts use vocabulary from DB (`classification_vocabulary.py`), not fixed category lists.
- Structural enums only: `flow_type`, `expense_type`, `classification`, `budget_tier`.

---

## Key files (implementation map)

| Concern | Path |
|---------|------|
| Pipeline orchestration | `webapp/pipeline/run.py` |
| LLM classification (legacy) | `webapp/llm/classify.py` |
| Lookup / rules | `webapp/processing/lookups.py`, `webapp/processing/custom_rules.py` |
| Save + confidence today | `webapp/adapters/dataframe_store.py` |
| Corrections log | `webapp/services/decision_events.py` |
| Learning Agent (retire Phase 5) | `webapp/services/learning_agent.py` |
| Classification audit (retire Phase 5) | `webapp/services/classification_audit.py` |
| Chat tools | `webapp/agent/tools.py`, `webapp/agent/chat.py` |
| DB schema | `webapp/db/schema.py` |
| Config / DB path | `webapp/config.py` (`FINANCE_DB_PATH`) |
| **New** classifier | `webapp/ml/` (to create) |

---

## Risks

| Risk | Mitigation |
|------|------------|
| Classifier only +1% better | Offline benchmark gate; abort or narrow scope |
| Small gold set (human-verified subset) | HPLLM distillation + rule promotion; expect review-heavy early months |
| High-confidence wrong labels | Keep Classification Audit until error rate proven low |
| Cloud teacher PII | Opt-in; document in Settings; local teacher default |
| Re-import overwrites history | `classification_events` append-only before claiming full audit trail |

---

## Maintenance

- Update this doc when a phase ships or benchmark bars change.
- Mark [ROADMAP.md](ROADMAP.md) §9 checkboxes when work completes.
- Do not implement pipeline hooks until **Phase 0 go** is recorded in this file.
