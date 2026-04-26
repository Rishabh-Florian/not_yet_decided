# PRINCIPLES.md

Coding principles for this template. Apply in **every** mode (human-driven and autonomous Ralph). These are not suggestions.

## 1. Fail fast and hard

Embrace fail-fast programming. Validate assumptions immediately. Crash hard and early when wrong.

**Forbidden**
- `try / except Exception:` blocks that swallow errors
- `dict.get(key, default)` to mask missing required data
- Continuing after an unexpected error "just in case"
- Silent fallbacks (`if not x: x = something_else`) for required data

**Required**
- Validate inputs at boundaries: `if not valid(data): raise ValueError(...)`
- Assert preconditions on internal functions
- Pydantic models at every API boundary — no untyped dicts crossing process lines
- Explicit `raise` with context: `raise RuntimeError(f"expected X, got {y}")`

Why: bugs found at the source cost minutes; bugs found three layers downstream cost hours. Hackathons have no hours to spare.

## 2. Deep modules > shallow modules

(Ousterhout, A Philosophy of Software Design; reinforced by Matt Pocock.)

A **deep module** has a small interface and large hidden complexity. A **shallow module** exposes most of its complexity through its interface.

Prefer:
- One `auth/` module with `login()`, `logout()`, `current_user()` over four files: `cookie_parser.py`, `jwt_decoder.py`, `session_lookup.py`, `permissions_check.py` exposed at top level.
- One TanStack Query hook `useProject(id)` that internally orchestrates fetch + cache + revalidation, over four hooks the caller has to compose.

Why: the agent (you) has limited context. Wide shallow architectures force you to read many files to do one thing — and you will miss one. Deep modules let you reason about a feature by reading one file's interface.

When tempted to split: ask "would the caller benefit from seeing the inside?" If no, keep it together.

## 3. End-to-end type safety

Backend pydantic v2 schemas are the single source of truth. Frontend types are **generated**, never hand-written.

Flow: `Pydantic models → FastAPI → openapi.json → orval → TanStack Query hooks + TS types`.

CI gate (`ci-templates/types-sync.yml`): regenerate the frontend client and fail if the diff is non-empty. Schema drift = build red.

## 4. TDD for non-trivial work

When the spec is clear (e.g., a PRD entry), write the test first.

- Pure functions, API endpoints, data transforms → TDD always.
- UI animations, design polish, exploratory spikes → skip TDD, lean on visual feedback (playwright MCP).

Small steps: one test → one implementation → green → commit. Don't batch five tests.

## 5. Domain-driven design (ubiquitous language)

Names in code = names the user uses. If the user says "ticket", don't call it `Issue` in code. Maintain `harness/ddd-glossary.md` per project to keep terms aligned across PRD, code, UI copy, and commit messages.

## 6. No premature abstraction

Three similar lines is better than a premature abstraction. Don't:
- Add a config flag for a future need that isn't real yet
- Build a "framework" when one concrete implementation works
- Create base classes / generic types until ≥2 real concrete users exist

Refactor when the third concrete user shows up — never before.

## 7. No half-finished implementations

Don't leave `// TODO: implement properly` in shipped code. If you can't finish it now, raise `NotImplementedError` and add it to `harness/ralph/plans/human-backlog.txt`. Crashes are louder than TODOs.

## 8. Trust the framework

Don't add error handling, fallbacks, or validation for scenarios that can't happen given framework guarantees. Only validate at system boundaries (user input, external APIs, file IO). Internal calls trust internal calls.

## 9. Comments

Default to no comments. Add a comment only when the **why** is non-obvious: a hidden constraint, a workaround for a specific bug, behavior that would surprise a future reader. Never explain **what** — well-named identifiers do that.

## 10. Commit hygiene

- One feature per commit
- Reference GitHub issue: `feat: add user login (closes #14)`
- Sacrifice grammar for concision in commit messages
- Never `--no-verify`. If a hook fails, fix the underlying issue.
