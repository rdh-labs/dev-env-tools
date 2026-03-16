#!/usr/bin/env bash
# run-tests.sh -- Schema-to-Mermaid test harness
# Usage: ./run-tests.sh [fixture-name]
# Exit:  0=all pass, 1=failures
# AWK dialect: gawk required

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures"
EXPECTED="$SCRIPT_DIR/expected"
SCRIPT="$(realpath "$SCRIPT_DIR/../../schema-to-mermaid.sh")"
VALIDATOR="$SCRIPT_DIR/validate-mermaid.sh"

# Check gawk
which gawk >/dev/null 2>&1 || { echo "ERROR: gawk required but not found"; exit 1; }

PASS=0
FAIL=0
SKIP=0
FAILED_TESTS=()

run_test() {
    local fixture="$1"
    local name
    name="$(basename "$fixture")"
    local expected_file="$EXPECTED/$name.mmd"

    if [[ ! -f "$expected_file" ]]; then
        echo "  SKIP $name (no expected file)"
        SKIP=$((SKIP + 1))
        return 0
    fi

    local tmp_actual
    tmp_actual="$(mktemp /tmp/schemermaid-actual.XXXXXX)"
    local tmp_filtered
    tmp_filtered="$(mktemp /tmp/schemermaid-filtered.XXXXXX)"

    # Run script (allow non-zero exit -- capture failure)
    local run_ok=0
    bash "$SCRIPT" --input "$fixture" --output "$tmp_actual" 2>/dev/null && run_ok=1 || run_ok=0

    if [[ $run_ok -eq 0 ]]; then
        echo "  FAIL $name (script exited non-zero)"
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$name")
        rm -f "$tmp_actual" "$tmp_filtered"
        return 0
    fi

    # Structural validation
    local valid_ok=0
    bash "$VALIDATOR" "$tmp_actual" 2>/dev/null && valid_ok=1 || valid_ok=0
    if [[ $valid_ok -eq 0 ]]; then
        echo "  FAIL $name (structural validation failed)"
        bash "$VALIDATOR" "$tmp_actual" || true
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$name")
        rm -f "$tmp_actual" "$tmp_filtered"
        return 0
    fi

    # Extract stable content: from erDiagram onward (skip variable header)
    awk '/^erDiagram/{found=1} found{print}' "$tmp_actual" > "$tmp_filtered"

    if diff -q "$expected_file" "$tmp_filtered" >/dev/null 2>&1; then
        echo "  PASS $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL $name (output mismatch)"
        diff "$expected_file" "$tmp_filtered" | head -30 || true
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$name")
    fi

    rm -f "$tmp_actual" "$tmp_filtered"
}

echo "=== Schema-to-Mermaid Test Suite ==="
echo "Script: $SCRIPT"
echo ""

# Determine which fixtures to run
if [[ $# -gt 0 ]]; then
    run_test "$FIXTURES/$1"
else
    for f in "$FIXTURES"/*.sql "$FIXTURES"/*.prisma; do
        [[ -f "$f" ]] || continue
        run_test "$f"
    done
fi

echo ""
echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
if [[ $FAIL -gt 0 ]]; then
    echo "Failed: ${FAILED_TESTS[*]}"
    exit 1
fi
exit 0
