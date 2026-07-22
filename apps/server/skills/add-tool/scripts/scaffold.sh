#!/usr/bin/env bash
set -euo pipefail

tool_name="${1:-}"

if [[ ! "$tool_name" =~ ^[a-z][a-z0-9_]*$ ]]; then
  echo "Usage: scaffold.sh <snake_case_tool_name>" >&2
  exit 1
fi

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target_dir="${ARDEN_DIR:-$HOME/.arden}/tools"
target="$target_dir/${tool_name}.py"

if [[ -e "$target" ]]; then
  echo "Error: $target already exists" >&2
  exit 1
fi

mkdir -p "$target_dir"
sed \
  -e "s/__TOOL_NAME__/${tool_name}/g" \
  -e "s/__DISPLAY_NAME__/${tool_name}/g" \
  "$skill_dir/assets/scaffold.py" > "$target"

echo "$target"
