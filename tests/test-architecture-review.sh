#!/usr/bin/env bash
# Deterministic test harness for architecture-review-check.sh (ISSUE-3405 tripwire).
#
# ROOT OF TRUST for the architecture re-evaluation tripwire. Reproducible, objective:
# each fixture either produces the expected stdout or it doesn't - no AI judgment.
# Re-run independently:  bash ~/bin/tests/test-architecture-review.sh
#
# "today" is injected via ARCH_REVIEW_TODAY so date logic is deterministic against
# fixed fixture next_review dates. No network, no `claude mcp list`.
set -uo pipefail

HELPER="${ARCH_REVIEW_HELPER:-$HOME/bin/architecture-review-check.sh}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0

# make_reg <path> <status> <next_review> [extra_entry_status] [extra_next_review]
make_reg() {
    local path="$1" status="$2" nr="$3"
    {
        echo "schema_version: 1"
        echo "assumptions:"
        echo "  - id: test-entry"
        echo "    status: ${status}"
        echo "    assumption: a load-bearing belief"
        echo "    current_basis: holds for now"
        echo "    falsifier: vendor ships feature X"
        echo "    watch_source:"
        echo "      - https://example.invalid/changelog"
        echo "    on_review_action: run the live check"
        echo "    linked_issue: ISSUE-9999"
        echo "    next_review: ${nr}"
        if [[ -n "${4:-}" ]]; then
            echo "  - id: second-entry"
            echo "    status: ${4}"
            echo "    assumption: another belief"
            echo "    falsifier: vendor ships feature Y"
            echo "    on_review_action: do the other thing"
            echo "    next_review: ${5}"
        fi
    } > "$path"
}

run() {  # <reg> [today] [state]   -> stdout
    local reg="$1" today="${2:-2026-06-29}" state="${3:-$TMP/state-$RANDOM}"
    ARCH_REVIEW_STATE_FILE="$state" ARCH_REVIEW_ERRLOG="$TMP/err-$RANDOM.jsonl" \
        ARCH_REVIEW_TODAY="$today" "$HELPER" "$reg" 2>/dev/null
    return 0
}

assert_fires()   { if grep -q '\[ARCH-REVIEW\]' <<<"$2"; then echo "PASS: $1 (fired)"; PASS=$((PASS+1)); else echo "FAIL: $1 - expected fire, got: '${2:0:80}'"; FAIL=$((FAIL+1)); fi; }
assert_silent()  { if [[ -z "$(tr -d '[:space:]' <<<"$2")" ]]; then echo "PASS: $1 (silent)"; PASS=$((PASS+1)); else echo "FAIL: $1 - expected silence, got: '${2:0:80}'"; FAIL=$((FAIL+1)); fi; }
assert_contains(){ if grep -qF "$3" <<<"$2"; then echo "PASS: $1 (has '$3')"; PASS=$((PASS+1)); else echo "FAIL: $1 - want '$3', got: '${2:0:120}'"; FAIL=$((FAIL+1)); fi; }
assert_exit0()   { ARCH_REVIEW_STATE_FILE="$TMP/s-$RANDOM" ARCH_REVIEW_ERRLOG="$TMP/e-$RANDOM" ARCH_REVIEW_TODAY="2026-06-29" "$HELPER" "$2" >/dev/null 2>&1; local rc=$?; if [[ $rc -eq 0 ]]; then echo "PASS: $1 (exit 0)"; PASS=$((PASS+1)); else echo "FAIL: $1 - exit $rc"; FAIL=$((FAIL+1)); fi; }

echo "=== architecture-review-check.sh deterministic harness ==="
[[ -x "$HELPER" ]] || echo "NOTE: helper not executable/present at $HELPER - failures expected (TDD red phase)"

# 1. due active entry (next_review in the past) -> FIRE
r="$TMP/due.yaml"; make_reg "$r" active 2026-01-01
out="$(run "$r")"; assert_fires "due active entry" "$out"
assert_contains "due shows falsifier" "$out" "falsifier:"
assert_contains "due shows watch" "$out" "watch:"

# 2. next_review in the FUTURE -> SILENT
r="$TMP/future.yaml"; make_reg "$r" active 2099-01-01
assert_silent "future next_review" "$(run "$r")"

# 3. next_review == today (boundary, <=) -> FIRE
r="$TMP/today.yaml"; make_reg "$r" active 2026-06-29
assert_fires "boundary next_review == today" "$(run "$r" 2026-06-29)"

# 4. retired entry though due -> SILENT
r="$TMP/retired.yaml"; make_reg "$r" retired 2026-01-01
assert_silent "retired (due but inactive)" "$(run "$r")"

# 5. missing register -> SILENT + exit 0
assert_silent "missing register" "$(run "$TMP/nope.yaml")"
assert_exit0 "missing register exit0" "$TMP/nope.yaml"

# 6. malformed YAML -> SILENT + exit0 + ERRLOG written (no-silent-death)
r="$TMP/bad.yaml"; printf 'assumptions: [ this : is : not valid yaml ::: {{{' > "$r"
errlog="$TMP/errlog-malformed.jsonl"
ARCH_REVIEW_STATE_FILE="$TMP/s-bad" ARCH_REVIEW_ERRLOG="$errlog" ARCH_REVIEW_TODAY="2026-06-29" "$HELPER" "$r" >/dev/null 2>&1
rc=$?
[[ $rc -eq 0 ]] && { echo "PASS: malformed exit0"; PASS=$((PASS+1)); } || { echo "FAIL: malformed exit $rc"; FAIL=$((FAIL+1)); }
if [[ -s "$errlog" ]]; then echo "PASS: malformed logged (no-silent-death)"; PASS=$((PASS+1)); else echo "FAIL: malformed - ERRLOG empty (silent death)"; FAIL=$((FAIL+1)); fi

# 6b. register EXISTS but UNREADABLE (chmod 000) -> SILENT + exit0 + ERRLOG written.
# Regression for codex HIGH: [[ -f && -r ]] alone skipped silently = silent monitoring death.
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    echo "SKIP: unreadable-register test (root bypasses file perms)"
else
    r="$TMP/unreadable.yaml"; make_reg "$r" active 2026-01-01; chmod 000 "$r"
    errlog2="$TMP/errlog-unreadable.jsonl"
    out="$(ARCH_REVIEW_STATE_FILE="$TMP/s-unread" ARCH_REVIEW_ERRLOG="$errlog2" ARCH_REVIEW_TODAY="2026-06-29" "$HELPER" "$r" 2>/dev/null)"
    chmod 644 "$r" 2>/dev/null || true   # restore so EXIT-trap cleanup can remove it
    assert_silent "unreadable register (silent)" "$out"
    if [[ -s "$errlog2" ]]; then echo "PASS: unreadable logged (no-silent-death)"; PASS=$((PASS+1)); else echo "FAIL: unreadable - ERRLOG empty (silent death)"; FAIL=$((FAIL+1)); fi
fi

# 7. multiple due -> count reflects both; capped output safe
r="$TMP/two.yaml"; make_reg "$r" active 2026-01-01 active 2026-02-01
out="$(run "$r")"; assert_contains "two due -> count 2" "$out" "2 architectural assumption"

# 8. fire-counter increments across consecutive fires; resets after a silent run
st="$TMP/counter"
rfire="$TMP/cf.yaml"; make_reg "$rfire" active 2026-01-01
rsil="$TMP/cs.yaml";  make_reg "$rsil"  active 2099-01-01
o1="$(run "$rfire" 2026-06-29 "$st")"; assert_contains "counter run1" "$o1" "fired 1 consecutive"
o2="$(run "$rfire" 2026-06-29 "$st")"; assert_contains "counter run2" "$o2" "fired 2 consecutive"
run "$rsil" 2026-06-29 "$st" >/dev/null    # silent -> reset
o3="$(run "$rfire" 2026-06-29 "$st")"; assert_contains "counter reset" "$o3" "fired 1 consecutive"

echo "=== $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]
