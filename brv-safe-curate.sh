#!/usr/bin/env bash
# brv-safe-curate.sh -- Symlink-aware ByteRover directory indexer
# Indexes subdirectories individually, skipping cross-boundary symlinks.
# Usage: brv-safe-curate.sh [base-dir] [--dry-run] [--max-depth N]
# Exit 0 = all indexed, 1 = partial (some errors), 2 = fatal error

set -euo pipefail

BASE_DIR="${1:-$HOME/dev}"
DRY_RUN=0
MAX_DEPTH=2

shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --max-depth) MAX_DEPTH="${2:?--max-depth requires a value}"; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! command -v brv &>/dev/null; then
  echo "FATAL: brv not found in PATH. Install: npm install -g byterover-cli" >&2
  exit 2
fi

# Resolve to absolute path
BASE_DIR="$(cd "$BASE_DIR" && pwd)"

indexed=0
skipped=0
errors=0
error_list=()

# Find directories up to MAX_DEPTH that are NOT symlinks and are not .brv/
# Process each one individually to avoid cross-boundary symlink issues.
while IFS= read -r dir; do
  # Skip .brv, hidden dirs (any path component starts with .), and Windows-path dirs
  rel="${dir#$BASE_DIR/}"
  if [[ "$dir" == *"/.brv"* ]] || [[ "$rel" == .* ]] || [[ "$rel" == */.* ]] || [[ "$dir" == *\\* ]] || [[ "$dir" == *:* ]]; then
    continue
  fi

  # Check if this directory contains any cross-boundary symlink (any depth)
  has_symlink=0
  while IFS= read -r link; do
    abs_target="$(readlink -f "$link" 2>/dev/null || true)"
    if [[ -n "$abs_target" ]] && [[ "$abs_target" != "$BASE_DIR"* ]]; then
      has_symlink=1
      break
    fi
  done < <(find -P "$dir" -type l 2>/dev/null || true)

  if [[ $has_symlink -eq 1 ]]; then
    echo "SKIP (cross-boundary symlink): $dir"
    ((skipped++)) || true
    continue
  fi

  rel_dir="${dir#$HOME/dev/}"
  echo "INDEX: $rel_dir"

  if [[ $DRY_RUN -eq 1 ]]; then
    ((indexed++)) || true
    continue
  fi

  if brv curate -d "$dir" --detach 2>/tmp/brv-safe-curate-err.txt; then
    ((indexed++)) || true
  else
    err_msg="$(cat /tmp/brv-safe-curate-err.txt 2>/dev/null || echo 'unknown error')"
    echo "  ERROR: $err_msg" >&2
    error_list+=("$rel_dir: $err_msg")
    ((errors++)) || true
  fi

done < <(find -P "$BASE_DIR" -maxdepth "$MAX_DEPTH" -mindepth 1 -not -type l -type d 2>/dev/null | sort)

echo ""
echo "=== brv-safe-curate summary ==="
echo "Indexed : $indexed"
echo "Skipped : $skipped (cross-boundary symlinks)"
echo "Errors  : $errors"

if [[ ${#error_list[@]} -gt 0 ]]; then
  echo ""
  echo "Errors:"
  for e in "${error_list[@]}"; do
    echo "  - $e"
  done
fi

if [[ $errors -gt 0 ]]; then
  exit 1
fi
exit 0
