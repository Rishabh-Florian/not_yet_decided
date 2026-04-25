#!/bin/bash
# Installs human-mode skills (Pocock + Emil), project-local.
#
# The skills CLI now writes to `.agents/skills/` (shared across many agent
# harnesses). Claude Code reads from `.claude/skills/`, so after install we
# mirror each skill into `.claude/skills/<name>/`.
#
# Do NOT run from inside the Ralph loop.

set -u  # don't `set -e`: per-skill failures should not abort the whole run

mkdir -p .claude/skills .agents/skills

# Pocock skill ids changed: write-a-prd -> to-prd, prd-to-issues -> to-issues.
SKILLS=(
  "mattpocock/skills/grill-me"
  "mattpocock/skills/to-prd"
  "mattpocock/skills/to-issues"
  "mattpocock/skills/tdd"
  "mattpocock/skills/improve-codebase-architecture"
  "emilkowalski/skill"
)

failed=()
for src in "${SKILLS[@]}"; do
  echo ""
  echo "==> Installing $src"
  if ! npx -y skills@latest add "$src" -y; then
    echo "   ⚠ failed: $src"
    failed+=("$src")
  fi
done

# Mirror .agents/skills/<name>/ -> .claude/skills/<name>/ so Claude Code sees them.
echo ""
echo "Mirroring .agents/skills/* -> .claude/skills/* for Claude Code..."
shopt -s nullglob
for d in .agents/skills/*/; do
  name="$(basename "$d")"
  rm -rf ".claude/skills/$name"
  cp -R "$d" ".claude/skills/$name"
done
shopt -u nullglob

echo ""
echo "Verifying install..."
if [ -n "$(ls -A .claude/skills 2>/dev/null)" ]; then
  echo "✓ Skills present at .claude/skills/:"
  ls .claude/skills/
else
  echo "⚠ .claude/skills/ is empty."
  echo "  Check .agents/skills/ and ~/.agents/skills/ for where the CLI wrote."
  exit 1
fi

if [ "${#failed[@]}" -gt 0 ]; then
  echo ""
  echo "⚠ Some skills failed to install:"
  printf '   - %s\n' "${failed[@]}"
fi

echo ""
echo "Available slash commands in human mode (Claude Code):"
echo "  /grill-me                       - elicit problem statement"
echo "  /to-prd                         - draft PRD from elicitation (was write-a-prd)"
echo "  /to-issues                      - file GitHub issues from PRD (was prd-to-issues)"
echo "  /tdd                            - test-first workflow"
echo "  /improve-codebase-architecture  - deep-modules refactor"
echo "  (Emil's skill applies to UI / animation / design work)"
