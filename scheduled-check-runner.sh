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
# OBJECTIVE:   OBJECTIVES.md Tier 1 #2 (System Catches Own Errors) and #3b (Infrastructure Liveness):
#              every scheduled check's outcome reaches someone, and silence never means "clean",
#              "never ran" and "still running" at once.
# SUCCESS:     one heartbeat row per run; adverse/unknown reaches notify.sh or leaves a notify_failed
#              row; for a check enabled in the budget pilot every start row gets an end row, and a run
#              past its BUDGET gets a timeout row + one page while it is still running.
# FAILURE:     a run with no heartbeat row; an adverse status with neither a push nor notify_failed;
#              an enabled check's start row with no end/timeout after 4x its budget (the census below
#              pages it as `orphan`); a timeout row for a child that finished inside its budget.
# TRIGGERS:    date-driven: each crontab line that wraps a check (36 on 2026-09-26). Event-driven
#              inside a run: the budget breach and the orphan census fire when the fact is known, not
#              on a later sweep.
# CONSUMER:    the human, through notify.sh pages; heartbeat readers governed-outcomes-check.py
#              (outcome_check_not_running), anomaly-conversion-check.py (_ran_checks) and
#              projects/sage-hayward/bin/shv_heartbeat_watchdog.py; the runs ledger is read by this
#              file's own orphan census and, once built, by ~/bin/lib/hook_runs.py (Dart Zbyi2wvF957Q).
# SILENT-FAIL: exit 0 always, so every failure path writes a row instead: notify_failed, exit 64 on a
#              missing command, census_failed, budget_unavailable / lock_unavailable (the check still
#              runs), and a runner killed mid-run leaves a start row that the census pages.
# PRIOR-ART:   started/terminal pairing, https://healthchecks.io/docs/monitoring_cron_jobs/ (cited by
#              ~/bin/watched-run, which also supplied flock overlap prevention); hook-runner.sh start/end
#              rows (dev-env-config 5e37090); review record in ~/dev/share/session-9d54a5ac-artifacts/
#              machine-consumer-design-2026-09-25.md §5 and §13 (DEC-365, DEC-366).
# PROMOTION:   the alert path cannot block: a cron wrapper has no action to gate. The budget pilot is
#              record-only; it may kill its own child only after a new DEC citing 7 days of runs-ledger
#              rows with precision >= 0.9 and zero signals outside the child's group. The user decides.
# predicate-rung: occurrence — tests/scheduled-check-runner-budget.test.sh (a child past its budget
#              yields a timeout row while alive; mutant m1, the breach test removed, fails the suite)
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

# ── BUDGET PILOT (DEC-366, RECORD-ONLY). Design: ~/dev/share/session-9d54a5ac-artifacts/
# machine-consumer-design-2026-09-25.md §6 (a)-(g). WHY: a check that hangs (2026-09-25: the artifact
# sweep blocked 6 h opening a FIFO) wrote NO row, because the heartbeat below is written only after the
# child exits -- silence read as "clean" and "still running" identically. This block records whether a
# check reached a terminal state within the budget its own script declares, and pages ONCE with evidence
# if not. It sends NO signal to any process and keeps waiting for the child; a kill needs its own DEC.
# OPT-IN PER CHECK: active only when ~/.config/scheduled-check-runner/enabled lists this check's name
# (one per line; surrounding blanks and a CR are ignored) or ALL. Not listed = the unindented capture
# line below, byte-identical to the pre-pilot runner, and no runs-ledger row (both asserted by
# tests/scheduled-check-runner-budget.test.sh).
# SAME RESULT AS THE CAPTURE LINE, by construction and by test: the output goes through a PIPE and the
# run ends at pipe EOF, exactly as `$( )` does, so a grandchild that writes after the child exits is
# still read (and holds the run open), and a child that reopens /dev/stderr writes to the pipe. A first
# version wrote to a temp FILE and polled the child pid; the verifier review leg (2026-09-26) showed that
# turned adverse into ok three ways. The one designed change on this path: a run of this check that
# starts while its previous run still holds the check's lock is skipped (skipped-locked, no page). One
# cosmetic exception to byte-identity, on BOTH paths since this block exists: bash's own diagnostics
# (a missing or non-executable command, a NUL-byte warning) cite this file's line number, which moved.
# LEDGER ~/.metrics/scheduled-check-runs.jsonl, hook-runner vocabulary (ts_ms, inv, phase, check, pid =
# this runner). Per inv: start -> [timeout -> proposed] -> [verified] -> end -> [refused]. verified =
# still running at 4x budget, or the next run found the lock still held after the budget (a true hang);
# refused = it ended before either (a false positive: raise that BUDGET); the first closure wins, and
# since a run and its next tick can race to close the same inv, consumers take the EARLIEST by ts_ms.
# Terminal: end; skipped-locked (a non-run); orphan (written by the census for a start whose runner died
# without an end). notify_failed, lock_unavailable, budget_unavailable, rc_unavailable, capture_failed
# and census_failed are informational. The heartbeat keeps ONE row per EXECUTED run; enabled rows gain
# inv, dur_ms and budget_exceeded. The census reads the last 20000 ledger lines; pruning is left to the
# active-phase DEC.
# BUDGET: a `# BUDGET: <seconds>` line in the first 40 lines of the FIRST argv file that starts with
# '#!' (a data file or an ELF can never set it); 1..86400, else 600. Counted in poll TICKS, not wall
# time: sleep does not tick through a laptop suspend, so a suspend does not read as a hang, and the end
# row's `suspended` marks runs where the two clocks parted (5 s + 2 %; measured tick overhead ~3 ms).
_SCR_ON=0 _SCR_L=""
# -f, not just -r: a directory or FIFO at this path must leave every check on the pre-pilot path (a
# directory aborted the read loop under set -u, a FIFO blocked it -- verifier leg, round 2).
if [ -f "$HOME/.config/scheduled-check-runner/enabled" ] && [ -r "$HOME/.config/scheduled-check-runner/enabled" ]; then
    while IFS= read -r _SCR_L || [ -n "${_SCR_L:-}" ]; do
        # strip a CR, then trailing, then leading whitespace
        _SCR_L=${_SCR_L%$'\r'}; _SCR_L=${_SCR_L%"${_SCR_L##*[![:space:]]}"}; _SCR_L=${_SCR_L#"${_SCR_L%%[![:space:]]*}"}
        if [ "$_SCR_L" = "$NAME" ] || [ "$_SCR_L" = ALL ]; then _SCR_ON=1; break; fi
    done < "$HOME/.config/scheduled-check-runner/enabled"
fi
if [ "$_SCR_ON" -eq 1 ]; then
    _SCR_RUNS="$HOME/.metrics/scheduled-check-runs.jsonl"
    _SCR_LOCKS="$HOME/.metrics/scheduled-check-locks"
    _SCR_INV="${EPOCHREALTIME/[.,]/}-$$-$RANDOM"
    _SCR_DUR=0 _SCR_BREACHED=0 _SCR_PAGE="" _SCR_PSTART=0
    # this runner's start time (/proc stat field 22): pid + start time identify it; a reused pid does not
    { read -r _SCR_L < "/proc/$$/stat"; } 2>/dev/null && read -r -a _SCR_F <<< "${_SCR_L##*) }" && _SCR_PSTART=${_SCR_F[19]:-0}
    _now_ms() { local t=${EPOCHREALTIME/[.,]/}; printf '%s' "${t%???}"; }   # [.,]: radix follows locale
    _san() { local s=${1//[^A-Za-z0-9_.:+-]/_}; printf '%s' "${s:0:64}"; }   # JSON-safe by construction
    _row() { # phase inv check [extra fields]; every value is _san'd, an integer, or fixed text
        printf '{"ts_ms":%s,"inv":"%s","phase":"%s","check":"%s","pid":%d%s}\n' \
            "$(_now_ms)" "$(_san "$2")" "$1" "$(_san "$3")" "$$" "${4:+,$4}" >> "$_SCR_RUNS"
    }
    _closed() { # inv -> 0 when the ledger already holds its verified or refused row (first closure wins)
        grep -qE -- "\"inv\":\"$(_san "$1")\",\"phase\":\"(verified|refused)\"" "$_SCR_RUNS" 2>/dev/null
    }
    _budget() { # -> "<secs> <src>"; never eval'd, never scans past the first '#!' file
        local a l n=0 b re='^# BUDGET: ([0-9]{1,5})$'
        for a in "$@"; do
            [ -f "$a" ] && [ -r "$a" ] && [ "$(head -c 2 -- "$a" 2>/dev/null)" = '#!' ] || continue
            while IFS= read -r l && [ $((n += 1)) -le 40 ]; do
                [[ $l =~ $re ]] || continue
                b=$((10#${BASH_REMATCH[1]}))                 # 10#: a bare "09" is a fatal octal error
                if [ "$b" -ge 1 ] && [ "$b" -le 86400 ]; then echo "$b header"; else echo "600 invalid"; fi
                return
            done < <(head -c 16384 -- "$a" 2>/dev/null)
            break
        done
        echo "600 default"
    }
    _scr_tree() { # pid -> "<cpu ticks, pid + descendants>\t<pid:state:wchan:cpu;...>" (8 listed, 64 walked)
        local q=("$1") p l w k f cpu=0 n=0 ev=""
        while [ "${#q[@]}" -gt 0 ] && [ "$n" -lt 64 ]; do
            p=${q[0]}; q=("${q[@]:1}"); n=$((n + 1))
            { read -r l < "/proc/$p/stat"; } 2>/dev/null || continue   # vanished mid-walk: skip it
            read -r -a f <<< "${l##*) }"                                # comm may contain ') '
            cpu=$(( cpu + ${f[11]:-0} + ${f[12]:-0} ))
            if [ "$n" -le 8 ]; then
                w=""; { read -r w < "/proc/$p/wchan"; } 2>/dev/null     # no trailing newline: rc 1, w set
                ev+="${ev:+;}$p:$(_san "${f[0]:-gone}"):$(_san "${w:-0}"):$(( ${f[11]:-0} + ${f[12]:-0} ))"
            fi
            for k in $(ps -o pid= --ppid "$p" 2>/dev/null); do q+=("$k"); done
        done
        printf '%s\t%s\n' "$cpu" "$ev"
    }
    _scr_child() { # the evidence root, re-read on every call: the capture group while it lives; once it has
        # exited, a process still holding the capture pipe (a daemonised grandchild); else the capture tail
        local p=""; { read -r p < "$_SCR_DIR/pid"; } 2>/dev/null
        if [[ $p =~ ^[0-9]+$ ]] && kill -0 "$p" 2>/dev/null; then _SCR_CHILD=$p; return; fi
        p=$(python3 -c '
import os, sys
tail = sys.argv[1]
try:
    target = os.readlink("/proc/%s/fd/0" % tail)          # "pipe:[inode]", shared by both ends
except OSError:
    sys.exit(0)
for p in sorted((d for d in os.listdir("/proc") if d.isdigit()), key=int):
    try:
        if p == tail or open("/proc/%s/stat" % p).read().rsplit(")", 1)[1].split()[1] == tail:
            continue                                        # the tail and its own cat hold the READ end
        if any(os.readlink("/proc/%s/fd/%s" % (p, f)) == target for f in os.listdir("/proc/%s/fd" % p)):
            print(p); break
    except OSError:
        pass' "$_SCR_TAIL" 2>/dev/null)
        [[ $p =~ ^[0-9]+$ ]] && _SCR_CHILD=$p || _SCR_CHILD=$_SCR_TAIL
    }
    _scr_breach() { # still running at BUDGET ticks: record, propose, page once; signal nothing
        local c1 ev prog=no
        _scr_child
        IFS=$'\t' read -r c1 ev < <(_scr_tree "$_SCR_CHILD")
        [ "${c1:-0}" -gt "${_SCR_C0:-0}" ] && prog=yes
        _SCR_BREACHED=1
        _row timeout "$_SCR_INV" "$NAME" "\"evidence_pid\":$_SCR_CHILD,\"budget_s\":$_SCR_B,\"ticks\":$_SCR_T,\"progressing\":\"$prog\",\"evidence\":\"$ev\""
        _row proposed "$_SCR_INV" "$NAME" "\"evidence_pid\":$_SCR_CHILD,\"evidence\":\"would signal pid $_SCR_CHILD at budget; record-only pilot, no signal sent\""
        # In the background: a slow notifier must not stall the tick clock (a stall reads as a suspend).
        ( "$HOME/bin/notify.sh" "SCHEDULED CHECK: $NAME is over budget" \
              "pid=$_SCR_CHILD past ${_SCR_B}s (budget_src=$_SCR_SRC), progressing=$prog, tree=$ev. NOT signalled (record-only pilot). Read $LOG" \
              --priority high --channel auto >/dev/null 2>&1 \
          || _row notify_failed "$_SCR_INV" "$NAME" '"evidence":"over-budget page not delivered"' ) {_SCR_FD}>&- &
        _SCR_PAGE=$!
    }
    _scr_run() { # sets OUT and RC exactly as the capture line would
        _SCR_T0=$(_now_ms)
        _row start "$_SCR_INV" "$NAME" "\"exit\":null,\"budget_s\":$_SCR_B,\"budget_src\":\"$_SCR_SRC\",\"pstart\":$_SCR_PSTART"
        # The group writes its pid (the evidence root), undoes the SIGINT/SIGQUIT ignore bash may give an
        # asynchronous command (belt-and-braces: bash 5.2 does not add it for a pipeline, so no test can fail
        # on that word), runs the check with cron's stdin and its stderr into the pipe, and hands the rc back
        # in a file (`wait` on a background pipeline reports the tail's status). The group's OWN stderr is
        # /dev/null, so bash's job-status lines ("Killed", "Terminated") never reach OUT: the capture line has
        # no such parent. The tail stores the output; if it cannot (a full /tmp) a second cat DRAINS the
        # pipe, so the check is never killed by SIGPIPE, and the run is reported unknown, never ok. Neither
        # side keeps the lock fd, so a leftover grandchild or an orphaned tail cannot hold this check's lock.
        { echo "$BASHPID" > "$_SCR_DIR/pid"; trap - INT QUIT; "$@" < /dev/null 2>&1; echo "$?" > "$_SCR_DIR/rc"; } \
            {_SCR_FD}>&- 2>/dev/null \
          | { cat > "$_SCR_DIR/out" || { echo "$?" > "$_SCR_DIR/catrc"; cat > /dev/null; }; } {_SCR_FD}>&- &
        _SCR_TAIL=$! _SCR_CHILD="" _SCR_T=0 _SCR_C0=0
        while kill -0 "$_SCR_TAIL" 2>/dev/null; do            # the tail lives until the pipe's last writer closes
            if [ "$_SCR_T" -eq $((_SCR_B - 1)) ]; then _scr_child; IFS=$'\t' read -r _SCR_C0 _ < <(_scr_tree "$_SCR_CHILD"); fi
            if [ "$_SCR_T" -eq "$_SCR_B" ]; then _scr_breach; fi
            if [ "$_SCR_BREACHED" -eq 1 ] && [ "$_SCR_T" -eq $((4 * _SCR_B)) ] && ! _closed "$_SCR_INV"; then
                _row verified "$_SCR_INV" "$NAME" "\"evidence_pid\":$_SCR_CHILD,\"ticks\":$_SCR_T,\"evidence\":\"still running at 4x budget: a true hang\""
            fi
            if [ "$_SCR_T" -eq 0 ]; then   # the first tick in tenths: a fast check does not pay a whole second
                for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 0.1; kill -0 "$_SCR_TAIL" 2>/dev/null || break; done
            else
                sleep 1
            fi
            _SCR_T=$((_SCR_T + 1))
        done
        wait "$_SCR_TAIL"
        local catrc=0; { read -r catrc < "$_SCR_DIR/catrc"; } 2>/dev/null
        RC=""; { read -r RC < "$_SCR_DIR/rc"; } 2>/dev/null
        OUT=$(<"$_SCR_DIR/out")
        exec {_SCR_FD}>&-                                     # release this check's lock
        [[ $RC =~ ^[0-9]+$ ]] || { _row rc_unavailable "$_SCR_INV" "$NAME" '"evidence":"the capture group left no rc: mapped to 70 (unknown)"'; RC=70; }
        if [ "$catrc" != 0 ]; then   # an incomplete capture can hide the marker: never let it read as ok
            [[ $catrc =~ ^[0-9]+$ ]] || catrc=1
            _row capture_failed "$_SCR_INV" "$NAME" "\"check_exit\":$RC,\"cat_exit\":$catrc,\"evidence\":\"captured output incomplete (full /tmp?): reported as rc 70, unknown\""
            RC=70
        fi
        _SCR_DUR=$(( $(_now_ms) - _SCR_T0 )); local wall=$(( _SCR_DUR / 1000 ))
        _row end "$_SCR_INV" "$NAME" "\"exit\":$RC,\"dur_ms\":$_SCR_DUR,\"ticks\":$_SCR_T,\"suspended\":$(( wall - _SCR_T > 5 + wall / 50 ))"
        if [ "$_SCR_BREACHED" -eq 1 ] && ! _closed "$_SCR_INV"; then
            _row refused "$_SCR_INV" "$NAME" "\"evidence\":\"ended after $_SCR_T ticks, before 4x budget ($((4 * _SCR_B))): a false positive, raise this check's BUDGET\""
        fi
    }
    _scr_census() { # §6 (f): the consumer for "started, never ended" -- one page per orphaned start row
        local fd inv chk age
        if ! { exec {fd}>>"$_SCR_LOCKS/.census"; } 2>/dev/null; then
            _row census_failed "$_SCR_INV" "$NAME" '"evidence":"census lock not openable: census skipped"'; return 0
        fi
        if flock -n "$fd"; then
            while IFS=$'\t' read -r inv chk age; do
                case "$inv" in
                    '') continue ;;
                    '!'*) _row census_failed "$_SCR_INV" "$NAME" "\"evidence\":\"$(_san "${chk:-python3 unavailable}")\""; continue ;;
                esac
                [[ $age =~ ^[0-9]+$ ]] || age=0
                if "$HOME/bin/notify.sh" "SCHEDULED CHECK: $(_san "$chk") started, never ended" \
                        "inv=$(_san "$inv") started ${age}s ago, past 4x its budget, with no end row and its runner gone. Read $_SCR_RUNS" \
                        --priority high --channel auto >/dev/null 2>&1 {fd}>&-; then
                    _row orphan "$inv" "$chk" "\"age_s\":$age"      # also the dedupe marker for this inv
                else
                    _row notify_failed "$inv" "$chk" '"evidence":"orphan page not delivered; retried next run"'
                fi
            done < <(python3 -c "$_SCR_CENSUS_PY" "$_SCR_RUNS" 2>/dev/null || printf '!\tpython3 failed\n')
        fi
        exec {fd}>&-
    }
    read -r -d '' _SCR_CENSUS_PY <<'PY' || :
import collections, json, sys, time
now = time.time() * 1000
starts, closed = {}, set()
try:
    with open(sys.argv[1], errors="replace") as fh:
        tail = collections.deque(fh, maxlen=20000)
except OSError as e:
    print("!\t%s" % type(e).__name__); sys.exit(0)
for line in tail:
    try:
        r = json.loads(line); inv, ph = str(r["inv"]), r["phase"]
    except (ValueError, KeyError, TypeError):
        continue
    if ph == "start":
        starts[inv] = r
    elif ph in ("end", "skipped-locked", "orphan"):   # timeout is NOT terminal: the child ran on
        closed.add(inv)
for inv, r in starts.items():
    try:
        age = (now - float(r["ts_ms"])) / 1000
        budget = float(r.get("budget_s") or 600)
    except (ValueError, KeyError, TypeError):
        continue
    if inv in closed or age <= 4 * budget:
        continue
    try:   # its runner still alive -- same pid AND same start time -- is late (a suspend, say), not dead
        with open("/proc/%d/stat" % int(r.get("pid", 0))) as fh:
            if fh.read().rsplit(")", 1)[1].split()[19] == str(r.get("pstart")):
                continue
    except (OSError, ValueError, TypeError, IndexError):
        pass
    print("%s\t%s\t%d" % (inv, r.get("check", "?"), age))
PY
    mkdir -p "$_SCR_LOCKS" 2>/dev/null
    read -r _SCR_B _SCR_SRC < <(_budget "$@")
    _SCR_B=${_SCR_B:-600} _SCR_SRC=${_SCR_SRC:-default}
    if ! _SCR_DIR=$(mktemp -d "${TMPDIR:-/tmp}/scheduled-check.XXXXXXXX" 2>/dev/null); then
        _row budget_unavailable "$_SCR_INV" "$NAME" '"evidence":"mktemp failed: ran unbudgeted, as before the pilot"'
        _SCR_ON=0
    else
        # The captured output is the check's own output: never leave it in /tmp, even if this runner is
        # stopped (subshells do not inherit these traps; the check keeps its default TERM/HUP).
        trap 'rm -f -- "$_SCR_DIR/out" "$_SCR_DIR/rc" "$_SCR_DIR/pid" "$_SCR_DIR/catrc"; rmdir -- "$_SCR_DIR" 2>/dev/null' EXIT
        trap 'exit 143' TERM; trap 'exit 129' HUP
        _SCR_LOCKFILE="$_SCR_LOCKS/$(_san "$NAME")"
        if { exec {_SCR_FD}>>"$_SCR_LOCKFILE"; } 2>/dev/null; then
            if ! flock -n "$_SCR_FD"; then
                _SCR_HOLDER=""; { read -r _SCR_HOLDER < "$_SCR_LOCKFILE"; } 2>/dev/null
                _row skipped-locked "$_SCR_INV" "$NAME" "\"holder\":\"$(_san "${_SCR_HOLDER:-unknown}")\""
                if [ -n "$_SCR_HOLDER" ] && ! _closed "$_SCR_HOLDER" \
                   && grep -qF -- "\"inv\":\"$(_san "$_SCR_HOLDER")\",\"phase\":\"proposed\"" "$_SCR_RUNS" 2>/dev/null; then
                    _row verified "$_SCR_HOLDER" "$NAME" '"evidence":"the next run found this run still holding the lock after its budget: a true hang"'
                fi
                exit 0   # the previous run is still going; its own rows carry the signal. No page.
            fi
            printf '%s\n' "$_SCR_INV" > "$_SCR_LOCKFILE" 2>/dev/null
        else
            exec {_SCR_FD}</dev/null   # keeps the `{_SCR_FD}>&-` redirections valid
            _row lock_unavailable "$_SCR_INV" "$NAME" '"evidence":"lock file not writable: ran without the overlap guard"'
        fi
    fi
fi

if [ "$_SCR_ON" -eq 1 ]; then _scr_run "$@"; else
OUT="$("$@" 2>&1)"; RC=$?
fi
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

if [ "$_SCR_ON" -eq 1 ]; then   # same row, three ADDITIVE keys so it joins the runs ledger by inv
    printf '{"ts":"%s","check":"%s","rc":%d,"marker_hit":%d,"status":"%s","inv":"%s","dur_ms":%d,"budget_exceeded":%d}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NAME" "$RC" "$FOUND" "$STATUS" "$_SCR_INV" "$_SCR_DUR" "$_SCR_BREACHED" >> "$HEARTBEAT"
else
printf '{"ts":"%s","check":"%s","rc":%d,"marker_hit":%d,"status":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$NAME" "$RC" "$FOUND" "$STATUS" >> "$HEARTBEAT"
fi

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

# budget pilot tail: the background over-budget page is waited only HERE, after the heartbeat and the alert
# above (a TERM during that wait must not cost the finished run its row), then the orphan census (§6 (f)).
[ "$_SCR_ON" -eq 1 ] && { [ -z "$_SCR_PAGE" ] || wait "$_SCR_PAGE"; _scr_census; }
exit 0   # never fail the cron itself; the heartbeat and the notification carry the signal
