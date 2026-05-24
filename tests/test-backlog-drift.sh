#!/usr/bin/env bash
# Deterministic test harness for backlog-drift-check.sh (IDEA-10307 Tier 1).
#
# ROOT OF TRUST for the backlog-drift gate. Reproducible, objective: each fixture
# either produces the expected stdout or it doesn't - no AI judgment. The user can
# re-run this independently:  bash ~/bin/tests/test-backlog-drift.sh
#
# Tests the FULL two-condition gate: fires only when HIGH>=8 AND last HIGH closed
# was >7 days ago (genuine drift). Stays silent while HIGH tasks are actively closing.
# Degrades fail-safe (HIGH>=8 only) when close-recency is unavailable.
#
# No network: the helper is pure cache-read; fixtures embed last_high_closed_at.
set -uo pipefail

HELPER="${BACKLOG_DRIFT_HELPER:-$HOME/bin/backlog-drift-check.sh}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

# ── fixture builders ─────────────────────────────────────────────────────────
# make_cache <path> <n_high> <n_critical> [last_high_closed_at]
make_cache() {
    local path="$1" nh="$2" nc="$3" closed="${4:-__OMIT__}"
    python3 - "$path" "$nh" "$nc" "$closed" <<'PY'
import json, sys
path, nh, nc, closed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
tasks = []
for i in range(nc):
    tasks.append({"id": f"crit{i}", "title": f"crit {i}", "priority": "Critical",
                  "dartboard": "General", "tags": []})
for i in range(nh):
    tasks.append({"id": f"high{i}", "title": f"high {i}", "priority": "High",
                  "dartboard": "General", "tags": []})
cache = {"schema_version": 3, "refreshed_at": 0, "dartboards": ["General"],
         "top_tasks": tasks, "input_needed_or_blocker": []}
if closed != "__OMIT__":
    cache["last_high_closed_at"] = closed
open(path, "w").write(json.dumps(cache))
PY
}

iso_days_ago() {  # ISO8601 with TZ + microseconds, matching Dart completedAt format
    python3 -c "import datetime,sys; print((datetime.datetime.now().astimezone()-datetime.timedelta(days=float(sys.argv[1]))).isoformat())" "$1"
}

# run helper against a cache; echo stdout. Isolated fire-count state per call unless shared.
run_gate() {  # <cache> [state_file]
    local cache="$1" state="${2:-$TMP/state-$RANDOM}"
    BACKLOG_DRIFT_STATE_FILE="$state" "$HELPER" "$cache" 2>/dev/null
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
assert_exit0() {  # <name> <cache>
    local name="$1" cache="$2"
    BACKLOG_DRIFT_STATE_FILE="$TMP/state-$RANDOM" "$HELPER" "$cache" >/dev/null 2>&1
    local rc=$?
    if [[ $rc -eq 0 ]]; then echo "PASS: $name (exit 0)"; PASS=$((PASS+1))
    else echo "FAIL: $name - expected exit 0, got $rc"; FAIL=$((FAIL+1)); fi
}

echo "=== backlog-drift-check.sh deterministic harness ==="
[[ -x "$HELPER" ]] || echo "NOTE: helper not executable/present at $HELPER - failures expected (TDD red phase)"

# 1. drift-fires: 8 HIGH + closed 10d ago → FIRE
c="$TMP/drift.json"; make_cache "$c" 8 0 "$(iso_days_ago 10)"
assert_fires "drift-fires (8 HIGH, closed 10d)" "$(run_gate "$c")"

# 2. actively-closing: 8 HIGH + closed 3d ago → SILENT (not drift)
c="$TMP/active.json"; make_cache "$c" 8 0 "$(iso_days_ago 3)"
assert_silent "actively-closing (8 HIGH, closed 3d)" "$(run_gate "$c")"

# 3. boundary just-over: 8 HIGH + closed 7d+1h ago → FIRE
c="$TMP/over.json"; make_cache "$c" 8 0 "$(iso_days_ago 7.05)"
assert_fires "boundary >7d (7d+1h)" "$(run_gate "$c")"

# 4. boundary just-under: 8 HIGH + closed 7d-1h ago → SILENT
c="$TMP/under.json"; make_cache "$c" 8 0 "$(iso_days_ago 6.95)"
assert_silent "boundary <7d (7d-1h)" "$(run_gate "$c")"

# 5. degraded fail-safe: 8 HIGH + last_high_closed_at ABSENT → FIRE w/ note
c="$TMP/degraded.json"; make_cache "$c" 8 0
out="$(run_gate "$c")"
assert_fires "degraded (no close data)" "$out"
assert_contains "degraded note" "$out" "close-recency unavailable"

# 6. count-low: 7 HIGH + closed 10d ago → SILENT (below threshold)
c="$TMP/low.json"; make_cache "$c" 7 0 "$(iso_days_ago 10)"
assert_silent "count-low (7 HIGH)" "$(run_gate "$c")"

# 7. criticals-not-high: 2 Critical + 6 High + closed 10d → SILENT (Critical != High)
c="$TMP/crit.json"; make_cache "$c" 6 2 "$(iso_days_ago 10)"
assert_silent "criticals-not-counted (6 High + 2 Crit)" "$(run_gate "$c")"

# 8. boundary exactly-8: 8 HIGH + 2 Critical + closed 10d → FIRE (threshold >=8)
c="$TMP/eight.json"; make_cache "$c" 8 2 "$(iso_days_ago 10)"
assert_fires "boundary exactly 8 HIGH (+2 Crit)" "$(run_gate "$c")"

# 9. missing cache → SILENT, exit 0, no crash
assert_silent "missing-cache" "$(run_gate "$TMP/does-not-exist.json")"
assert_exit0 "missing-cache exit0" "$TMP/does-not-exist.json"

# 10. malformed JSON → SILENT, exit 0, no crash
c="$TMP/bad.json"; printf 'this is not json {{{' > "$c"
assert_silent "malformed-json" "$(run_gate "$c")"
assert_exit0 "malformed-json exit0" "$c"

# 11. fire-counter: two consecutive fires increment; a silent run resets
st="$TMP/counter-state"
cfire="$TMP/cf.json"; make_cache "$cfire" 8 0 "$(iso_days_ago 10)"
csilent="$TMP/cs.json"; make_cache "$csilent" 3 0 "$(iso_days_ago 10)"
o1="$(run_gate "$cfire" "$st")"; assert_contains "fire-counter run1" "$o1" "fired 1 consecutive"
o2="$(run_gate "$cfire" "$st")"; assert_contains "fire-counter run2" "$o2" "fired 2 consecutive"
run_gate "$csilent" "$st" >/dev/null   # silent run → reset
o3="$(run_gate "$cfire" "$st")"; assert_contains "fire-counter reset" "$o3" "fired 1 consecutive"

echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
