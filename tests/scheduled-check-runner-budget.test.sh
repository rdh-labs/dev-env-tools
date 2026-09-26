#!/usr/bin/env bash
# Contract + mutation suite for the scheduled-check-runner.sh BUDGET PILOT (DEC-366, record-only).
#
# OBJECTIVE:   OBJECTIVES.md Tier 1 #2 / #3b -- prove the pilot (a) changes nothing for a check that is
#              not enabled, (b) records start/timeout/proposed/end (+ closure) rows for an enabled check
#              that outlives its budget WITHOUT signalling it, and (c) can FAIL: fifteen mutants must each
#              fail a case here, and each mutation is shown applied before it is judged.
# SUCCESS:     the summary line reads `fail=0 mutants_caught=15/15`.
# FAILURE:     any FAIL line; a mutant reported SURVIVED; a row for a test check (names start `scrt-`)
#              found in the LIVE heartbeat or runs ledger after the suite.
# TRIGGERS:    event-driven: run by hand before landing any runner change (plan §9); no cron line.
# CONSUMER:    the person or agent landing a runner change reads the summary line; /ship cites it.
# SILENT-FAIL: exits 1 on any FAIL or surviving mutant; refuses to start if a fixture HOME equals the
#              real HOME; a case that cannot measure (missing tool) prints FAIL, never skips silently.
# PRIOR-ART:   ~/dev/infrastructure/tools/tests/scheduled-check-runner-exit-codes.test.sh (HOME redirect +
#              stub notify pattern, reused); Judge 3's mutation assertions in
#              ~/dev/share/session-9d54a5ac-artifacts/machine-consumer-design-2026-09-25.md §13.
# PROMOTION:   cannot block: a test file gates nothing by itself; /ship runs it before a runner commit.
#
# ISOLATION: every runner invocation uses HOME=<mktemp -d> with a recording bin/notify.sh stub, cwd = that
# HOME (as cron), and stdin /dev/null (as cron). Nothing here reads or writes ~/.metrics of the real HOME
# except the final read-only guard.
#
# Usage: bash tests/scheduled-check-runner-budget.test.sh
#        RUNNER=<file> BASE=<git ref of the pre-pilot runner> KEEP=1 (keep fixtures) are optional.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=${REPO:-$(dirname "$HERE")}
RUNNER=${RUNNER:-$REPO/scheduled-check-runner.sh}
REAL_HOME=$HOME
if [ -z "${BASE:-}" ]; then   # the commit before the one that introduced the pilot; HEAD while uncommitted
    intro=$(git -C "$REPO" log -S'BUDGET PILOT (DEC-366' --format=%H -- scheduled-check-runner.sh | tail -1)
    BASE=${intro:+$intro^}; BASE=${BASE:-HEAD}
fi
pass=0 fail=0 caught=0
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/scrt.XXXXXX") || exit 2   # every fixture lives under this one root
case "$ROOT" in "$REAL_HOME"|"$REAL_HOME/.metrics"*) echo "REFUSING suite root $ROOT" >&2; exit 2 ;; esac

ok()  { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }
ck()  { local d=$1; shift; if "$@"; then ok "$d"; else bad "$d"; fi; }
eq()  { [ "$1" = "$2" ] || { printf '        want [%s] got [%s]\n' "$2" "$1"; return 1; }; }

fixture() { # sets FX to a fresh HOME with a recording notify stub
    FX=$(mktemp -d "$ROOT/home.XXXXXX") || { echo "mktemp failed" >&2; exit 2; }
    case "$FX" in "$REAL_HOME"|"$REAL_HOME/.metrics"*) echo "REFUSING fixture HOME $FX" >&2; exit 2 ;; esac
    mkdir -p "$FX/bin" "$FX/.metrics" "$FX/.config/scheduled-check-runner" "$FX/tmp"
    cat > "$FX/bin/notify.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s || %s\n' "$1" "$2" >> "$HOME/notify.record"
ls -l /proc/$$/fd >> "$HOME/notify.fds" 2>/dev/null     # which fds the notifier inherited (lock leaks)
[ -f "$HOME/notify.fail" ] && exit 1
exit 0
EOF
    chmod +x "$FX/bin/notify.sh"
}
enable() { printf '%s\n' "$2" >> "$1/.config/scheduled-check-runner/enabled"; }
child() { # HOME name body [header-line] -> prints the path of an executable child script
    local f="$1/$2.sh"
    { echo '#!/usr/bin/env bash'; [ -n "${4:-}" ] && printf '%s\n' "$4"; printf '%s\n' "$3"; } > "$f"
    chmod +x "$f"; printf '%s' "$f"
}
run() { # HOME name marker cmd... (foreground)
    local h=$1 n=$2 m=$3; shift 3
    env --default-signal=PIPE -C "$h" HOME="$h" TMPDIR="${SCRT_TMPDIR:-$h/tmp}" bash "$RUNNER" "$n" "$h/$n.log" "$m" -- "$@" \
        </dev/null >/dev/null 2>&1
}
runbg() { # same, in the background; sets RP to the runner's own pid
    local h=$1 n=$2 m=$3; shift 3
    env --default-signal=PIPE -C "$h" HOME="$h" TMPDIR="$h/tmp" bash "$RUNNER" "$n" "$h/$n.log" "$m" -- "$@" \
        </dev/null >/dev/null 2>&1 &
    RP=$!
}
q() { # FILE EXPR -> python evaluates EXPR over `rows` (every line MUST parse as JSON)
    python3 - "$1" "$2" <<'PY'
import json, sys
rows = []
try:
    with open(sys.argv[1]) as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
except FileNotFoundError:
    pass
def n(phase, **kw):
    return sum(1 for r in rows if r.get("phase") == phase and all(r.get(k) == v for k, v in kw.items()))
def first(phase):
    return next((r for r in rows if r.get("phase") == phase), {})
print(eval(sys.argv[2]))
PY
}
qlax() { # FILE PHASE -> count of rows in PHASE among the lines that parse (for fixtures with a junk line)
    python3 -c "
import json, sys
n = 0
for l in open(sys.argv[1]):
    try: n += json.loads(l).get('phase') == sys.argv[2]
    except ValueError: pass
print(n)" "$1" "$2"
}
RUNS() { printf '%s' "$1/.metrics/scheduled-check-runs.jsonl"; }
HB()   { printf '%s' "$1/.metrics/scheduled-check-heartbeat.jsonl"; }
pages() { if [ -f "$1/notify.record" ]; then wc -l < "$1/notify.record" | tr -d ' '; else echo 0; fi; }
no_lock_fd_in_notify() { ! grep -q 'scheduled-check-locks' "$1/notify.fds" 2>/dev/null; }
tmp_empty() { [ -z "$(find "$1/tmp" -mindepth 1 2>/dev/null)" ]; }
pstart_of() { python3 -c "import sys; print(open('/proc/%s/stat' % sys.argv[1]).read().rsplit(')', 1)[1].split()[19])" "$1"; }
await_phase() { # HOME phase pid-to-watch: wait <=12 s for the phase row, stop early if the runner is gone
    local i
    for i in $(seq 1 60); do
        [ "$(q "$(RUNS "$1")" "n('$2')")" -ge 1 ] 2>/dev/null && return 0
        kill -0 "$3" 2>/dev/null || { [ "$(q "$(RUNS "$1")" "n('$2')")" -ge 1 ]; return; }
        sleep 0.2
    done
    return 1
}

# ───────────────────────────── cases (each returns 0 only if all its assertions hold) ─────────────────────
case_positive() { # BUDGET 2, child lives 5 s and exits 3 on its own: nothing is signalled
    fixture; local h=$FX c r rc=0
    enable "$h" scrt-pos; c=$(child "$h" pos 'echo pos-out; sleep 5; exit 3' '# BUDGET: 2'); r=$(RUNS "$h")
    runbg "$h" scrt-pos - "$c"
    await_phase "$h" timeout "$RP" || rc=1
    POS_CHILD=$(q "$r" "first('timeout').get('evidence_pid', 0)")
    if [ "$POS_CHILD" -gt 0 ] && kill -0 "$POS_CHILD" 2>/dev/null; then POS_ALIVE=1; else POS_ALIVE=0; fi
    POS_PGC=$(ps -o pgid= -p "$POS_CHILD" 2>/dev/null | tr -d ' '); POS_PGR=$(ps -o pgid= -p "$RP" 2>/dev/null | tr -d ' ')
    wait "$RP"
    eq "$POS_ALIVE" 1 || rc=1                                     # alive when the timeout row landed
    eq "$(q "$r" "[n(p) for p in ('start','timeout','proposed','end','refused','verified')]")" "[1, 1, 1, 1, 1, 0]" || rc=1
    eq "$(q "$r" "first('end').get('exit')")" 3 || rc=1           # its own rc: a kill would read 143/137
    eq "$(q "$r" "first('timeout')['ts_ms'] < first('end')['ts_ms']")" True || rc=1   # the end row is LATE
    eq "$(pages "$h")" 1 || rc=1                                  # the over-budget page only (rc 3 = idle)
    grep -q 'is over budget' "$h/notify.record" 2>/dev/null || rc=1
    grep -qx pos-out "$h/scrt-pos.log" || rc=1                    # the background page did not eat the capture
    no_lock_fd_in_notify "$h" || { echo "        notify.sh inherited the check's lock fd"; rc=1; }
    tmp_empty "$h" || rc=1
    eq "$(q "$(HB "$h")" "[(r['status'], r['budget_exceeded'], r['inv']) for r in rows]")" \
       "[('idle', 1, '$(q "$r" "first('start')['inv']")')]" || rc=1
    POS_H=$h
    return $rc
}
case_pgid() { # no setsid in the pilot (D9): the child shares the runner's group and does not lead one
    [ -n "${POS_CHILD:-}" ] && [ -n "${POS_PGC:-}" ] || return 1
    eq "$POS_PGC" "$POS_PGR" && [ "$POS_PGC" != "$POS_CHILD" ]
}
case_negative() { # fast child under BUDGET 5: no timeout row even after waiting past the budget; wall < 3 s
    fixture; local h=$FX c r t0 t1 rc=0
    enable "$h" scrt-neg; c=$(child "$h" neg 'echo fine; exit 0' '# BUDGET: 5'); r=$(RUNS "$h")
    t0=${EPOCHREALTIME/[.,]/}; run "$h" scrt-neg - "$c"; t1=${EPOCHREALTIME/[.,]/}
    NEG_WALL_MS=$(( (t1 - t0) / 1000 ))
    [ "$NEG_WALL_MS" -lt 3000 ] || { printf '        runner wall %s ms >= 3000\n' "$NEG_WALL_MS"; rc=1; }
    sleep 6                                                       # a stray late row would land by now
    eq "$(q "$r" "[n(p) for p in ('start','timeout','proposed','end')]")" "[1, 0, 0, 1]" || rc=1
    eq "$(pages "$h")" 0 || rc=1
    return $rc
}
case_verified() { # BUDGET 1, child lives 6 s: still alive at 4x budget -> `verified`, and no `refused`
    fixture; local h=$FX c r
    enable "$h" scrt-ver; c=$(child "$h" ver 'sleep 6; exit 0' '# BUDGET: 1'); r=$(RUNS "$h")
    run "$h" scrt-ver - "$c"
    eq "$(q "$r" "[n(p) for p in ('start','timeout','proposed','verified','end','refused')]")" "[1, 1, 1, 1, 1, 0]"
}
case_octal() { # `# BUDGET: 09` is parsed base-10 as 9 (a bare $((09)) is a fatal octal error)
    fixture; local h=$FX c rc=0
    enable "$h" scrt-oct; c=$(child "$h" oct 'exit 0' '# BUDGET: 09')
    run "$h" scrt-oct - "$c"
    eq "$(q "$(RUNS "$h")" "(first('start').get('budget_s'), first('start').get('budget_src'))")" "(9, 'header')" || rc=1
    eq "$(q "$(HB "$h")" "len(rows)")" 1 || rc=1
    return $rc
}
budget_of() { # header-line -> "(budget_s, 'budget_src')" read back from the start row
    fixture; local h=$FX c
    enable "$h" scrt-b; c=$(child "$h" b 'exit 0' "$1")
    run "$h" scrt-b - "$c"
    q "$(RUNS "$h")" "(first('start').get('budget_s'), first('start').get('budget_src'))"
}
case_ranges() {
    local rc=0
    eq "$(budget_of '# BUDGET: 0')"      "(600, 'invalid')" || rc=1
    eq "$(budget_of '# BUDGET: 99999')"  "(600, 'invalid')" || rc=1
    eq "$(budget_of '# BUDGET: 86400')"  "(86400, 'header')" || rc=1
    eq "$(budget_of '# no budget here')" "(600, 'default')" || rc=1
    eq "$(budget_of '#BUDGET: 7')"       "(600, 'default')" || rc=1   # the grammar is exact
    return $rc
}
case_datafile() { # only the FIRST '#!' argv file may set a budget; a data file never can
    fixture; local h=$FX first second rc=0
    enable "$h" ALL
    printf '# BUDGET: 7\necho data\n' > "$h/data.txt"                  # no '#!': a data file
    run "$h" scrt-data - bash "$h/data.txt"
    eq "$(q "$(RUNS "$h")" "(first('start').get('budget_s'), first('start').get('budget_src'))")" "(600, 'default')" || rc=1
    first=$(child "$h" first 'exit 0'); second=$(child "$h" second 'exit 0' '# BUDGET: 7')
    rm -f "$(RUNS "$h")"
    run "$h" scrt-2nd - "$first" "$second"                             # stop at the first '#!' file
    eq "$(q "$(RUNS "$h")" "(first('start').get('budget_s'), first('start').get('budget_src'))")" "(600, 'default')" || rc=1
    return $rc
}
case_fifo_e2e() { # plan §7 step 3 + §9: cron-like env (env -i: no inherited PATH/TMPDIR/CLAUDE_*; bash's
                  # compiled default PATH, no ~/bin), child blocked opening a FIFO. PATH is deliberately
                  # not assigned: destructive_op_gate's PATH_SHADOW rule fires on PATH= + any redirect +
                  # the `env` word in one command (measured 2026-09-26); cron's PATH is /usr/bin:/bin,
                  # bash's default adds /usr/local/* and sbin -- the property proved is "no ~/bin".
    fixture; local h=$FX c r w rc=0
    enable "$h" scrt-fifo; mkfifo "$h/pipe"; r=$(RUNS "$h")
    c=$(child "$h" fifo "cat < '$h/pipe' > /dev/null; exit 0" '# BUDGET: 2')
    env -i --default-signal=PIPE -C "$h" HOME="$h" /usr/bin/bash "$RUNNER" scrt-fifo "$h/scrt-fifo.log" - -- "$c" \
        </dev/null >/dev/null 2>&1 &
    RP=$!
    await_phase "$h" timeout "$RP" || rc=1
    FIFO_ALIVE=0; w=$(q "$r" "first('timeout').get('evidence_pid', 0)")   # never kill -0 0 (= the whole group)
    [ "$w" -gt 0 ] && kill -0 "$w" 2>/dev/null && FIFO_ALIVE=1
    exec {w}<>"$h/pipe"; exec {w}>&-                   # open as a writer (never blocks), then close: EOF
    wait "$RP"
    eq "$FIFO_ALIVE" 1 || rc=1
    q "$r" "first('timeout').get('evidence', '')" | grep -q 'wait_for_partner' || { echo "        no wait_for_partner in evidence"; rc=1; }
    eq "$(q "$r" "first('timeout').get('progressing')")" no || rc=1   # blocked in open(): no CPU between readings
    eq "$(q "$r" "[n(p) for p in ('start','timeout','proposed','end','refused','verified')]")" "[1, 1, 1, 1, 1, 0]" || rc=1
    eq "$(q "$r" "first('timeout')['ts_ms'] < first('end')['ts_ms']")" True || rc=1
    eq "$(q "$(HB "$h")" "[(r['status'], r['budget_exceeded']) for r in rows]")" "[('ok', 1)]" || rc=1
    eq "$(pages "$h")" 1 || rc=1
    FIFO_H=$h
    return $rc
}
case_boundary() { # child ends at ~BUDGET: whichever side it lands on, the accounting is consistent
    fixture; local h=$FX c r t
    enable "$h" scrt-bnd; c=$(child "$h" bnd 'sleep 3; exit 0' '# BUDGET: 3'); r=$(RUNS "$h")
    run "$h" scrt-bnd - "$c"
    t=$(q "$r" "n('timeout')")
    BND_T=$t
    if [ "$t" = 1 ]; then
        eq "$(q "$r" "[n(p) for p in ('start','proposed','end')] + [n('refused') + n('verified')]")" "[1, 1, 1, 1]"
    else
        eq "$(q "$r" "[n(p) for p in ('start','timeout','proposed','end','refused','verified')]")" "[1, 0, 0, 1, 0, 0]"
    fi
}
case_concurrency() { # two checks at once in one HOME: rows attributable by inv, no shared or leftover temp file
    fixture; local h=$FX a b pa pb rc=0
    enable "$h" ALL
    a=$(child "$h" ca 'sleep 1; echo OUT-A' '# BUDGET: 30'); b=$(child "$h" cb 'sleep 1; echo OUT-B' '# BUDGET: 30')
    runbg "$h" scrt-ca - "$a"; pa=$RP; runbg "$h" scrt-cb - "$b"; pb=$RP; wait "$pa" "$pb"
    eq "$(q "$(RUNS "$h")" "sorted(set((r['check'], r['inv']) for r in rows)) == sorted(set((r['check'], r['inv']) for r in rows if r['phase'] == 'start')) and len(set(r['inv'] for r in rows)) == 2")" True || rc=1
    eq "$(q "$(RUNS "$h")" "sorted((r['check'], r['phase']) for r in rows)")" "[('scrt-ca', 'end'), ('scrt-ca', 'start'), ('scrt-cb', 'end'), ('scrt-cb', 'start')]" || rc=1
    eq "$(python3 -c "
import json
hb = {json.loads(l)['check']: json.loads(l)['inv'] for l in open('$(HB "$h")')}
st = {json.loads(l)['check']: json.loads(l)['inv'] for l in open('$(RUNS "$h")') if json.loads(l)['phase'] == 'start'}
print(hb == st)")" True || rc=1
    grep -qx OUT-A "$h/scrt-ca.log" && grep -qx OUT-B "$h/scrt-cb.log" || rc=1
    tmp_empty "$h" || rc=1
    return $rc
}
case_progressing() { # a child burning CPU past its budget reads progressing=yes (the other polarity of the FIFO case)
    fixture; local h=$FX c
    enable "$h" scrt-cpu; c=$(child "$h" cpu 'e=$((SECONDS + 4)); while [ $SECONDS -lt $e ]; do :; done' '# BUDGET: 2')
    run "$h" scrt-cpu - "$c"
    eq "$(q "$(RUNS "$h")" "first('timeout').get('progressing')")" yes
}
case_grandchild() { # a grandchild that does NOT hold the output pipe: prompt end row, and it does not inherit the lock
    fixture; local h=$FX c t0 t1 rc=0
    enable "$h" scrt-gc; c=$(child "$h" gc 'sleep 4 >/dev/null 2>&1 & exit 0' '# BUDGET: 2')
    t0=${EPOCHREALTIME/[.,]/}; run "$h" scrt-gc - "$c"; t1=${EPOCHREALTIME/[.,]/}
    [ $(( (t1 - t0) / 1000 )) -lt 2000 ] || { printf '        wall %s ms\n' $(( (t1 - t0) / 1000 )); rc=1; }
    run "$h" scrt-gc - "$(child "$h" gc2 'exit 0')"                     # grandchild still alive now
    eq "$(q "$(RUNS "$h")" "[n(p) for p in ('start','end','timeout','skipped-locked')]")" "[2, 2, 0, 0]" || rc=1
    return $rc
}
case_notify_failed() { # the over-budget page fails -> a notify_failed row in the runs ledger
    fixture; local h=$FX c
    enable "$h" scrt-nf; touch "$h/notify.fail"; c=$(child "$h" nf 'sleep 3; exit 0' '# BUDGET: 1')
    run "$h" scrt-nf - "$c"
    eq "$(q "$(RUNS "$h")" "(n('timeout'), n('notify_failed'))")" "(1, 1)"
}
case_suspend() { # SIGSTOP the RUNNER 8 s: ticks freeze while the wall clock runs -> suspended=1, no false breach
    fixture; local h=$FX c r
    enable "$h" scrt-sus; c=$(child "$h" sus 'sleep 12; exit 0' '# BUDGET: 8'); r=$(RUNS "$h")
    runbg "$h" scrt-sus - "$c"
    sleep 1.5; kill -STOP "$RP"; sleep 8; kill -CONT "$RP"; wait "$RP"
    SUS=$(q "$r" "(n('timeout'), first('end').get('ticks'), first('end').get('dur_ms'), first('end').get('suspended'))")
    eq "$(q "$r" "(n('timeout'), first('end').get('suspended'))")" "(0, 1)"
}
case_suspend_control() { # same budget, no stop, child outlives it: the breach DOES fire, suspended=0
    fixture; local h=$FX c r
    enable "$h" scrt-susc; c=$(child "$h" susc 'sleep 10; exit 0' '# BUDGET: 8'); r=$(RUNS "$h")
    run "$h" scrt-susc - "$c"
    eq "$(q "$r" "(n('timeout'), first('end').get('suspended'))")" "(1, 0)"
}
case_lock() { # a second run while the first holds the lock: skipped-locked naming the holder, no page, no heartbeat
    fixture; local h=$FX a rc=0
    enable "$h" scrt-lk; a=$(child "$h" lk 'sleep 3; exit 0' '# BUDGET: 30')
    runbg "$h" scrt-lk - "$a"; await_phase "$h" start "$RP" || rc=1
    run "$h" scrt-lk - "$(child "$h" lk2 'exit 0')"
    wait "$RP"
    eq "$(q "$(RUNS "$h")" "(n('start'), n('end'), n('skipped-locked'), first('skipped-locked').get('holder') == first('start').get('inv'))")" "(1, 1, 1, True)" || rc=1
    eq "$(pages "$h")" 0 || rc=1
    eq "$(q "$(HB "$h")" "len(rows)")" 1 || rc=1
    tmp_empty "$h" || { echo "        the skipped run left its temp dir"; rc=1; }
    return $rc
}
case_next_tick_verified() { # a breached run still holding its lock when the next run starts -> verified; no refused later
    fixture; local h=$FX r rc=0
    enable "$h" scrt-nt; r=$(RUNS "$h")
    runbg "$h" scrt-nt - "$(child "$h" nt 'sleep 6; exit 0' '# BUDGET: 2')"
    await_phase "$h" proposed "$RP" || rc=1
    run "$h" scrt-nt - "$(child "$h" nt2 'exit 0')"                   # the "next scheduled tick"
    wait "$RP"
    eq "$(q "$r" "[n(p) for p in ('start','timeout','proposed','skipped-locked','verified','refused','end')]")" "[1, 1, 1, 1, 1, 0, 1]" || rc=1
    eq "$(q "$r" "first('verified')['inv'] == first('start')['inv']")" True || rc=1
    return $rc
}
case_signal_parity() { # the check sees the runner's own SIGINT/SIGQUIT dispositions on both paths, ignored on entry or not
    fixture; local h=$FX c rc=0 pre
    c=$(child "$h" sig "awk '/^SigIgn/{print \$2}' /proc/self/status")
    enable "$h" scrt-sig-on
    for pre in "" "--ignore-signal=INT,QUIT"; do
        rm -f "$h"/scrt-sig-*.log
        for n in scrt-sig-off scrt-sig-on; do
            env --default-signal=PIPE $pre -C "$h" HOME="$h" TMPDIR="$h/tmp" bash "$RUNNER" "$n" "$h/$n.log" - -- "$c" </dev/null >/dev/null 2>&1
        done
        SIGP+="[${pre:-default}: off=$(cat "$h/scrt-sig-off.log") on=$(cat "$h/scrt-sig-on.log")] "
        cmp -s "$h/scrt-sig-off.log" "$h/scrt-sig-on.log" || rc=1
    done
    return $rc
}
case_enabled_file_trim() { # a CR or surrounding blanks in the enabled file must not silently disable a check
    fixture; local h=$FX
    printf 'scrt-crlf\r\n  scrt-sp  \n' > "$h/.config/scheduled-check-runner/enabled"
    run "$h" scrt-crlf - "$(child "$h" t1 'exit 0')"; run "$h" scrt-sp - "$(child "$h" t2 'exit 0')"
    eq "$(q "$(RUNS "$h")" "sorted(r['check'] for r in rows if r['phase'] == 'start')")" "['scrt-crlf', 'scrt-sp']"
}
case_capture_failed() { # a full /tmp (proxy: ulimit -f 16 KiB) must not read as ok, and must not SIGPIPE the check
    fixture; local h=$FX c rc=0
    enable "$h" scrt-cap
    c=$(child "$h" cap 'head -c 40000 /dev/zero | tr "\0" x; echo; echo ADVERSE last-line; exit 0' '# BUDGET: 30')
    # --ignore-signal=XFSZ: cat then fails with EFBIG (as on a real full disk) instead of dying by SIGXFSZ,
    # whose core dump WSL's core_pattern (|/wsl-capture-crash) writes OUTSIDE the fixture (judge 3, 2026-09-26)
    ( ulimit -f 16; env --default-signal=PIPE --ignore-signal=XFSZ -C "$h" HOME="$h" TMPDIR="$h/tmp" bash "$RUNNER" scrt-cap /dev/null ADVERSE -- "$c" \
        </dev/null >/dev/null 2>&1 )                             # LOG=/dev/null: only the capture file is capped
    eq "$(q "$(RUNS "$h")" "(n('capture_failed'), first('capture_failed').get('check_exit'), first('end').get('exit'))")" "(1, 0, 70)" || rc=1
    eq "$(q "$(HB "$h")" "[r['status'] for r in rows]")" "['unknown']" || rc=1   # check_exit 0: drained, not SIGPIPEd
    eq "$(pages "$h")" 1 || rc=1
    return $rc
}
case_enabled_path_not_a_file() { # a directory or a FIFO at the enabled path leaves checks on the pre-pilot path
    local h rc=0 i w
    fixture; h=$FX; mkdir "$h/.config/scheduled-check-runner/enabled"
    run "$h" scrt-dir - "$(child "$h" d1 'exit 0')"
    eq "$(q "$(HB "$h")" "[sorted(r) for r in rows]")" "[['check', 'marker_hit', 'rc', 'status', 'ts']]" || rc=1
    fixture; h=$FX; mkfifo "$h/.config/scheduled-check-runner/enabled"
    runbg "$h" scrt-fcfg - "$(child "$h" d2 'exit 0')"
    for i in $(seq 1 25); do kill -0 "$RP" 2>/dev/null || break; sleep 0.2; done
    if kill -0 "$RP" 2>/dev/null; then                          # blocked reading the FIFO: release it, fail
        echo "        runner blocked on a FIFO at the enabled path"; exec {w}<>"$h/.config/scheduled-check-runner/enabled"; exec {w}>&-; rc=1
    fi
    wait "$RP"
    eq "$(q "$(HB "$h")" "len(rows)")" 1 || rc=1
    return $rc
}
case_rc_unavailable() { # the capture group dies before writing the rc (its child SIGKILLs it): rc 70, unknown, valid JSON
    fixture; local h=$FX rc=0
    enable "$h" scrt-rcu
    run "$h" scrt-rcu - "$(child "$h" rcu 'echo x; kill -KILL $PPID; exit 0' '# BUDGET: 30')"
    eq "$(q "$(RUNS "$h")" "(n('rc_unavailable'), first('end').get('exit'))")" "(1, 70)" || rc=1
    eq "$(q "$(HB "$h")" "[(r['rc'], r['status']) for r in rows]")" "[(70, 'unknown')]" || rc=1
    return $rc
}
case_runner_death() { # the runner is TERMed mid-run while a grandchild holds the pipe: temp dir gone, lock free
    fixture; local h=$FX rc=0
    enable "$h" scrt-die
    runbg "$h" scrt-die - "$(child "$h" die 'sleep 6 & exit 0' '# BUDGET: 30')"
    await_phase "$h" start "$RP" || rc=1
    sleep 1; kill -TERM "$RP"; wait "$RP"; DIE_RC=$?
    run "$h" scrt-die - "$(child "$h" die2 'exit 0')"            # the grandchild and the orphaned tail still live
    eq "$(q "$(RUNS "$h")" "(n('start'), n('skipped-locked'), n('end'))")" "(2, 0, 1)" || rc=1
    eq "$DIE_RC" 143 || rc=1
    tmp_empty "$h" || rc=1
    return $rc
}
case_lock_unavailable() { # the lock path cannot be opened: the check still runs, and the ledger says so
    fixture; local h=$FX
    enable "$h" scrt-lu; printf x > "$h/.metrics/scheduled-check-locks"   # a FILE where the dir should be
    run "$h" scrt-lu - "$(child "$h" lu 'exit 0' '# BUDGET: 5')"
    eq "$(q "$(RUNS "$h")" "(n('lock_unavailable'), n('start'), n('end'))")" "(1, 1, 1)" &&
    eq "$(q "$(HB "$h")" "[r['status'] for r in rows]")" "['ok']"
}
case_mktemp_fails() { # no temp file possible: budget_unavailable, then exactly the pre-pilot path (5-key row)
    fixture; local h=$FX
    enable "$h" scrt-mt
    SCRT_TMPDIR="$h/does-not-exist" run "$h" scrt-mt - "$(child "$h" mt 'echo x; exit 0')"
    eq "$(q "$(RUNS "$h")" "(n('budget_unavailable'), n('start'))")" "(1, 0)" &&
    eq "$(q "$(HB "$h")" "[sorted(r) for r in rows]")" "[['check', 'marker_hit', 'rc', 'status', 'ts']]"
}
case_adversarial() { # marker-shaped / $( ) / backtick output and a hostile BUDGET line: nothing executes
    fixture; local h=$FX rc=0
    enable "$h" scrt-adv
    cat > "$h/adv.sh" <<'EOF'
#!/usr/bin/env bash
# BUDGET: 1; touch PWNED_H
printf '%s\n' '$(touch PWNED1)' '`touch PWNED2`' 'ADVERSE "quoted" \back\slash ;touch PWNED3'
exit 0
EOF
    chmod +x "$h/adv.sh"
    run "$h" scrt-adv 'ADVERSE' "$h/adv.sh"
    eq "$(find "$h" -name 'PWNED*' | wc -l | tr -d ' ')" 0 || rc=1
    eq "$(q "$(RUNS "$h")" "(first('start').get('budget_s'), first('start').get('budget_src'), n('end'))")" "(600, 'default', 1)" || rc=1
    eq "$(q "$(HB "$h")" "[r['status'] for r in rows]")" "['adverse']" || rc=1
    grep -qF '$(touch PWNED1)' "$h/scrt-adv.log" || rc=1              # the text reached the log verbatim
    enable "$h" 'scrt-q"x'; run "$h" 'scrt-q"x' - "$(child "$h" qx 'exit 0')"
    eq "$(q "$(RUNS "$h")" "sorted(set(r['check'] for r in rows))")" "['scrt-adv', 'scrt-q_x']" || rc=1
    return $rc
}
case_stdin() { # a child that reads stdin sees EOF on both paths (cron gives EOF); enabled gives EOF even on a pipe
    fixture; local h=$FX c rc=0
    c=$(child "$h" in 'if read -r x; then echo "got=$x"; else echo EOF; fi')
    run "$h" scrt-in-off - "$c"; enable "$h" scrt-in-on; run "$h" scrt-in-on - "$c"
    eq "$(cat "$h/scrt-in-off.log")|$(cat "$h/scrt-in-on.log")" "EOF|EOF" || rc=1
    echo data | env --default-signal=PIPE -C "$h" HOME="$h" TMPDIR="$h/tmp" bash "$RUNNER" scrt-in-on "$h/pipe-on.log" - -- "$c" >/dev/null 2>&1
    echo data | env --default-signal=PIPE -C "$h" HOME="$h" TMPDIR="$h/tmp" bash "$RUNNER" scrt-in-off "$h/pipe-off.log" - -- "$c" >/dev/null 2>&1
    eq "$(cat "$h/pipe-on.log")" "EOF" || rc=1
    STDIN_DELTA="disabled path on a data pipe: $(cat "$h/pipe-off.log")"
    return $rc
}
case_differential() { # SAFETY: the same child, disabled vs enabled -> same status/rc/marker, same log bytes, same pages
    local i rc=0 hd he mk body
    # The last three are the verifier leg's counterexamples to the first (temp-file) design: output written by
    # a grandchild after the child exits; a child that SIGINTs itself; a child that reopens /dev/stderr.
    # Round 2 added self-TERM and self-KILL: a parent bash would print "Terminated"/"Killed" into OUT.
    # (No SIGSEGV child: a core dump would write outside the fixture.)
    local -a M=(- ADVERSE - - - ADVERSE - ADVERSE - ADVERSE - -)
    local -a B=("printf 'all fine\n'; exit 0"        "printf 'ADVERSE: thing\n'; exit 0"
                "printf 'degraded\n'; exit 1"        "printf 'cannot assess\n'; exit 2"
                "printf 'nothing to assess\n'; exit 3" "printf 'ADVERSE: thing\n'; exit 3"
                "printf 'command not found\n'; exit 127"
                "( sleep 2; echo ADVERSE late ) & exit 0"
                'kill -INT $$; echo survived-SIGINT'
                "echo ADVERSE first-line; echo second | tee /dev/stderr; echo third"
                'echo before; kill -TERM $$; echo after'
                'echo before; kill -KILL $$; echo after')
    for i in "${!B[@]}"; do
        mk=${M[$i]} body=${B[$i]}
        fixture; hd=$FX; fixture; he=$FX; enable "$he" scrt-diff
        for hh in "$hd" "$he"; do
            child "$hh" d "$body" '# BUDGET: 30' >/dev/null
            run "$hh" scrt-diff "$mk" "$hh/d.sh"
        done
        eq "$(q "$(HB "$hd")" "[(r['check'], r['rc'], r['marker_hit'], r['status']) for r in rows]")" \
           "$(q "$(HB "$he")" "[(r['check'], r['rc'], r['marker_hit'], r['status']) for r in rows]")" \
           || { echo "        child $i: $body"; rc=1; }
        cmp -s "$hd/scrt-diff.log" "$he/scrt-diff.log" || { echo "        log differs for child $i: $body"; rc=1; }
        eq "$(sed "s#$hd#HOME#g" "$hd/notify.record" 2>/dev/null)" \
           "$(sed "s#$he#HOME#g" "$he/notify.record" 2>/dev/null)" || rc=1   # page bodies name the log path
        eq "$(q "$(HB "$he")" "[sorted(set(r) - {'ts'}) for r in rows]")" \
           "[['budget_exceeded', 'check', 'dur_ms', 'inv', 'marker_hit', 'rc', 'status']]" || rc=1
    done
    return $rc
}
case_disabled_no_rows() { # not enabled (no file, or a file naming another check): no runs ledger, no lock dir
    local rc=0 h
    fixture; h=$FX; run "$h" scrt-off - "$(child "$h" off 'exit 0')"
    [ ! -e "$(RUNS "$h")" ] && [ ! -e "$h/.metrics/scheduled-check-locks" ] || rc=1
    eq "$(q "$(HB "$h")" "[sorted(r) for r in rows]")" "[['check', 'marker_hit', 'rc', 'status', 'ts']]" || rc=1
    fixture; h=$FX; enable "$h" some-other-check; run "$h" scrt-off - "$(child "$h" off 'exit 0')"
    [ ! -e "$(RUNS "$h")" ] || rc=1
    return $rc
}
case_exit_codes_suite() { # the existing 8-case contract suite, unchanged, against this runner
    local out; out=$(RUNNER="$RUNNER" bash "$HERE/scheduled-check-runner-exit-codes.test.sh" 2>&1)
    EXIT_SUITE=$(printf '%s\n' "$out" | tail -1)
    eq "$EXIT_SUITE" "pass=8 fail=0"
}
case_untouched_blocks() { # capture line, rc mapping, heartbeat printf, NEEDS_ALERT arm, notify block: byte-identical
    local old="$ROOT/base-runner.sh"
    git -C "$REPO" show "$BASE:scheduled-check-runner.sh" > "$old" || return 1
    python3 - "$old" "$RUNNER" <<'PY'
import sys
L = open(sys.argv[1]).read().split("\n")
new = "\n" + open(sys.argv[2]).read() + "\n"
def block(start, end):
    i = next(k for k, l in enumerate(L) if start(l))
    j = next(k for k in range(i, len(L)) if end(L[k]))
    return "\n".join(L[i:j + 1])
blocks = {
    "capture":     block(lambda l: l == 'OUT="$("$@" 2>&1)"; RC=$?', lambda l: True),
    "rc mapping":  block(lambda l: l == "STATUS=ok", lambda l: l == "esac"),
    "hb printf":   block(lambda l: l.startswith("printf '{\"ts\""), lambda l: l.endswith('>> "$HEARTBEAT"')),
    "NEEDS_ALERT": block(lambda l: l == 'case "$STATUS" in', lambda l: l == "esac"),
    "notify":      block(lambda l: l == 'if [ "$NEEDS_ALERT" -eq 1 ]; then', lambda l: l == "fi"),
}
# each block present, unaltered, and exactly ONCE: a modified live copy next to an untouched dead one fails
bad = [k for k, b in blocks.items() if new.count("\n" + b + "\n") != 1]
print("        lines per block:", {k: b.count("\n") + 1 for k, b in blocks.items()}, "altered or duplicated:", bad)
sys.exit(1 if bad else 0)
PY
}
case_additive() { # vs the pre-pilot runner: zero deleted lines, the capture line exactly once and unindented
    local del
    del=$(git -C "$REPO" diff --numstat "$BASE" -- scheduled-check-runner.sh | cut -f2)
    ADD_NUMSTAT=$(git -C "$REPO" diff --numstat "$BASE" -- scheduled-check-runner.sh)
    eq "${del:-0}" 0 && eq "$(grep -Fxc -- 'OUT="$("$@" 2>&1)"; RC=$?' "$RUNNER")" 1
}
case_readers() { # the three heartbeat readers return the SAME verdicts with and without the three additive keys
    fixture; local h=$FX
    HOME="$h" python3 - "$h" "$REAL_HOME" <<'PY'
import importlib.util, json, sys, datetime as dt
from pathlib import Path
h, real = Path(sys.argv[1]), Path(sys.argv[2])
now = dt.datetime.now(dt.timezone.utc)
def ts(m): return (now - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
base = [dict(ts=ts(5), check="artifact-sweep", rc=1, marker_hit=1, status="adverse"),
        dict(ts=ts(9), check="gate-ledger-archive", rc=0, marker_hit=0, status="ok"),
        dict(ts=ts(30), check="shv-gate-monitor", rc=3, marker_hit=0, status="idle"),
        dict(ts=ts(60), check="correction-rate", rc=2, marker_hit=0, status="unknown"),
        dict(ts=ts(61), check="correction-rate", status="notify_failed")]
plain, extra = h / "plain.jsonl", h / "extra.jsonl"
plain.write_text("".join(json.dumps(r) + "\n" for r in base))
extra.write_text("".join(json.dumps(dict(r, inv="1-2-3", dur_ms=40, budget_exceeded=1) if "rc" in r else r) + "\n" for r in base))
def load(p):
    s = importlib.util.spec_from_file_location("m" + str(abs(hash(p))), p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
ac = load(str(real / "dev/infrastructure/tools/anomaly-conversion-check.py"))
go = load(str(real / "dev/infrastructure/tools/governed-outcomes-check.py"))
sw = load(str(real / "dev/projects/sage-hayward/bin/shv_heartbeat_watchdog.py"))
out = {}
for name, p in (("plain", plain), ("extra", extra)):
    ac.HEARTBEAT = p
    out[name] = (sorted(ac._ran_checks()), go.outcome_check_not_running(p), sw.newest_row(p, "shv-gate-monitor"))
print("readers:", "SAME" if out["plain"] == out["extra"] else "DIFFER", "|", out["extra"][0], out["extra"][2][1])
sys.exit(0 if out["plain"] == out["extra"] else 1)
PY
}
case_census() { # starts whose runner is gone are paged ONCE; closed, fresh and still-running starts are not
    fixture; local h=$FX r live pm rc=0
    enable "$h" ALL; r=$(RUNS "$h"); pm=$(cat /proc/sys/kernel/pid_max)      # pid_max itself is never a pid
    fixture; live=$FX; enable "$live" scrt-live
    runbg "$live" scrt-live - "$(child "$live" lv 'sleep 8; exit 0' '# BUDGET: 30')"; local lp=$RP
    local old=$(( ${EPOCHREALTIME/[.,]/} / 1000 - 100000 )) new=$(( ${EPOCHREALTIME/[.,]/} / 1000 ))
    {
        printf '{"ts_ms":%s,"inv":"dead-1","phase":"start","check":"scrt-dead","pid":%s,"budget_s":1}\n' "$old" "$pm"
        # timeout is NOT terminal: its runner died after the page, so the proposal can never close
        printf '{"ts_ms":%s,"inv":"tmo-1","phase":"start","check":"scrt-tmo","pid":%s,"budget_s":1}\n' "$old" "$pm"
        printf '{"ts_ms":%s,"inv":"tmo-1","phase":"timeout","check":"scrt-tmo","pid":%s}\n' "$old" "$pm"
        printf '{"ts_ms":%s,"inv":"done-1","phase":"start","check":"scrt-done","pid":%s,"budget_s":1}\n' "$old" "$pm"
        printf '{"ts_ms":%s,"inv":"done-1","phase":"end","check":"scrt-done","pid":%s}\n' "$old" "$pm"
        printf '{"ts_ms":%s,"inv":"fresh-1","phase":"start","check":"scrt-fresh","pid":%s,"budget_s":600}\n' "$new" "$pm"
        printf '{"ts_ms":%s,"inv":"live-1","phase":"start","check":"scrt-alive","pid":%s,"pstart":%s,"budget_s":1}\n' "$old" "$lp" "$(pstart_of "$lp")"
        # pid REUSE: a live pid with the wrong start time is not the runner that wrote the row
        printf '{"ts_ms":%s,"inv":"reuse-1","phase":"start","check":"scrt-reuse","pid":%s,"pstart":1,"budget_s":1}\n' "$old" "$$"
        printf 'not json at all\n'
    } > "$r"
    run "$h" scrt-cen - "$(child "$h" cen 'exit 0')"
    CEN1=$(pages "$h")
    run "$h" scrt-cen - "$(child "$h" cen 'exit 0')"                            # dedupe: no second page
    wait "$lp"
    eq "$CEN1|$(pages "$h")" "3|3" || rc=1
    eq "$(qlax "$r" orphan)" 3 || rc=1
    eq "$(grep -v '^not json' "$r" | python3 -c "import json,sys; print(sorted(r['inv'] for r in map(json.loads, sys.stdin) if r['phase'] == 'orphan'))")" \
       "['dead-1', 'reuse-1', 'tmo-1']" || rc=1
    no_lock_fd_in_notify "$h" || { echo "        notify.sh inherited the census lock fd"; rc=1; }
    chmod 200 "$r"       # write-only: the runner can still append, the census cannot read -> census_failed
    run "$h" scrt-cen - "$h/cen.sh"
    chmod 600 "$r"
    eq "$(qlax "$r" census_failed)" 1 || rc=1                                     # a broken census is not silent
    PYTHONHOME=/nonexistent run "$h" scrt-cen - "$h/cen.sh"    # python3 cannot start: the `||` branch
    eq "$(qlax "$r" census_failed)" 2 || rc=1
    rm -f "$h/.metrics/scheduled-check-locks/.census" && mkdir "$h/.metrics/scheduled-check-locks/.census"
    run "$h" scrt-cen - "$h/cen.sh"                             # census lock unopenable (a directory): says so
    eq "$(qlax "$r" census_failed)" 3 || rc=1
    return $rc
}
case_unit_setu() { # set -u: a pid that vanished before the /proc read aborts nothing; the ms clock tolerates a ',' radix
    local defs
    defs=$(sed -n '/^    _now_ms() /p; /^    _san() /p; /^    _scr_tree() {/,/^    }/p' "$RUNNER")
    [ -n "$defs" ] || return 1
    eq "$(bash -u -c "$defs"$'\n'"_scr_tree $(cat /proc/sys/kernel/pid_max); echo rc=\$?" | tr '\t' '|')" "0|"$'\n'"rc=0" &&
    eq "$(bash -u -c "$defs"$'\n''unset EPOCHREALTIME; EPOCHREALTIME=1727000000,123456; _now_ms')" 1727000000123
}

# ───────────────────────────── main ──────────────────────────────────────────────────────────────────────
echo "runner under test: $RUNNER  (pre-pilot base: $BASE)"
LIVE_ENABLED=absent; [ -e "$REAL_HOME/.config/scheduled-check-runner/enabled" ] && LIVE_ENABLED=PRESENT
ck "disabled: the existing 8-case exit-code suite passes"            case_exit_codes_suite
ck "disabled: capture/rc mapping/heartbeat printf/NEEDS_ALERT/notify byte-identical to $BASE" case_untouched_blocks
ck "disabled: diff vs $BASE is purely additive; capture line once, unindented" case_additive
ck "disabled: no runs ledger, no lock dir, 5-key heartbeat row"      case_disabled_no_rows
ck "SAFETY differential: 12 children (late grandchild, SIGINT/TERM/KILL, /dev/stderr) identical enabled vs disabled" case_differential
ck "positive: timeout+proposed while alive, late end with its own rc 3, one page" case_positive
ck "pgid: child shares the runner's group, is not a leader (no setsid, D9)" case_pgid
ck "negative: fast child -> no timeout even after the budget; wall < 3 s" case_negative
ck "true hang: alive at 4x budget -> verified, no refused"            case_verified
ck "BUDGET '09' parses as 9 (base-10)"                               case_octal
ck "BUDGET range and exact grammar (0, 99999, 86400, none, #BUDGET)"  case_ranges
ck "locator: a data file never sets a budget; stop at the first #! file" case_datafile
ck "E2E env -i: FIFO-blocked child -> timeout at budget, late end, 1 heartbeat row" case_fifo_e2e
ck "boundary: child ends at ~BUDGET -> consistent accounting"        case_boundary
ck "concurrency: two checks, rows by inv, no leftover temp file"     case_concurrency
ck "grandchild off the pipe: prompt end row; the lock is not inherited" case_grandchild
ck "progressing=yes for a CPU-burning child past its budget"         case_progressing
ck "next run finds a breached run still locked -> verified, no refused" case_next_tick_verified
ck "signal parity: SigIgn seen by the check equal on both paths, both entry states" case_signal_parity
ck "enabled file: CR and surrounding blanks tolerated"               case_enabled_file_trim
ck "capture failure (full /tmp proxy) -> unknown + page; the check is drained, not SIGPIPEd" case_capture_failed
ck "disabled: a directory or FIFO at the enabled path -> pre-pilot path, no abort, no hang" case_enabled_path_not_a_file
ck "rc file never written -> rc_unavailable, rc 70 unknown, valid JSON" case_rc_unavailable
ck "runner TERMed while a grandchild holds the pipe -> temp dir removed, next run not locked out" case_runner_death
ck "notify failure on the over-budget page -> notify_failed row"     case_notify_failed
ck "suspend: runner SIGSTOPped 8 s -> suspended=1, no false breach"  case_suspend
ck "suspend control: no stop, child past budget -> breach, suspended=0" case_suspend_control
ck "lock: second run -> skipped-locked (holder = first inv), no page, no heartbeat" case_lock
ck "lock path unusable -> lock_unavailable, the check still runs"    case_lock_unavailable
ck "mktemp fails -> budget_unavailable, pre-pilot path (5-key row)"  case_mktemp_fails
ck "adversarial output and BUDGET line: nothing executes; quoted NAME sanitised" case_adversarial
ck "stdin: EOF on both paths under cron-like stdin; enabled EOF on a pipe" case_stdin
ck "readers: 3 heartbeat readers give identical verdicts with the additive keys" case_readers
ck "orphan census: pages a dead start once; closed/fresh/alive not paged; broken census -> row" case_census
ck "unit: vanished pid under set -u; ',' radix in EPOCHREALTIME"      case_unit_setu

echo
echo "── mutants (each must FAIL its case; the applied change is shown first) ──"
MT=$(mktemp -d "$ROOT/mut.XXXXXX")
mutant() { # id sed-expr case description
    local id=$1 expr=$2 cs=$3 m="$MT/$1/scheduled-check-runner.sh" d   # the real basename: nothing may key on it
    mkdir -p "$MT/$1"; sed "$expr" "$RUNNER" > "$m"
    d=$(diff "$RUNNER" "$m" | grep '^[<>]' | cut -c1-150)
    if [ -z "$d" ] || ! bash -n "$m" 2>/dev/null; then printf '  FAIL  %s NOT APPLIED (or not valid bash)\n' "$id"; fail=$((fail + 1)); return; fi
    printf '  %s applied:\n%s\n' "$id" "$(printf '%s\n' "$d" | sed 's/^/        /')"
    if ( RUNNER="$m" "$cs" ) >/dev/null 2>&1; then   # subshell: a mutant cannot overwrite the evidence
        printf '  FAIL  %s SURVIVED: %s still passes -- the suite is vacuous for it\n' "$id" "$cs"; fail=$((fail + 1))
    else
        printf '  PASS  %s CAUGHT by %s (%s)\n' "$id" "$cs" "$4"; pass=$((pass + 1)); caught=$((caught + 1))
    fi
}
mutant m1 's/if \[ "\$_SCR_T" -eq "\$_SCR_B" \]; then _scr_breach; fi/:/' case_positive "budget check removed"
mutant m2 's/while kill -0 "\$_SCR_TAIL" 2>\/dev\/null; do/while [ "$_SCR_T" -le "$_SCR_B" ]; do/' case_negative "poll loop made unconditional"
mutant m3 's/b=\$((10#/b=$((/' case_octal "base-10 prefix removed"
mutant m4 's/^            break$/            :/' case_datafile "locator no longer stops at the first #! file"
mutant m5 's/^            {_SCR_FD}>&- 2>\/dev\/null \\$/            2>\/dev\/null \\/' case_grandchild "the check keeps the lock fd"
mutant m6 's/ph in ("end", "skipped-locked", "orphan")/ph in ("end", "timeout", "skipped-locked", "orphan")/' case_census "timeout treated as terminal"
mutant m7 's/|| _row notify_failed "\$_SCR_INV" "\$NAME" .*over-budget page not delivered.*) {/|| : ) {/' case_notify_failed "a failed page leaves no row"
mutant m8 's/local c1 ev prog=no/local c1 ev prog=yes/' case_fifo_e2e "progressing always yes"
mutant m9 's/ ) {_SCR_FD}>&- &$/ ) \&/' case_positive "the page inherits the lock fd"
mutant m10 's/"\$@" < \/dev\/null 2>&1; echo "\$?" > "\$_SCR_DIR\/rc"; }/"$@" < \/dev\/null > "$_SCR_DIR\/late" 2>\&1; echo "$?" > "$_SCR_DIR\/rc"; }/' case_differential "output diverted from the pipe"
mutant m11 's/\[\[ \$RC =~ \^\[0-9\]+\$ \]\] || { _row rc_unavailable.*RC=70; }/:/' case_rc_unavailable "the missing-rc guard removed"
mutant m12 's/cat > \/dev\/null; }; } {_SCR_FD}>&- &$/cat > \/dev\/null; }; } \&/' case_runner_death "the capture tail keeps the lock fd"
mutant m13 's/^            RC=70$/            :/' case_capture_failed "an incomplete capture keeps the check's rc"
mutant m14 's/^if \[ -f "\$HOME\/\.config\/scheduled-check-runner\/enabled" \] && /if /' case_enabled_path_not_a_file "the regular-file guard removed"
mutant m15 's/^            {_SCR_FD}>&- 2>\/dev\/null \\$/            {_SCR_FD}>\&- 2>\&1 \\/' case_differential "the group's own stderr goes into the capture"

echo
echo "── evidence ──"
printf '  positive: child pid %s alive-at-timeout=%s pgid(child)=%s pgid(runner)=%s\n' "${POS_CHILD:-?}" "${POS_ALIVE:-?}" "${POS_PGC:-?}" "${POS_PGR:-?}"
printf '  negative: runner wall %s ms (budget 5 s)\n' "${NEG_WALL_MS:-?}"
printf '  boundary: timeout rows = %s\n' "${BND_T:-?}"
printf '  suspend: (timeout rows, ticks, dur_ms, suspended) = %s\n' "${SUS:-?}"
printf '  exit-code suite: %s\n' "${EXIT_SUITE:-?}"
printf '  signal parity (SigIgn the check saw): %s\n' "${SIGP:-?}"
printf '  diff numstat vs %s: %s\n' "$BASE" "${ADD_NUMSTAT:-?}"
printf '  stdin (INFO, not asserted): %s -- the enabled path always gives EOF\n' "${STDIN_DELTA:-?}"
if [ -n "${FIFO_H:-}" ]; then
    echo "  E2E FIFO runs ledger (verbatim):"; sed 's/^/    /' "$(RUNS "$FIFO_H")"
    echo "  E2E FIFO heartbeat (verbatim):"; sed 's/^/    /' "$(HB "$FIFO_H")"
fi

# live-state guard: the suite must not have written a single row for a test check into the real HOME
LIVE_HITS=$(python3 - "$REAL_HOME" <<'PY'
import json, sys
from pathlib import Path
n = 0
for f in ("scheduled-check-heartbeat.jsonl", "scheduled-check-runs.jsonl"):
    p = Path(sys.argv[1]) / ".metrics" / f
    if p.exists():
        for l in p.read_text(errors="replace").splitlines():
            try:
                n += str(json.loads(l).get("check", "")).startswith("scrt-")
            except ValueError:
                pass
print(n)
PY
)
ck "live guard: 0 rows for scrt-* checks in the real ~/.metrics (found $LIVE_HITS)" eq "$LIVE_HITS" 0
echo "  live enabled file: $LIVE_ENABLED"

[ "${KEEP:-0}" = 1 ] || rm -rf -- "$ROOT"   # the suite's own mktemp root, nothing else
echo
echo "pass=$pass fail=$fail mutants_caught=$caught/15"
[ "$fail" -eq 0 ] && [ "$caught" -eq 15 ]
