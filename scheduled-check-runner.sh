#!/usr/bin/env bash
# Runs a scheduled check and makes its outcome REACH SOMEONE.
#
# WHY THIS EXISTS. This workspace built ~15 detectors in one session with ZERO notification and
# ZERO remediation (ANOMALY-REGISTER rows 199-201). All seven scheduled entries `>>` to a log
# file that nothing reads. The operative test, from peer fd4a8afd: *when this fires at 3am with
# nobody watching, what happens?* Today's answer for every one of them: a line is appended to a
# file no-one reads. This is the missing half.
#
# WHY A WRAPPER AND NOT `|| notify` ON THE CRON LINE. Two of the seven checks run --report-only,
# which forces exit 0. A `||` on those is DECORATION -- it can never fire, and wiring it would be
# a silent no-op, the exact defect class this session catalogued. The exit code is the ADJACENT
# signal; the report is the thing. Same substitution as artifact_verified: ask the artifact.
#
# HEARTBEAT, and it matters more than it looks. Every run appends a row whether or not anything
# was found. Without it, SILENCE means "clean" and "never ran" identically -- and these seven
# entries have never fired once, so their silence has been unfalsifiable all session. With it, a
# MISSING heartbeat is itself detectable. "No silent failures" has to apply to the runner, not
# only to the checks it runs.
#
# Usage: scheduled-check-runner.sh <name> <logfile> <marker-regex|-> -- <cmd...>
set -uo pipefail

NAME="${1:?usage: <name> <logfile> <marker|-> -- <cmd...>}"
LOG="${2:?logfile}"
MARKER="${3:?marker regex or - }"
shift 3
[ "${1:-}" = "--" ] && shift
# NO COMMAND = NO CHECK. Without this, `"$@"` expands to nothing, the command substitution
# below runs nothing, yields rc=0, and this wrapper writes a GREEN heartbeat every run
# forever -- silence indistinguishable from health, which is the exact class this file's
# header says it exists to eliminate. A crontab line that loses its command past `--` was
# previously undetectable. 64 = EX_USAGE, and being outside {0,1} it also lands in `*)`.
[ "$#" -gt 0 ] || { echo "usage: <name> <logfile> <marker|-> -- <cmd...>" >&2; exit 64; }

HEARTBEAT="$HOME/.metrics/scheduled-check-heartbeat.jsonl"
mkdir -p "$(dirname "$LOG")" "$(dirname "$HEARTBEAT")" 2>/dev/null || true

OUT="$("$@" 2>&1)"; RC=$?
printf '%s\n' "$OUT" >> "$LOG"

# ARTIFACT branch: for a tool that always exits 0, ask its OUTPUT, not its code.
FOUND=0
# NO PIPE. `set -o pipefail` + `grep -q` is a documented false-negative: grep exits on first
# match, printf takes SIGPIPE, and the PIPELINE returns 1 even though the marker WAS found.
# Past the ~64KB pipe buffer this silently flips adverse -> ok, which is precisely the failure
# this wrapper exists to prevent. Confirmed on this file: a 2.6MB output with the marker on
# LINE 1 reported marker_hit=0, status=ok. My four original fixtures all used small outputs and
# passed. A here-string has no upstream writer to kill. (memory: pipefail-grep-q-sigpipe-false-negative)
if [ "$MARKER" != "-" ] && grep -qE "$MARKER" <<< "$OUT"; then
    FOUND=1
fi

# DEC-334 rc=3 = NOTHING-TO-ASSESS: the check RAN and correctly found nothing to measure
# (e.g. no live sessions). DEC-334 binds consumers to "1|2 = act, 0|3 = silent"; the `*)`
# arm below was exactly the forbidden `rc -ne 0` pattern it warned about. NOT collapsed to
# `ok`: DEC-326/334 hold that nothing-to-assess is not health, so `idle` stays a DISTINCT
# heartbeat status -- logged and queryable, just not alerted.
#
# ROOT CAUSE, CORRECTED 2026-08-26 after an independent review falsified the first account
# written here. This wrapper was created 2026-08-12 and PREDATES DEC-334 (2026-08-19) by
# seven days; it never consumed peer-messaging-health, so DEC-334's consumer audit correctly
# excluded it and the `*)` arm was CORRECT WHEN WRITTEN. What actually happened: three tools
# adopted DEC-334's split on 2026-08-19 (context-ceiling-watch, peer-comms-check,
# anomaly-ledger-report) and were wired into this pre-existing wrapper WITH NO CONTRACT CHECK
# AT THE WIRING STEP. The transferable rule is therefore NOT "greps miss generic wrappers" --
# a grep for a wrapped tool DOES return this runner's crontab line. It is:
#     RE-AUDIT CONSUMERS WHENEVER A TOOL ADOPTS AN ALREADY-EXISTING EXIT-CODE CONTRACT.
# An audit is an EVENT; the population keeps changing. Standing enforcement now exists:
# bin/tests/contract-norm-enforcer.py::dec334_producer_check, cron 17 7 * * *, both
# polarities self-controlled.
#
# NO LIVE COUNTS IN THIS COMMENT, deliberately. The first draft hardcoded "13 genuine
# CANNOT-ASSESS alerts"; the true figure was 14 before the commit even landed. A frozen
# historical count is fine, a live one rots. Measure: grep -c '"rc":2' on the heartbeat.
STATUS=ok
case "$RC" in
    0) [ "$FOUND" -eq 1 ] && STATUS=adverse ;;
    1) STATUS=adverse ;;
    # rc=3 honours the artifact channel exactly as rc=0 does. A check that exits 3 while
    # PRINTING its adverse marker is reporting a finding, and unconditional silence would
    # lose it -- a NEW signal-loss path the first version of this fix introduced.
    3) if [ "$FOUND" -eq 1 ]; then STATUS=adverse; else STATUS=idle; fi ;;
    *) STATUS=unknown ;;   # a check that could not RUN must shout. UNKNOWN is never a pass.
esac

printf '{"ts":"%s","check":"%s","rc":%d,"marker_hit":%d,"status":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NAME" "$RC" "$FOUND" "$STATUS" >> "$HEARTBEAT"

case "$STATUS" in
    adverse|unknown) NEEDS_ALERT=1 ;;
    *)               NEEDS_ALERT=0 ;;   # ok, idle -> silent (DEC-334: consumers act on 1|2 only)
esac

if [ "$NEEDS_ALERT" -eq 1 ]; then
    if ! "$HOME/bin/notify.sh" "SCHEDULED CHECK: $NAME is $STATUS" \
            "rc=$RC marker_hit=$FOUND. Read $LOG" --priority high --channel auto >/dev/null 2>&1
    then
        # The notifier failing silently would reproduce the very defect this wrapper exists to
        # fix, one layer up. Record it where the heartbeat consumer will see it.
        printf '{"ts":"%s","check":"%s","status":"notify_failed"}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NAME" >> "$HEARTBEAT"
        printf 'NOTIFY FAILED for %s\n' "$NAME" >> "$LOG"
    fi
fi

exit 0   # never fail the cron itself; the heartbeat and the notification carry the signal
