#!/usr/bin/env bash
# Removes the demo folder (only ever /tmp/agent10_demo).
set -eu
DEMO=/tmp/agent10_demo
if [ -e "$DEMO" ] || [ -L "$DEMO" ]; then
  chmod -R u+rwX "$DEMO" 2>/dev/null || true
  rm -rf -- "$DEMO"
  echo "Removed $DEMO"
else
  echo "Nothing to clean: $DEMO does not exist"
fi
