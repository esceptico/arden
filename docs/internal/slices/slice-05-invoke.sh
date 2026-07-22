#!/usr/bin/env bash
# Slice 05 — invoke codex exec headless.
# Prereq: user has approved docs/internal/slices/slice-05-claim-layer.md (§1–§16).
# This script does NOT touch ~/.arden/memory.db. Tests run on temp DBs.
# Run from repo root.

set -euo pipefail

BRIEF="docs/internal/slices/slice-05-claim-layer.md"
SPEC="docs/internal/arden-memory-redesign-spec.md"

if [ ! -f "$BRIEF" ]; then
  echo "Missing $BRIEF" >&2
  exit 1
fi
if [ ! -f "$SPEC" ]; then
  echo "Missing $SPEC" >&2
  exit 1
fi

# Pre-flight: slice 4 pass-1 tests still green?
echo "==== Pre-flight: slice 4 pattern-finder tests ===="
if ! (cd apps/server && uv run pytest tests/memory/test_pattern_finder.py -q 2>&1 | tail -5); then
  echo "Slice 4 pattern-finder tests are NOT green. Fix before firing slice 5." >&2
  exit 1
fi
echo ""

# Pre-flight: full suite count baseline
echo "==== Pre-flight: full suite baseline ===="
(cd apps/server && uv run pytest tests/ -q 2>&1 | tail -3)
echo ""

# Pre-flight: schema_version in live DB still 31?
echo "==== Pre-flight: schema_version in live DB ===="
SCHEMA_VER=$(sqlite3 ~/.arden/memory.db "SELECT value FROM meta WHERE key='schema_version';" 2>/dev/null || echo "")
echo "schema_version=$SCHEMA_VER"
echo ""

# Pre-flight: confirm pass-1 prompt + pattern_finder.py exist
echo "==== Pre-flight: slice 4 artifacts present ===="
for f in apps/server/arden/memory/pattern_finder.py \
         apps/server/arden/memory/prompts/pass1.txt \
         apps/server/tests/memory/test_pattern_finder.py; do
  if [ ! -f "$f" ]; then
    echo "MISSING: $f — slice 4 prereq not satisfied" >&2
    exit 1
  fi
  echo "  $f ✓"
done
echo ""

# Pre-flight: confirm dead search_* wrappers still present (slice 5 will delete them)
echo "==== Pre-flight: dead search_* wrappers present (to be deleted in slice 5) ===="
DEAD_COUNT=$(grep -cE 'def search_(text|vector|entities|temporal)' apps/server/arden/memory/service.py || echo "0")
echo "Found $DEAD_COUNT dead wrapper definitions (expect 4)"
if [ "$DEAD_COUNT" != "4" ]; then
  echo "WARN: expected 4 dead wrappers, found $DEAD_COUNT — backlog §3A may already be partially addressed" >&2
fi
echo ""

# Extract the verbatim prompt block from §13 of the brief.
PROMPT=$(awk '
  /^## 13\. Codex prompt/ {capture=1; next}
  capture && /^```$/ {if (started) exit; started=1; next}
  capture && started {print}
' "$BRIEF")

if [ -z "$PROMPT" ]; then
  echo "Could not extract §13 prompt from $BRIEF" >&2
  exit 1
fi

echo "==== Prompt extracted (first 20 lines) ===="
echo "$PROMPT" | head -20
echo "==== /preview ===="
echo ""
echo "Full prompt length: $(echo "$PROMPT" | wc -l) lines"
echo ""

read -p "Fire codex exec [b]ackground / [f]oreground / [N]o ? " mode
case "$mode" in
  b|B)
    LOG="/tmp/slice-05-codex-$(date +%Y%m%d-%H%M%S).log"
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
