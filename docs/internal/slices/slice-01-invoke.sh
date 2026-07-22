#!/usr/bin/env bash
# Slice 01 — invoke codex exec headless.
# Prereq: tim has approved docs/internal/slices/slice-01-schema.md.
# This script does NOT touch ~/.arden/memory.db. Tests run on temp DBs.
# Run from repo root.

set -euo pipefail

BRIEF="docs/internal/slices/slice-01-schema.md"
SPEC="docs/internal/arden-memory-redesign-spec.md"

if [ ! -f "$BRIEF" ]; then
  echo "Missing $BRIEF" >&2
  exit 1
fi
if [ ! -f "$SPEC" ]; then
  echo "Missing $SPEC" >&2
  exit 1
fi

# Extract the verbatim prompt block from §8 of the brief.
# Pattern: code fence after "## 8. Codex prompt", first ``` block.
PROMPT=$(awk '
  /^## 8\. Codex prompt/ {capture=1; next}
  capture && /^```$/ {if (started) exit; started=1; next}
  capture && started {print}
' "$BRIEF")

if [ -z "$PROMPT" ]; then
  echo "Could not extract §8 prompt from $BRIEF" >&2
  exit 1
fi

echo "==== Prompt extracted (first 10 lines) ===="
echo "$PROMPT" | head -10
echo "==== /preview ===="
echo ""
read -p "Fire codex exec? [y/N] " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
  echo "Aborted."
  exit 0
fi

codex exec \
  --sandbox workspace-write \
  --cd "$(pwd)" \
  "$PROMPT"
