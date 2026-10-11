#!/usr/bin/env bash
# Runs the direct-mode commands, then a few natural-language prompts (if an API key is set).
# Run ./demo/make_demo.sh first. Run from anywhere.
set -u
DEMO=/tmp/agent10_demo
cd "$(dirname "$0")/.."
run() { echo; echo "\$ $*"; "$@"; }
agent() { python3 -m inode_symlink_agent "$@"; }

if [ ! -d "$DEMO" ]; then echo "Run demo/make_demo.sh first."; exit 1; fi

echo "=== PART 1: direct mode (no LLM) ==="
run agent scan "$DEMO"
run agent broken "$DEMO/desktop"
run agent loops "$DEMO/desktop"
run agent hardlinks "$DEMO"
run agent inodes "$DEMO"
run agent suspicious "$DEMO"
run agent inspect "$DEMO/desktop/old-project"
run agent inspect "$DEMO/projects/data.csv"
echo; echo "--- delete-broken without --yes in a non-interactive run changes nothing:"
if [ -t 0 ]; then echo "(interactive terminal: you will be asked y/N)"; fi
run agent delete-broken "$DEMO/desktop"

echo
echo "=== PART 2: natural language (needs AGENT_LLM_API_KEY) ==="
if [ -z "${AGENT_LLM_API_KEY:-}" ] && ! grep -qs '^AGENT_LLM_API_KEY=.' .env; then
  echo "SKIPPED: no API key set. Example (this terminal only):"
  echo "  export AGENT_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/"
  echo "  export AGENT_LLM_API_KEY=your-key"
  echo "  export AGENT_LLM_MODEL=gemini-2.5-flash"
  exit 0
fi
run agent "Audit the folder $DEMO and tell me what is wrong"
run agent "Which files in $DEMO/projects are hard links to each other?"
run agent "Are there any suspicious shortcuts in $DEMO?"
run agent "Find all broken shortcuts in $DEMO/desktop and delete them"
echo; echo "Done. Log of changes: ~/.local/state/inode_symlink_agent/actions.log"
