#!/bin/bash
# Installs human-mode skills (Pocock + Emil) into ./.claude/skills/ (project-local).
# Default behavior of `npx skills add` is project-local when run from a dir with .claude/.
# Use `-g` if you ever want user-global instead.
# Do NOT run from inside the Ralph loop.
set -e

# Ensure project-local target exists so the CLI doesn't fall back to global.
mkdir -p .claude/skills

echo "Installing Pocock skills..."
npx skills@latest add mattpocock/skills/grill-me
npx skills@latest add mattpocock/skills/write-a-prd
npx skills@latest add mattpocock/skills/prd-to-issues
npx skills@latest add mattpocock/skills/tdd
npx skills@latest add mattpocock/skills/improve-codebase-architecture

echo "Installing Emil Kowalski UI/animation skill..."
npx skills@latest add emilkowalski/skill

echo ""
echo "Verifying install location..."
if [ -d ".claude/skills" ] && [ "$(ls -A .claude/skills 2>/dev/null)" ]; then
  echo "✓ Skills installed project-local at: .claude/skills/"
  ls .claude/skills/
else
  echo "⚠ .claude/skills/ is empty — skills may have been written elsewhere."
  echo "  Known issue: some versions of the skills CLI write to ~/.agents/skills/"
  echo "  Check with:  ls ~/.agents/skills/  and  ls ~/.claude/skills/"
  exit 1
fi

echo ""
echo "Available slash commands in human mode:"
echo "  /grill-me                       - elicit problem statement"
echo "  /write-a-prd                    - draft PRD from elicitation"
echo "  /prd-to-issues                  - file GitHub issues from PRD"
echo "  /tdd                            - test-first workflow"
echo "  /improve-codebase-architecture  - deep-modules refactor"
echo "  (Emil's skill applies to UI / animation / design work)"
