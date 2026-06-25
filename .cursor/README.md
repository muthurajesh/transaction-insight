# Cursor governance — Transaction Insight

How agent constraints are split so the project stays LLM-first and does not drift.

## Rules vs skills

| Layer | Path | When it applies | Your concerns |
|-------|------|-----------------|---------------|
| **Rules** | `.cursor/rules/*.mdc` | **Always** — every agent turn | (1) No data logic, (2) AI/LLM for judgment |
| **Skills** | `.cursor/skills/*/SKILL.md` | **On demand** — you type `/` or ask explicitly | (3) Pre-commit: tests, docs, ROADMAP |

### Rules (always on)

- `no-data-logic-in-code.mdc` — No merchant/category keyword rules in code; LLM + DB + user custom rules only.
- `minimal-diff.mdc` — Smallest correct change; no scope creep.
- `readme-on-significant-changes.mdc` — Tests, README, ROADMAP, env docs before commit or task done.
- `ironbee-devtools-use.mdc` — Verify UI/runtime changes in the browser when applicable.

### Skills (invoke when needed)

- `pre-commit-check` — Full checklist: grep → pytest → ROADMAP/README → draft commit message.

## Quick use

1. **Any coding session** — rules apply automatically; agents should refuse `if "taco bell" in desc` style shortcuts.
2. **Before commit** — say *pre commit check* or invoke `/pre-commit-check`.
3. **New chat** — `@docs/AI_SESSION_CONTEXT.md` for product context.
