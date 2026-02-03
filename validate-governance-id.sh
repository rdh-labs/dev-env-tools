#!/bin/bash
# validate-governance-id.sh - Pre-write governance ID validation
# Prevents duplicate ID creation by checking existence BEFORE writing
#
# Usage:
#   validate-governance-id.sh DEC-141
#   validate-governance-id.sh ISSUE-147
#   validate-governance-id.sh IDEA-255
#
# Exit codes:
#   0 - ID is unique, safe to create
#   1 - ID already exists, HALT
#   2 - Invalid arguments
#
# Created: 2026-02-02
# Source: Critique finding - "create before verify" pattern prevention

set -euo pipefail

GOVERNANCE_DIR="${GOVERNANCE_DIR:-$HOME/dev/infrastructure/dev-env-docs}"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 <ID> [--quiet]"
    echo ""
    echo "Validates that a governance ID doesn't already exist before creation."
    echo ""
    echo "Arguments:"
    echo "  ID        Governance ID to validate (e.g., DEC-141, ISSUE-147, IDEA-255)"
    echo "  --quiet   Suppress output, only set exit code"
    echo ""
    echo "Exit codes:"
    echo "  0 - ID is unique, safe to create"
    echo "  1 - ID already exists, HALT"
    echo "  2 - Invalid arguments"
    echo ""
    echo "Examples:"
    echo "  $0 DEC-141           # Check if DEC-141 exists"
    echo "  $0 ISSUE-150 --quiet # Silent check for scripting"
    exit 2
}

# Parse arguments
QUIET=false
ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --quiet|-q)
            QUIET=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            if [[ -z "$ID" ]]; then
                ID="$1"
            else
                echo "Error: Unexpected argument: $1" >&2
                usage
            fi
            shift
            ;;
    esac
done

if [[ -z "$ID" ]]; then
    echo "Error: ID argument required" >&2
    usage
fi

# Validate ID format (PREFIX-NUMBER)
if ! [[ "$ID" =~ ^(DEC|ISSUE|IDEA)-[0-9]+$ ]]; then
    echo "Error: Invalid ID format '$ID'. Expected: DEC-123, ISSUE-45, or IDEA-67" >&2
    exit 2
fi

# Determine file based on ID prefix
case "$ID" in
    DEC-*)
        FILE="$GOVERNANCE_DIR/DECISIONS-LOG.md"
        SUMMARY="$GOVERNANCE_DIR/DECISIONS-SUMMARY.yaml"
        TYPE="Decision"
        ;;
    ISSUE-*)
        FILE="$GOVERNANCE_DIR/ISSUES-TRACKER.md"
        SUMMARY="$GOVERNANCE_DIR/ISSUES-SUMMARY.yaml"
        TYPE="Issue"
        ;;
    IDEA-*)
        FILE="$GOVERNANCE_DIR/IDEAS-BACKLOG.md"
        SUMMARY="$GOVERNANCE_DIR/IDEAS-SUMMARY.yaml"
        TYPE="Idea"
        ;;
    *)
        echo "Error: Unknown ID prefix. Expected DEC-*, ISSUE-*, or IDEA-*" >&2
        exit 2
        ;;
esac

# Check if governance files exist
if [[ ! -f "$FILE" ]]; then
    echo "Error: Governance file not found: $FILE" >&2
    exit 2
fi

# Check for ID in main file (header format)
# Matches: ### DEC-141, ## ISSUE-147, ### IDEA-255
MAIN_MATCHES=$(grep -cE "^#{2,3} $ID[^0-9]|^#{2,3} $ID$" "$FILE" 2>/dev/null) || MAIN_MATCHES=0

# Check for ID in summary file
SUMMARY_MATCHES=0
if [[ -f "$SUMMARY" ]]; then
    SUMMARY_MATCHES=$(grep -c "^  $ID:" "$SUMMARY" 2>/dev/null) || SUMMARY_MATCHES=0
fi

# Determine result
TOTAL_MATCHES=$((MAIN_MATCHES + SUMMARY_MATCHES))

if [[ $TOTAL_MATCHES -gt 0 ]]; then
    # ID EXISTS - HALT
    if [[ "$QUIET" == false ]]; then
        echo -e "${RED}❌ HALT: $ID already exists${NC}"
        echo ""
        echo "Found $MAIN_MATCHES occurrence(s) in $FILE"
        echo "Found $SUMMARY_MATCHES occurrence(s) in $SUMMARY"
        echo ""
        echo "Locations:"
        grep -nE "^#{2,3} $ID[^0-9]|^#{2,3} $ID$" "$FILE" 2>/dev/null | head -5 || true
        echo ""
        echo "Action: Use a different ID or update the existing entry."
        echo ""
        # Suggest next available ID (only if numeric suffix)
        PREFIX="${ID%%-*}"
        CURRENT_NUM="${ID##*-}"
        if [[ "$CURRENT_NUM" =~ ^[0-9]+$ ]]; then
            NEXT_NUM=$((CURRENT_NUM + 1))
            echo "Suggestion: Try $PREFIX-$NEXT_NUM"
        fi
        echo "Suggestion: Try $PREFIX-$NEXT_NUM"
    fi
    exit 1
else
    # ID is unique - safe to create
    if [[ "$QUIET" == false ]]; then
        echo -e "${GREEN}✅ $ID is unique - safe to create${NC}"
    fi
    exit 0
fi
