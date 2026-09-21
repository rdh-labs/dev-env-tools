#!/usr/bin/env bash
# Measured-By wrapper for session-artifact-sweep.py --self-check (the commit-msg hook re-executes only a
# BARE allowlisted bash command under a tests/ dir; python3 is not on the allowlist -- 6b55eef therefore
# carried its count in prose, which nothing re-derived). Prints the self-check's PASS count with --count,
# exits non-zero if any check failed. Scrubs git's hook env so the self-check's own git probes see the
# real tree (memory measured-by-suite-must-scrub-git-env).
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX
set -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$(python3 "$HERE/../session-artifact-sweep.py" --self-check 2>&1)"; RC=$?
LINE="$(printf '%s\n' "$OUT" | grep -E '^\s*\[(PASS|FAIL)/self-check\]' | tail -1)"
N="$(printf '%s' "$LINE" | grep -oE '[0-9]+/[0-9]+' | head -1 | cut -d/ -f1)"
if [ "$1" = "--count" ]; then printf '%s\n' "${N:-0}"; else printf '%s\n' "$OUT"; fi
[ "$RC" -eq 0 ] && [ -n "$N" ] && printf '%s' "$LINE" | grep -q "PASS/self-check"
