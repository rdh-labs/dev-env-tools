#!/usr/bin/env bash
# Deterministic test harness for backlog-drift-check.sh (IDEA-10307 Tier 1).
#
# ROOT OF TRUST for the backlog-drift gate. Reproducible, objective: each fixture
# either produces the expected stdout or it doesn't - no AI judgment. The user can
# re-run this independently:  bash ~/bin/tests/test-backlog-drift.sh
#
# Tests the FULL two-condition gate: fires only when HIGH>=8 AND last HIGH closed
# was >7 days ago (genuine drift). Stays silent while HIGH tasks are actively closing.
# Degrades fail-safe (HIGH>=8 only) when close-recency is unavailable OR stale.
#
# No network. Post-fix (Dart ildWF7YsnNql): high_count comes from the main cache
# ($1) and close-recency from a SEPARATE dedicated file injected via
# DART_LAST_HIGH_CLOSE_FILE. The main cache NEVER carries last_high_closed_at -- so
# these fixtures also exercise the "survives a /queue refresh" property by construction.
set -uo pipefail

HELPER="${BACKLOG_DRIFT_HELPER:-$HOME/bin/backlog-drift-check.sh}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

# ── fixture builders ─────────────────────────────────────────────────────────
# make_cache <path> <n_high> <n_critical>   (main cache: top_tasks only, NO close field)
make_cache() {
    local path="$1" nh="$2" nc="$3"
    python3 - "$path" "$nh" "$nc" <<'PY'
import json, sys
path, nh, nc = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
tasks = [{"id": f"crit{i}", "title": f"crit {i}", "priority": "Critical",
          "dartboard": "General", "tags": []} for i in range(nc)]
tasks += [{"id": f"high{i}", "title": f"high {i}", "priority": "High",
           "dartboard": "General", "tags": []} for i in range(nh)]
cache = {"schema_version": 4, "refreshed_at": 0, "dartboards": ["General"],
         "top_tasks": tasks, "input_needed_or_blocker": []}
open(path, "w").write(json.dumps(cache))
PY
}

# make_close <path> <closed_iso> [queried_iso]   (dedicated single-source close file)
make_close() {
    local path="$1" closed="$2" queried="${3:-}"
    [[ -n "$queried" ]] || queried="$(iso_hours_ago 0)"
    python3 - "$path" "$closed" "$queried" <<'PY'
import json, sys
path, closed, queried = sys.argv[1], sys.argv[2], sys.argv[3]
open(path, "w").write(json.dumps({"last_high_closed_at": closed, "queried_at": queried}))
PY
}

iso_days_ago() {  # ISO8601 with TZ + microseconds, matching Dart completedAt format
    python3 -c "import datetime,sys; print((datetime.datetime.now().astimezone()-datetime.timedelta(days=float(sys.argv[1]))).isoformat())" "$1"
}
iso_hours_ago() {
    python3 -c "import datetime,sys; print((datetime.datetime.now().astimezone()-datetime.timedelta(hours=float(sys.argv[1]))).isoformat())" "$1"
}

# run helper against a (main cache, close file) pair; echo stdout.
# close="__NONE__" -> point the close-file env at a nonexistent path (degraded case).
run_gate() {  # <cache> <close|__NONE__> [state_file]
    local cache="$1" close="$2" state="${3:-$TMP/state-$RANDOM}"
    local closeenv="$close"
    [[ "$close" == "__NONE__" ]] && closeenv="$TMP/no-close-$RANDOM.json"
    BACKLOG_DRIFT_STATE_FILE="$state" DART_LAST_HIGH_CLOSE_FILE="$closeenv" \
        "$HELPER" "$cache" 2>/dev/null
    return 0
}

assert_fires() {  # <name> <output>
    local name="$1" out="$2"
    if grep -q '\[BACKLOG-DRIFT\]' <<<"$out"; then
        echo "PASS: $name (fired)"; PASS=$((PASS+1))
    else
        echo "FAIL: $name - expected fire, got: '${out:0:80}'"; FAIL=$((FAIL+1))
    fi
}
assert_silent() {  # <name> <output>
    local name="$1" out="$2"
    if [[ -z "$(tr -d '[:space:]' <<<"$out")" ]]; then
        echo "PASS: $name (silent)"; PASS=$((PASS+1))
    else
        echo "FAIL: $name - expected silence, got: '${out:0:80}'"; FAIL=$((FAIL+1))
    fi
}
assert_contains() {  # <name> <output> <substring>
    local name="$1" out="$2" sub="$3"
    if grep -qF "$sub" <<<"$out"; then
        echo "PASS: $name (contains '$sub')"; PASS=$((PASS+1))
    else
        echo "FAIL: $name - expected '$sub', got: '${out:0:120}'"; FAIL=$((FAIL+1))
    fi
}
assert_exit0() {  # <name> <cache> <close|__NONE__>
    local name="$1" cache="$2" close="$3" closeenv="$3"
    [[ "$close" == "__NONE__" ]] && closeenv="$TMP/no-close-$RANDOM.json"
    BACKLOG_DRIFT_STATE_FILE="$TMP/state-$RANDOM" DART_LAST_HIGH_CLOSE_FILE="$closeenv" \
        "$HELPER" "$cache" >/dev/null 2>&1
    local rc=$?
    if [[ $rc -eq 0 ]]; then echo "PASS: $name (exit 0)"; PASS=$((PASS+1))
    else echo "FAIL: $name - expected exit 0, got $rc"; FAIL=$((FAIL+1)); fi
}

echo "=== backlog-drift-check.sh deterministic harness ==="
[[ -x "$HELPER" ]] || echo "NOTE: helper not executable/present at $HELPER - failures expected (TDD red phase)"

# 1. drift-fires (survives /queue refresh): 8 HIGH (main cache, no close field) + dedicated close 10d → FIRE
c="$TMP/drift.json"; make_cache "$c" 8 0
cl="$TMP/drift-close.json"; make_close "$cl" "$(iso_days_ago 10)"
assert_fires "drift-fires (8 HIGH, dedicated close 10d, survives refresh)" "$(run_gate "$c" "$cl")"

# 2. actively-closing: 8 HIGH + closed 3d ago → SILENT (not drift)
c="$TMP/active.json"; make_cache "$c" 8 0
cl="$TMP/active-close.json"; make_close "$cl" "$(iso_days_ago 3)"
assert_silent "actively-closing (8 HIGH, closed 3d)" "$(run_gate "$c" "$cl")"

# 3. boundary just-over: 8 HIGH + closed 7d+1h ago → FIRE
c="$TMP/over.json"; make_cache "$c" 8 0
cl="$TMP/over-close.json"; make_close "$cl" "$(iso_days_ago 7.05)"
assert_fires "boundary >7d (7d+1h)" "$(run_gate "$c" "$cl")"

# 4. boundary just-under: 8 HIGH + closed 7d-1h ago → SILENT
c="$TMP/under.json"; make_cache "$c" 8 0
cl="$TMP/under-close.json"; make_close "$cl" "$(iso_days_ago 6.95)"
assert_silent "boundary <7d (7d-1h)" "$(run_gate "$c" "$cl")"

# 5. degraded fail-safe: 8 HIGH + dedicated close file ABSENT → FIRE w/ note
c="$TMP/degraded.json"; make_cache "$c" 8 0
out="$(run_gate "$c" "__NONE__")"
assert_fires "degraded (no close file)" "$out"
assert_contains "degraded note" "$out" "close-recency unavailable"

# 6. count-low: 7 HIGH + closed 10d ago → SILENT (below threshold)
c="$TMP/low.json"; make_cache "$c" 7 0
cl="$TMP/low-close.json"; make_close "$cl" "$(iso_days_ago 10)"
assert_silent "count-low (7 HIGH)" "$(run_gate "$c" "$cl")"

# 7. criticals-not-high: 2 Critical + 6 High + closed 10d → SILENT (Critical != High)
c="$TMP/crit.json"; make_cache "$c" 6 2
cl="$TMP/crit-close.json"; make_close "$cl" "$(iso_days_ago 10)"
assert_silent "criticals-not-counted (6 High + 2 Crit)" "$(run_gate "$c" "$cl")"

# 8. boundary exactly-8: 8 HIGH + 2 Critical + closed 10d → FIRE (threshold >=8)
c="$TMP/eight.json"; make_cache "$c" 8 2
cl="$TMP/eight-close.json"; make_close "$cl" "$(iso_days_ago 10)"
assert_fires "boundary exactly 8 HIGH (+2 Crit)" "$(run_gate "$c" "$cl")"

# 9. stale close-recency: 8 HIGH + closed 10d ago but last query 25h ago → FIRE degraded (stale)
c="$TMP/stale.json"; make_cache "$c" 8 0
cl="$TMP/stale-close.json"; make_close "$cl" "$(iso_days_ago 10)" "$(iso_hours_ago 25)"
out="$(run_gate "$c" "$cl")"
assert_fires "stale close-recency (queried 25h ago)" "$out"
assert_contains "stale -> degraded note" "$out" "close-recency unavailable"

# 10. fresh-but-recent query (23h) still trusts the value: 8 HIGH + closed 10d, queried 23h → FIRE (precise)
c="$TMP/fresh.json"; make_cache "$c" 8 0
cl="$TMP/fresh-close.json"; make_close "$cl" "$(iso_days_ago 10)" "$(iso_hours_ago 23)"
out="$(run_gate "$c" "$cl")"
assert_fires "fresh query (23h) trusts value" "$out"
assert_contains "fresh -> precise note" "$out" "last HIGH closed"

# 10b. clock skew: queried_at in the FUTURE → untrusted → FIRE degraded (stale)
c="$TMP/future.json"; make_cache "$c" 8 0
cl="$TMP/future-close.json"; make_close "$cl" "$(iso_days_ago 10)" "$(iso_hours_ago -2)"
out="$(run_gate "$c" "$cl")"
assert_fires "future queried_at (clock skew → degraded)" "$out"
assert_contains "future → degraded note" "$out" "close-recency unavailable"

# 11. missing cache → SILENT, exit 0, no crash
assert_silent "missing-cache" "$(run_gate "$TMP/does-not-exist.json" "__NONE__")"
assert_exit0 "missing-cache exit0" "$TMP/does-not-exist.json" "__NONE__"

# 12. malformed main-cache JSON → SILENT, exit 0, no crash
c="$TMP/bad.json"; printf 'this is not json {{{' > "$c"
assert_silent "malformed-json" "$(run_gate "$c" "__NONE__")"
assert_exit0 "malformed-json exit0" "$c" "__NONE__"

# 13. fire-counter: two consecutive fires increment; a silent run resets
st="$TMP/counter-state"
cfire="$TMP/cf.json"; make_cache "$cfire" 8 0
clfire="$TMP/cf-close.json"; make_close "$clfire" "$(iso_days_ago 10)"
csilent="$TMP/cs.json"; make_cache "$csilent" 3 0
clsilent="$TMP/cs-close.json"; make_close "$clsilent" "$(iso_days_ago 10)"
o1="$(run_gate "$cfire" "$clfire" "$st")"; assert_contains "fire-counter run1" "$o1" "fired 1 consecutive"
o2="$(run_gate "$cfire" "$clfire" "$st")"; assert_contains "fire-counter run2" "$o2" "fired 2 consecutive"
run_gate "$csilent" "$clsilent" "$st" >/dev/null   # silent run → reset
o3="$(run_gate "$cfire" "$clfire" "$st")"; assert_contains "fire-counter reset" "$o3" "fired 1 consecutive"

echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
