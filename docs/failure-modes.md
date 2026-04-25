# Failure Modes & Countermeasures

Expanded version of the table in `CLAUDE.md`. Pattern: name the failure → name the cause → name the concrete countermeasure already shipped in this template.

## 1. AI does not do what the user wants

**Cause**: under-specified intent. The agent fills gaps with plausible-but-wrong assumptions.

**Countermeasure**:
- Human mode: `/grill-me` (Pocock skill) before any code. Forces explicit problem statement.
- `/write-a-prd` produces a written contract.
- `/prd-to-issues` files GitHub issues — each issue is a verifiable acceptance criterion.
- Ralph loop reads PRD entries directly, not free-form descriptions.

## 2. AI is too verbose

**Cause**: defaults to over-explanation, hedging, summary paragraphs.

**Countermeasure**:
- `~/.claude/CLAUDE.md` already enforces concision (sacrifice grammar).
- This template's `CLAUDE.md` reinforces: no trailing summaries, bullet points over prose where it fits.
- For commits: one-line subject, body only when truly needed.

## 3. Code does not work

**Cause**: no fast feedback loop — agent ships untested code that compiles but doesn't run correctly.

**Countermeasure** (layered):
- Static: ruff + pyright + tsc on every save (`.claude/settings.json` PostToolUse hook).
- Unit: pytest + vitest in CI (`ci-templates/`).
- Integration: contract-driven types (`recipes/e2e-typesafety/`) — schema drift = build red.
- Behavioral: playwright MCP — agent drives the running app like a user before declaring done.
- Ralph PROMPT.md step 5 makes verification mandatory before committing.

## 4. Doing way too much at once

**Cause**: agent tries to ship a whole feature set in one pass; review/diff becomes intractable.

**Countermeasure**:
- TDD: one test → one impl → green → commit.
- Ralph constraint: **one feature per iteration, one feature per commit**.
- PRD `steps[]` array breaks each item into sub-steps the agent can complete sequentially.

## 5. AI loses bearings as codebase grows

**Cause**: limited context window + shallow module structure → agent can't find the right file → re-implements logic that already exists, or modifies the wrong copy.

**Countermeasure**:
- **Deep modules** (PRINCIPLES.md §2). Fewer files, richer interfaces. Agent can fit the whole module in context.
- Ubiquitous language (`docs/ddd-glossary.md`) — searches succeed because there's only one name per concept.
- `progress.txt` and `human-backlog.txt` — persistent cross-iteration memory the agent reads at the start of every Ralph loop iteration.

## 6. Agent silently fails / swallows errors

**Cause**: defensive programming patterns (`try/except Exception`, `.get(k, default)`) hide real bugs until much later.

**Countermeasure**:
- Hard-coded in `~/.claude/CLAUDE.md`: forbidden patterns explicitly listed.
- Reinforced in PRINCIPLES.md §1.
- Code review (manual) flags any blanket `except` block.

## 7. Agent gets stuck on something that needs a human

**Cause**: missing credentials, account access, ambiguous spec, etc. Without an out, the agent burns iterations on the same blocker.

**Countermeasure**:
- `ralph/plans/human-backlog.txt` — append blocker, mark PRD item `blocked: true`, move to next item. Loop keeps moving; human reviews the backlog later.

## 8. Schema drift between backend and frontend

**Cause**: backend changes, frontend client not regenerated, runtime breaks in production.

**Countermeasure**:
- `ci-templates/types-sync.yml` regenerates orval client and fails on diff.
- Convention: never hand-edit `frontend/src/api/`.

## 9. CI passes but the demo still looks broken

**Cause**: tests cover code correctness, not feature/UI correctness.

**Countermeasure**:
- For UI: agent uses playwright MCP to actually open the app and verify visually.
- Manual: `recipes/demo_recipe.md` final-pass checklist run before recording.
- Don't claim "works" without browser verification — say "tests green, UI not verified" if you can't.
