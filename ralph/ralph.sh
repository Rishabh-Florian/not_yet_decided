#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$1" ]; then
  echo "Usage: $0 <iterations>"
  exit 1
fi

# --- LOAD PROMPT ---
if [ -f "$SCRIPT_DIR/PROMPT.md" ]; then
  PROMPT=$(cat "$SCRIPT_DIR/PROMPT.md")
else
  echo "Error: PROMPT.md not found."
  exit 1
fi

# --- RALPH LOOP ---
for ((i=1; i<=$1; i++)); do
  echo ""
  echo -e "\033[1;35m══════════════════════════════════════════════════\033[0m"
  echo -e "\033[1;35m  ITERATION $i / $1\033[0m"
  echo -e "\033[1;35m══════════════════════════════════════════════════\033[0m"
  echo ""

  outfile="/tmp/claude_iter_$i.out"
  > "$outfile"

  # Stream and parse with python (reliable JSON + streaming)
  claude --verbose --output-format stream-json --permission-mode acceptEdits -p "$PROMPT" 2>&1 | while IFS= read -r line; do
    # Save raw JSON
    printf '%s\n' "$line" >> "$outfile"

    # Parse with python via stdin
    printf '%s' "$line" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    t = d.get('type', '')
    if t == 'system' and d.get('subtype') == 'init':
        print('\033[36m[SESSION START]\033[0m', flush=True)
        model = d.get('model', 'unknown')
        print(f'\033[1;36mModel: {model}\033[0m', flush=True)
    elif t == 'assistant':
        for c in d.get('message', {}).get('content', []):
            if c.get('type') == 'text' and c.get('text'):
                print(f\"\033[32m{c['text']}\033[0m\", flush=True)
            elif c.get('type') == 'tool_use':
                name = c.get('name', '')
                inp = c.get('input', {})
                if name == 'Bash':
                    cmd = str(inp.get('command', ''))[:80]
                    print(f'\033[33m→ Bash:\033[0m {cmd}', flush=True)
                elif name in ('Write', 'Edit', 'Read'):
                    fp = inp.get('file_path', '')
                    print(f'\033[33m→ {name}:\033[0m {fp}', flush=True)
                else:
                    print(f'\033[33m→ {name}\033[0m', flush=True)
    elif t == 'result':
        cost = d.get('total_cost_usd', 0)
        print(f'\033[36m[DONE] Cost: \${cost}\033[0m', flush=True)
except:
    pass
" 2>/dev/null
  done

  if grep -q "<promise>COMPLETE</promise>" "$outfile" 2>/dev/null; then
    echo ""
    echo -e "\033[1;32m✓ PRD complete after $i iterations.\033[0m"
    exit 0
  fi
done

echo ""
echo -e "\033[1;33m⚠ Reached max iterations ($1). PRD may be incomplete.\033[0m"
