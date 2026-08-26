#!/usr/bin/env bash
# Contract test: scheduled-check-runner.sh exit-code -> status/notify mapping.
#
# DEC-334 (ACCEPTED 2026-08-19) defines FOUR states:
#   0 HEALTHY | 1 DEGRADED | 2 CANNOT-ASSESS | 3 NOTHING-TO-ASSESS
#   "Consumers branch 1|2 = act, 0|3 = silent. This is a CONTRACT change: any
#    consumer branching on `rc -ne 0` fires on an idle machine after it."
#
# WHY THIS TEST EXISTS. The runner is a THIRD exit-code consumer that DEC-334's own
# audit could not find, because it takes `-- <cmd...>` and never names the tool it
# consumes -- a grep-for-consumers audit is structurally blind to a generic wrapper.
# Its `*)` branch was exactly the forbidden `rc -ne 0` pattern, so rc=3 produced 229
# false high-priority pushes from context-ceiling-watch (which correctly exits 3 when
# no session is live), burying the 13 genuine rc=2 alerts at ~5% SNR.
#
# The runner ADDS an artifact channel on rc=0 (marker match -> adverse). That is not a
# DEC-334 violation and is asserted here so a future fix cannot silently drop it.
#
# FALSIFICATION CONTROL: run this against the PRE-FIX file and exactly ONE case must
# fail (rc=3), with the other six passing. A test that cannot be shown failing is not
# a test. To do that:
#   cd ~/dev/infrastructure/tools
#   git show <pre-fix-sha>:scheduled-check-runner.sh > /tmp/old-runner.sh
#   RUNNER=/tmp/old-runner.sh bash tests/scheduled-check-runner-exit-codes.test.sh
#
# ISOLATION: each case runs with HOME=<tmpdir> holding a stub bin/notify.sh, so no real
# notification is sent and the real heartbeat at ~/.metrics is never written.
set -uo pipefail
RUNNER="${RUNNER:-$HOME/dev/infrastructure/tools/scheduled-check-runner.sh}"
pass=0; fail=0

run_case() { # name rc marker out expect_status expect_notify
    local name="$1" rc="$2" marker="$3" out="$4" exp_s="$5" exp_n="$6"
    local tmp; tmp=$(mktemp -d)
    mkdir -p "$tmp/bin"
    printf '#!/usr/bin/env bash\necho NOTIFIED >> "$HOME/notify.record"\nexit 0\n' > "$tmp/bin/notify.sh"
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$CHILD_OUT"\nexit "$CHILD_RC"\n' > "$tmp/child.sh"
    chmod +x "$tmp/bin/notify.sh" "$tmp/child.sh"

    HOME="$tmp" CHILD_OUT="$out" CHILD_RC="$rc" \
        bash "$RUNNER" testcheck "$tmp/test.log" "$marker" -- "$tmp/child.sh" >/dev/null 2>&1

    local hb="$tmp/.metrics/scheduled-check-heartbeat.jsonl" got_s got_n
    got_s=$(grep -o '"status":"[a-z_]*"' "$hb" 2>/dev/null | head -1 | cut -d'"' -f4)
    [ -z "$got_s" ] && got_s="<no-heartbeat>"
    if [ -f "$tmp/notify.record" ]; then got_n=yes; else got_n=no; fi

    if [ "$got_s" = "$exp_s" ] && [ "$got_n" = "$exp_n" ]; then
        printf '  PASS  %-32s status=%-8s notify=%s\n' "$name" "$got_s" "$got_n"; pass=$((pass+1))
    else
        printf '  FAIL  %-32s want status=%s notify=%s | got status=%s notify=%s\n' \
               "$name" "$exp_s" "$exp_n" "$got_s" "$got_n"; fail=$((fail+1))
    fi
    rm -rf "$tmp"
}

echo "runner under test: $RUNNER"
run_case "rc=0 no marker"         0   '-'       "all fine"          ok      no
run_case "rc=0 marker HIT"        0   'ADVERSE' "ADVERSE: thing"    adverse yes
run_case "rc=0 marker MISS"       0   'ADVERSE' "all fine"          ok      no
run_case "rc=1 DEGRADED"          1   '-'       "degraded"          adverse yes
run_case "rc=2 CANNOT-ASSESS"     2   '-'       "cannot assess"     unknown yes
run_case "rc=3 NOTHING-TO-ASSESS" 3   '-'       "nothing to assess" idle    no
run_case "rc=127 genuine failure" 127 '-'       "command not found" unknown yes

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ] || exit 1
