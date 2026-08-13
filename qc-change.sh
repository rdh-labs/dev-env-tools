#!/usr/bin/env bash
# Run the independent-review leg for a change, in ONE invocation.
#
# WHY THIS EXISTS, and it is not convenience. On 2026-08-12 I skipped the QC Map's code-change
# row twice in a session whose entire subject was QC failure, then analysed why. The answer was
# not forgetting: under Reason's GEMS taxonomy these were VIOLATIONS -- intentional, reasoned
# deviations -- and violations are driven by the cost differential between the compliant path
# and the deviation. `/ship`'s review gate requires a subagent; when a session policy forbids
# subagents, the documented fallback is a multi-step manual procedure. The compliant path cost
# several steps, the deviation cost nothing, and I deviated. Twice, with a fresh justification
# each time.
#
# A GATE DEMANDING THE EXPENSIVE PATH WOULD MAKE THIS WORSE -- it raises the cost of compliance,
# which is the pressure that produces the violation. The remedy for a violation class is to make
# the compliant path cheap. That is the whole design intent here: one command, no procedure.
#
# NEVER TRUNCATES SILENTLY. Passing `head -300` of a diff to a reviewer with no tools produces
# confident findings about "missing" code that was merely cut -- measured the same day: a CRITICAL
# finding was filed against a fix that was present, because the payload stopped before it. If the
# diff exceeds the budget this script says so loudly, in the payload AND on stderr, and records
# `truncated: true` so the ledger never reads as full coverage.
#
# Usage: qc-change.sh [--model gemini|codex] [-- <path>...]     (default: staged + unstaged)
set -uo pipefail

MODEL=gemini; PATHS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --model) MODEL="${2:?--model needs gemini|codex}"; shift 2 ;;
        --) shift; PATHS=("$@"); break ;;
        *) printf 'usage: %s [--model gemini|codex] [-- <path>...]\n' "$0" >&2; exit 2 ;;
    esac
done

LEDGER="$HOME/.metrics/qc-change.jsonl"
mkdir -p "$(dirname "$LEDGER")" 2>/dev/null || true
REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || { printf 'not a git repo\n' >&2; exit 2; }
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || echo none)"

if [ "${#PATHS[@]}" -eq 0 ]; then
    mapfile -t PATHS < <( { git diff --name-only --cached; git diff --name-only; } 2>/dev/null | sort -u )
fi
[ "${#PATHS[@]}" -gt 0 ] || { printf 'nothing to review\n' >&2; exit 2; }

DIFF="$( { git diff --cached -- "${PATHS[@]}"; git diff -- "${PATHS[@]}"; } 2>/dev/null )"

# NEW FILES ARE INVISIBLE TO `git diff`. An untracked file produces no diff at all, so the one
# thing most needing review -- brand new code -- would silently be reviewed as nothing. Found by
# running this script on itself the first time. Include untracked contents explicitly.
for p in "${PATHS[@]}"; do
    [ -f "$p" ] || continue
    git ls-files --error-unmatch -- "$p" >/dev/null 2>&1 && continue
    DIFF="$DIFF
=== NEW UNTRACKED FILE: $p (full contents, not a diff) ===
$(cat "$p")"
done
# Fall back to the last commit when the work is already committed -- the common case at session
# end, and the case where /ship's own fallback silently produced an empty payload.
if [ -z "${DIFF//[[:space:]]/}" ]; then
    DIFF="$(git show HEAD -- "${PATHS[@]}" 2>/dev/null)"
    SRC="HEAD"
else
    SRC="worktree"
fi
[ -n "${DIFF//[[:space:]]/}" ] || { printf 'empty diff for those paths -- nothing to review\n' >&2; exit 2; }

BUDGET=100000; TRUNC=false; BYTES=${#DIFF}
if [ "$BYTES" -gt "$BUDGET" ]; then
    TRUNC=true
    DIFF="${DIFF:0:$BUDGET}"
    printf 'WARNING: diff is %s bytes, budget %s. TRUNCATED -- findings about "missing" code are suspect.\n' \
        "$BYTES" "$BUDGET" >&2
fi

PROMPT_FILE="$(mktemp)"; trap 'rm -f "$PROMPT_FILE"' EXIT
{
    printf 'Review this change for correctness, security, debug artifacts and unrelated edits.\n\n'
    printf 'Files: %s\n' "${PATHS[*]}"
    printf 'Diff source: %s. Total diff bytes: %s. TRUNCATED: %s\n\n' "$SRC" "$BYTES" "$TRUNC"
    if [ "$TRUNC" = true ]; then
        printf '*** THIS PAYLOAD IS TRUNCATED. Code you cannot see is NOT missing code. Do not\n'
        printf '    file a finding that something is absent -- say "not shown to me". ***\n\n'
    fi
    printf '%s\n\n' "$DIFF"
    cat <<'EOG'
For each finding: quote the EXACT line from the diff, give file:line, and a severity of
CRITICAL / HIGH / MEDIUM / LOW. If you cannot quote the line, do not file the finding.

GROUNDING DECLARATION (emit BEFORE any finding):
You are a CLI process with NO TOOLS. You cannot read files, run commands, or search. Every
fact you have came from this prompt. Therefore:
1. State the coverage you were given and say plainly what you could NOT see.
2. Label each finding VERIFIED (quote it) / INFERRED / ASSUMPTION.
3. Where the real cause may be that context was withheld from you, say "not shown to me"
   rather than reporting a defect.
4. If this prompt framed one side, say so -- your output is that side's case, not a verdict.
EOG
} > "$PROMPT_FILE"

# gemini-ask / codex-ask are workspace wrappers on PATH (see ~/bin); they resolve the concrete
# model at call time via resolve-model, so this script never pins a model literal. Weight the
# result accordingly -- the default tier is a fast model, and a clean report from a no-tools
# reviewer is weak evidence of absence, not strong.
case "$MODEL" in
    gemini) CMD=(gemini-ask) ;;
    codex)  CMD=(codex-ask) ;;
    *) printf 'unknown model %s\n' "$MODEL" >&2; exit 2 ;;
esac

OUT="$("${CMD[@]}" "$(cat "$PROMPT_FILE")" 2>&1)"; RC=$?
printf '%s\n' "$OUT"

# LEDGER ROW. This is the join partner for outcome_qc_not_run, which lists code-change commits
# with no row here. The omission is derived from git -- state this script does not write -- so a
# skipped review cannot hide by simply not logging.
python3 - "$LEDGER" "$REPO" "$HEAD_SHA" "$MODEL" "$RC" "$TRUNC" "$BYTES" "${PATHS[@]}" <<'EOP'
import json, sys, datetime
led, repo, sha, model, rc, trunc, nbytes, *paths = sys.argv[1:]
row = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "repo": repo, "head": sha, "model": model, "rc": int(rc),
       "truncated": trunc == "true", "diff_bytes": int(nbytes), "paths": paths,
       "result": "reviewed" if int(rc) == 0 else "attempted"}
with open(led, "a") as f:
    f.write(json.dumps(row) + "\n")
EOP

[ "$RC" -eq 0 ] || printf '\nREVIEW DISPATCH FAILED (rc=%s) -- logged as attempted, NOT reviewed.\n' "$RC" >&2
exit "$RC"
