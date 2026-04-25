@plans/prd.json @plans/progress.txt @plans/human-backlog.txt @../CLAUDE.md @../PRINCIPLES.md

You are running inside the Ralph autonomous loop. One iteration = one feature shipped.

0. **Init**: If `prd.json` is empty or contains mock entries, populate it from `gh issue list --label ralph --json number,title,body` (one PRD entry per issue). Skip if already populated.

1. **Context**: Read `progress.txt` for prior session state. Read `human-backlog.txt` to avoid items blocked on humans.

2. **Prioritize**: Pick the single highest-value incomplete item from `prd.json` (`passes: false`). Not necessarily first — judge by impact and dependency order.

3. **Plan**: Write a short implementation plan (3–7 steps). For non-trivial items, write the test first (TDD).

4. **Execute**: Implement step-by-step. Make small, focused commits along the way if useful.

5. **Verify** (must all pass before marking complete):
   - Backend: `uv run ruff check .`, `uv run pyright`, `uv run pytest -q`
   - Frontend: `npm run lint`, `npm run typecheck` (or `npx tsc --noEmit`), `npm run test` (or `npx vitest run`)
   - E2E (if UI changed): use the playwright MCP to drive the running app like a user
   - All Python commands MUST go through `uv run` — never invoke `python` / `pytest` / `ruff` / `pyright` directly
   - If a verifier isn't applicable to this stack, skip with a one-line note in `progress.txt`

6. **Update PRD**: Set `passes: true` on the completed item. If you discovered new sub-work, add it as new entries.

7. **Log progress**: Append to `progress.txt`: date, item completed, key decisions, next-up. Keep terse.

8. **Blockers**: If you hit a permissions / secrets / external-account wall, append the item to `human-backlog.txt` with full context, mark the PRD item with a `blocked: true` flag, and move to the next item — do not stall.

9. **Commit**: `git commit -m "feat: <description> (closes #<issue>)"`. One feature per commit. Reference the GitHub issue if applicable so it auto-closes.

10. **Exit**: If every PRD item has `passes: true` or `blocked: true`, output `<promise>COMPLETE</promise>` exactly, then stop.

## Hard rules

- Fail-fast: no `try/except` swallowing, no `.get(k, default)` to hide missing data. Crash early with clear errors.
- Deep modules: prefer fewer files with rich interfaces over many shallow ones (see PRINCIPLES.md).
- Never skip hooks (`--no-verify`) or bypass type/lint checks to "get it green".
- Never delete `progress.txt`, `human-backlog.txt`, or other people's commits.
