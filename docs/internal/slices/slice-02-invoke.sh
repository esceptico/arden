#!/usr/bin/env bash
# Slice 02 — invoke codex exec headless.
# Prereq: tim has approved docs/internal/slices/slice-02-chat-connector.md (§1–§10).
# This script does NOT touch ~/.arden/memory.db. Tests run on temp DBs.
# Run from repo root.

set -euo pipefail

BRIEF="docs/internal/slices/slice-02-chat-connector.md"
SPEC="docs/internal/arden-memory-redesign-spec.md"

if [ ! -f "$BRIEF" ]; then
  echo "Missing $BRIEF" >&2
  exit 1
fi
if [ ! -f "$SPEC" ]; then
  echo "Missing $SPEC" >&2
  exit 1
fi

# Pre-flight: slice 1 schema tests still green?
echo "==== Pre-flight: slice 1 schema tests ===="
if ! pytest apps/server/tests/memory/test_slice01_schema.py -q 2>&1 | tail -5; then
  echo "Slice 1 schema tests are NOT green. Fix before firing slice 2." >&2
  exit 1
fi
echo ""

# Pre-flight: schema_version=31 in live DB?
echo "==== Pre-flight: schema_version in live DB ===="
SCHEMA_VER=$(sqlite3 ~/.arden/memory.db "SELECT value FROM meta WHERE key='schema_version';" 2>/dev/null || echo "")
if [ "$SCHEMA_VER" != "31" ]; then
  echo "Live DB schema_version='$SCHEMA_VER' (expected 31). Slice 1 not landed?" >&2
  exit 1
fi
EMBED_DIM=$(sqlite3 ~/.arden/memory.db "SELECT value FROM meta WHERE key='embedding_dim';" 2>/dev/null || echo "")
echo "schema_version=$SCHEMA_VER ✓"
echo "embedding_dim=$EMBED_DIM (connectors must match this)"
echo ""

# Extract the verbatim prompt block from §11 of the brief.
# Pattern: code fence after "## 11. Codex prompt", first ``` block.
PROMPT=$(awk '
  /^## 11\. Codex prompt/ {capture=1; next}
  capture && /^```$/ {if (started) exit; started=1; next}
  capture && started {print}
' "$BRIEF")

if [ -z "$PROMPT" ]; then
  echo "Could not extract §11 prompt from $BRIEF" >&2
  exit 1
fi

echo "==== Prompt extracted (first 15 lines) ===="
echo "$PROMPT" | head -15
echo "==== /preview ===="
echo ""
echo "Full prompt length: $(echo "$PROMPT" | wc -l) lines"
echo ""

# Background nohup (matches slice 1 winning pattern), foreground, or abort.
read -p "Fire codex exec [b]ackground / [f]oreground / [N]o ? " mode
case "$mode" in
  b|B)
    LOG="/tmp/slice-02-codex-$(date +%Y%m%d-%H%M%S).log"
    echo "Logging to $LOG"
    nohup codex exec \
      --sandbox workspace-write \
      --cd "$(pwd)" \
      "$PROMPT" > "$LOG" 2>&1 &
    echo "Started pid=$!"
    echo "Tail with: tail -f $LOG"
    ;;
  f|F)
    codex exec \
      --sandbox workspace-write \
      --cd "$(pwd)" \
      "$PROMPT"
    ;;
  *)
    echo "Aborted."
    exit 0
    ;;
esac
