#!/bin/bash
# Validate reflexion report format
# Part of: IDEA-281 Tier 2 (IDEA-TBD-007)
# Usage: validate-reflexion-report.sh <report-file>

set -euo pipefail

REPORT="${1:-}"

# Check if file provided
if [ -z "$REPORT" ]; then
    echo "Usage: $0 <report-file>" >&2
    exit 1
fi

# Check if file exists
if [ ! -f "$REPORT" ]; then
    echo "❌ Error: File not found: $REPORT" >&2
    exit 1
fi

# Track validation status
VALID=true
ERRORS=()

# Check required sections
if ! grep -q "^## Confidence Assessment" "$REPORT"; then
    ERRORS+=("Missing required section: ## Confidence Assessment")
    VALID=false
fi

if ! grep -q "^## Critical Issues Identified" "$REPORT"; then
    ERRORS+=("Missing required section: ## Critical Issues Identified")
    VALID=false
fi

if ! grep -q "^## Recommendations for Next Session" "$REPORT"; then
    ERRORS+=("Missing required section: ## Recommendations for Next Session")
    VALID=false
fi

if ! grep -q "^## Assumptions Requiring Validation" "$REPORT"; then
    ERRORS+=("Missing required section: ## Assumptions Requiring Validation")
    VALID=false
fi

# Check confidence format
if ! grep -qE "\*\*Overall Confidence:\*\* [0-5]\.[0-9]/5\.0" "$REPORT"; then
    ERRORS+=("Invalid or missing confidence format (expected: **Overall Confidence:** X.X/5.0)")
    VALID=false
fi

# Report results
if [ "$VALID" = true ]; then
    echo "✅ Report format valid: $REPORT"
    exit 0
else
    echo "❌ Report format validation FAILED: $REPORT" >&2
    echo "" >&2
    for error in "${ERRORS[@]}"; do
        echo "  • $error" >&2
    done
    exit 1
fi
