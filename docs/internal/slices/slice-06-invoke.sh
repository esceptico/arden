#!/usr/bin/env bash
# Slice 06 — invoke codex exec headless.
# Prereq: slice 5 landed + user approved docs/internal/slices/slice-06-contradiction-watcher.md.
# Run from repo root.

set -euo pipefail

BRIEF="docs/internal/slices/slice-06-contradiction-watcher.md"
SPEC="docs/internal/arden-memory-redesign-spec.md"

if [ ! -f "$BRIEF" ] || [ ! -f "$SPEC" ]; then
  echo "Missing brief or spec" >&2
  exit 1
fi

# Pre-flight: slice 5 claim-layer tests green?
echo "==== Pre-flight: slice 5 claim-layer tests ===="
if ! (cd apps/server && uv run pytest tests/memory/test_claim_layer.py -q 2>&1 | tail -5); then
  echo "Slice 5 claim-layer tests NOT green. Fix before firing slice 6." >&2
  exit 1
fi
echo ""

echo "==== Pre-flight: full suite baseline ===="
(cd apps/server && uv run pytest tests/ -q 2>&1 | tail -3)
echo ""

# Extract verbatim prompt from §11 of slice-06 brief
PROMPT=$(awk '
  /^## 11\. Codex prompt/ {capture=1; next}
  capture && /^```$/ {if (started) exit; started=1; next}
  capture && started {print}
' "$BRIEF")

if [ -z "$PROMPT" ]; then
  echo "Could not extract §11 prompt from $BRIEF" >&2
  exit 1
fi

echo "==== Prompt extracted (first 20 lines) ===="
echo "$PROMPT" | head -20
echo "==== /preview ===="
echo "Full prompt length: $(echo "$PROMPT" | wc -l) lines"
echo ""

read -p "Fire codex exec [b]ackground / [f]oreground / [N]o ? " mode
case "$mode" in
  b|B)
    LOG="/tmp/slice-06-codex-$(date +%Y%m%d-%H%M%S).log"
    echo "Logging to $LOG"
    nohup codex exec --sandbox workspace-write --cd "$(pwd)" "$PROMPT" > "$LOG" 2>&1 &
    echo "Started pid=$!"
    echo "Tail: tail -f $LOG"
    ;;
  f|F)
    codex exec --sandbox workspace-write --cd "$(pwd)" "$PROMPT"
    ;;
  *)
    echo "Aborted."
    exit 0
    ;;
esac
