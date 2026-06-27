---
name: pre-commit-check
description: >-
  Run the full pre-commit gate before commit or task completion — pytest, taxonomy
  grep, README/ROADMAP/docs sync, env example alignment, commit message draft.
  Use when the user says "pre commit check", "ready to commit", asks to commit,
  or wants docs/tests/roadmap verified before finishing.
disable-model-invocation: true
---

# Pre-commit check

Executable checklist for Transaction Insight. Structural rules (no data logic, minimal diff) are always on via `.cursor/rules/`; this skill runs the **verification procedure** before commit.

## 1. Inspect changes

```bash
git status
git diff
git diff --staged
```

List every changed file. Note whether the change is behavior, docs-only, or config.

## 2. No data logic (grep gate)

On changed `webapp/` and prompt files, confirm **no** new:

- Merchant or brand names in runtime logic/prompts (real names — placeholders OK)
- `if`/`match` on description text → category, tier, or expense type
- Hardcoded category lists, `ALLOWED_*` sets, keyword→label maps

If violations exist: remove and route judgment to LLM, DB lookup, or user custom rule. Say which path you used.

## 3. Tests

From repo root:

```bash
pytest -v --tb=short
```

- **Behavior changes:** add or update tests under `tests/` before commit.
- **Narrow fix:** `pytest tests/test_<area>.py -v --tb=short` then full suite.
- **Docs-only:** skip pytest only when there is zero runtime effect.
- Do not commit with failing tests. Report pass/fail counts in the reply.

### Common targeted suites

| Area touched | Suggested tests |
|--------------|-----------------|
| `webapp/llm/classify.py`, `prompts.py` | `test_classify_payload`, `test_classification_audit`, `test_descriptions_plausible` |
| Pipeline / processing | `test_custom_rules`, `test_taxonomy_rules` |
| Cadence | `test_expense_cadence`, `test_cadence_insights` |
| Chat / agent | `test_chat_llm_first`, `test_sql_intent` |

## 4. ROADMAP and linked docs

1. Read `docs/product/ROADMAP.md` and `docs/product/PRODUCT_CHARTER.md` if the change affects product direction.
2. If this work completes, advances, or defers an item: update `[x]` / `[~]` / `[ ]` and the linked detail doc.
3. Update `docs/product/AI_SESSION_CONTEXT.md` when shipped vs pending state changed.

## 5. README and env

- Scan `README.md` sections affected by the diff (workflow, Quick start, How AI is used, LLM setup, lookups, cadence, custom rules).
- New or renamed env vars → `config/.env.example` (and presets like `.env.lmstudio` if applicable).

## 6. Reply and commit message

Include in the reply:

- Test command run and result (pass/fail)
- Taxonomy grep: clean or what was fixed
- ROADMAP/README/doc updates (or "none needed" with reason)
- Files in the commit
- Draft commit message (1–2 sentences, **why** not just what)

**Do not run `git commit`** unless the user explicitly asked.
