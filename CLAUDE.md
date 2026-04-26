# CLAUDE.md — hackathon-template

You are Claude Code working inside a **hackathon-template** repo. This file is your entry point. Read it once, then act.

## What this template is

A reusable agent harness for hackathons. Goal: enable a coding agent (you) to ship a working, demo-ready product **autonomously** under tight time pressure, with enough guardrails to prevent slop.

It is **not** a stack. Backend/frontend/styling/deploy are picked at hackathon T+0 from `recipes/` (docs only) — keeps the template stack-agnostic.

## Components (what lives where)

- `CLAUDE.md` — this file. Harness rules + pointers.
- `harness/PRINCIPLES.md` — coding principles. Read on first use of session, then again before any architecture decision.
- `README.md` — human-facing quickstart + hackathon-day runbook.
- `.claude/settings.json` — hooks (PostToolUse fmt/lint), permission allowlist.
- `.mcp.json` — project MCPs: **playwright** (browser feedback loop), **remotion** (demo videos).
- `harness/skills-install.sh` — one-shot installer for human-mode skills (Pocock + Emil). **Do not run inside the Ralph loop.**
- `harness/ralph/` — autonomous loop kit. `ralph.sh <iterations>` runs the loop; `PROMPT.md` is its system prompt; `plans/` holds live state (`prd.json`, `progress.txt`, `human-backlog.txt`).
- `recipes/` — markdown-only stack picks (backend, frontend, e2e-typesafety, styling, motion, demo, deploy). Pick combos, follow install commands.
- `ci-templates/` — inert GitHub Actions YAML. Copy chosen ones into `.github/workflows/` once stack is decided.
- `docs/` — extended philosophy reference: deep-modules, ddd-glossary, failure-modes.

## Two operating modes

1. **Human-driven** (you + Florian, interactive): use installed skills (`/grill-me`, `/write-a-prd`, `/prd-to-issues`, `/tdd`, `/improve-codebase-architecture`, Emil's UI skill). Used to elicit specs, write PRDs, file GitHub issues with label `ralph`, then polish hero/demo manually.
2. **Autonomous (Ralph)**: `bash harness/ralph/ralph.sh <N>`. You run headlessly, consume issues labeled `ralph`, ship one feature per iteration, exit on `<promise>COMPLETE</promise>`. Skills are not invoked here — `harness/ralph/PROMPT.md` is self-contained.

## Coding principles (do not re-derive — these are decided)

- **Fail fast and hard**. No `try/except` swallowing. No `.get(k, default)` to mask missing data. Validate inputs at boundaries; assert preconditions; raise clearly. Bugs surface early.
- **Deep modules > shallow modules** (Ousterhout / Pocock). Prefer fewer files with rich interfaces hiding complexity over many files with narrow leaky interfaces. Future-you (limited context window) navigates deep modules better.
- **End-to-end type safety**. Pydantic v2 schemas → OpenAPI → orval → TanStack Query hooks. CI fails on drift between backend schema and frontend client.
- **TDD for non-trivial work**. Write the test first when the spec is clear. For UI polish / spikes, skip TDD.
- **DDD / ubiquitous language**. Names in code = names the user uses. Don't invent synonyms. See `docs/ddd-glossary.md` once one exists for the project.
- **No premature abstraction**. Three similar lines beats a wrong abstraction. Don't add config knobs for hypothetical needs.
- **No emojis in code/files** unless explicitly requested.

## Failure modes you must guard against

| Failure | Countermeasure |
|---|---|
| AI does not do what user wants | Requirements gathering: `/grill-me` → `/write-a-prd` → `/prd-to-issues` |
| AI is too verbose | Concise responses, sacrifice grammar for concision; no trailing summaries |
| Code does not work | Feedback loops: pyright + pytest + tsc + vitest + playwright MCP |
| Doing way too much at once | TDD in small steps; one feature per Ralph iteration; one feature per commit |
| AI loses bearings as codebase grows | Deep modules; ubiquitous language; `progress.txt` between iterations |

## How to set up this template (first run after clone)

1. Read this file + `harness/PRINCIPLES.md`.
2. Run `bash harness/skills-install.sh` once (human mode only).
3. Pick stack from `recipes/`: typically one each from backend/, frontend/, e2e-typesafety/, styling/, motion/, demo/, deploy/. Follow install commands.
4. Copy relevant `ci-templates/*.yml` into `.github/workflows/` once the stack is up.
5. Create GitHub issues for the work (label `ralph` for autonomous items).
6. `cp harness/ralph/prd.sample.json harness/ralph/plans/prd.json` (or let Ralph populate from `gh issue list`).
7. Start the loop: `bash harness/ralph/ralph.sh 20`.

## Hard rules for you (the agent)

- All Python execution goes through **`uv run`**. Never invoke `python`, `pytest`, `ruff`, `pyright` etc. directly — always `uv run <cmd>` to stay inside the project venv. Same for installs: `uv add` / `uv sync`, never bare `pip`.
- Never run `git push --force`, `git reset --hard` on shared branches, or `--no-verify` without explicit user approval.
- Never invent skill or recipe names not present in the repo.
- Never edit `progress.txt` or `human-backlog.txt` by deleting prior entries — only append.
- When unsure between two recipe options, **ask Florian once**, then commit to the choice for the session.
