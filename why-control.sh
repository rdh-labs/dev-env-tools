#!/usr/bin/env bash
# WHY does this control behave the way it does? Follow the citations already in its source.
#
# WHY THIS EXISTS. On 2026-08-13 three concurrent sessions spent ~20 hours diagnosing four
# governance controls as broken. All four had a documented decision behind them, and each session
# invented a different cause. The rationale was never missing: `evidence_gate.py` carries
# governance references on 1,112 lines and `DECISIONS-LOG.md` holds 243 entries. **The links
# existed. Nothing traversed them.** The citation was two lines above the code we were reading.
#
# So this adds nothing and stores nothing. It DERIVES: pull the DEC-/IDEA-/ISSUE-/L- ids and Dart
# task ids out of the region of source that mentions a control, then resolve each against the
# governance logs. Everything it reads is state it does not write -- the one property that held
# 5-for-5 in that session while every added mechanism needed repair.
#
# Usage: why-control.sh <CONTROL>  [<source-file>]
#        why-control.sh A72
#        why-control.sh outcome_qc_not_run ~/dev/infrastructure/tools/governed-outcomes-check.py
set -uo pipefail

CTL="${1:?usage: why-control.sh <CONTROL> [<source-file>]}"
SRC="${2:-}"
DOCS="$HOME/dev/infrastructure/dev-env-docs"

# Default hunting ground: the stop-hook stack, where control decisions live.
if [ -z "$SRC" ]; then
    # MOST mentions, not the first alphabetically. The first version took `head -1` and selected
    # `session_authorship.py` for A72 -- a file that merely names it -- while the decision lives in
    # `evidence_gate.py`. A file that mentions a control once is not where its rationale is.
    SRC="$(grep -RcF --include='*.py' --exclude-dir=__pycache__ -- "$CTL" "$HOME/.claude/hooks" 2>/dev/null \
           | awk -F: '$2>0{print $2"\t"$1}' | sort -rn | head -1 | cut -f2)"
    # NOTE: --include/--exclude-dir are load-bearing: the first run of this tool selected a
    # .pyc from __pycache__ and reported "control not found", which reads as absence.
    # -R not -r. The .py files under ~/.claude/hooks are SYMLINKS and `grep -r` does not
    # follow them, so -r returns 0 hits and reads as "not deployed". Measured twice on 2026-08-13.
fi
[ -n "$SRC" ] && [ -f "$SRC" ] || { printf 'no source found mentioning %s\n' "$CTL" >&2; exit 2; }

printf '=== %s  (source: %s)\n\n' "$CTL" "${SRC/#$HOME/\~}"

# Lines mentioning the control, plus surrounding context - the decision is usually in the comment
# directly above the code, which is exactly where nobody looked.
CTX="$(grep -nF -B6 -A2 -- "$CTL" "$SRC" 2>/dev/null)"
[ -n "$CTX" ] || { printf 'control not found in source\n' >&2; exit 2; }

printf -- '--- STATED RATIONALE (comment lines near %s) ---\n' "$CTL"
printf '%s\n' "$CTX" | grep -E '^\s*[0-9]+[-:]\s*#' | sed 's/^[0-9]*[-:]//' | head -14

IDS="$(printf '%s\n' "$CTX" | grep -oE '\b(DEC|IDEA|ISSUE|L)-[0-9]+\b' | sort -u)"
DART="$(printf '%s\n' "$CTX" | grep -oE '\bDart [A-Za-z0-9]{12}\b' | sort -u)"
DOCREFS="$(printf '%s\n' "$CTX" | grep -oE '[a-z0-9-]+\.md\b' | sort -u)"

printf -- '\n--- CITED GOVERNANCE IDS, RESOLVED ---\n'
if [ -z "$IDS$DART$DOCREFS" ]; then
    printf '  none cited near this control -- rationale may be undocumented (that is a finding)\n'
fi
for id in $IDS; do
    hit="$(grep -rhm1 -- "$id" "$DOCS"/*.md 2>/dev/null | head -1 | cut -c1-96)"
    printf '  %-12s %s\n' "$id" "${hit:-(no entry found in governance logs)}"
done
for d in $DART; do printf '  %-12s %s\n' "Dart" "${d#Dart }"; done
for f in $DOCREFS; do
    p="$(find "$DOCS" "$HOME/dev/share" -maxdepth 3 -name "$f" 2>/dev/null | head -1)"
    [ -n "$p" ] && printf '  %-12s %s\n' "decision-file" "${p/#$HOME/\~}"
done

printf -- '\n--- READ THESE BEFORE CONCLUDING THE CONTROL IS BROKEN ---\n'
printf '  A documented decision is not a defect. On 2026-08-13, four controls were diagnosed as\n'
printf '  broken by three sessions; all four were behaving as decided, and the decisions were\n'
printf '  in the lines above.\n'
