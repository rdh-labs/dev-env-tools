#!/bin/bash
# ByteRover Governance Sync Script
# Syncs governance entries (DEC-###, ISSUE-###, IDEA-###) to ByteRover
#
# PREREQUISITE: brv REPL must be running in ~/dev/
# Usage: Run after adding new governance entries
#
# Created: 2026-02-04
# Updated: 2026-02-04 - R2 fixes: safe arithmetic, exit-code detection, injection-safe state parsing

set -e

GOVERNANCE_DIR="$HOME/dev/infrastructure/dev-env-docs"
SYNC_STATE_FILE="$HOME/.brv-governance-sync-state"
LOG_FILE="$HOME/.brv-governance-sync.log"
TIMEOUT=60
MAX_RETRIES=3

# Logging function
log() {
    local level=$1
    local msg=$2
    echo "$(date -Iseconds) [$level] $msg" >> "$LOG_FILE"
    if [ "$level" = "ERROR" ]; then
        echo "ERROR: $msg" >&2
    fi
}

# Atomic state update function (prevents race conditions)
update_state() {
    local last_dec=$1
    local last_issue=$2
    local last_idea=$3
    local tmp_state
    tmp_state=$(mktemp)

    echo "LAST_SYNC=$(date -Iseconds)" > "$tmp_state"
    echo "LAST_DEC=$last_dec" >> "$tmp_state"
    echo "LAST_ISSUE=$last_issue" >> "$tmp_state"
    echo "LAST_IDEA=$last_idea" >> "$tmp_state"

    mv "$tmp_state" "$SYNC_STATE_FILE"
    log "INFO" "State updated: DEC=$last_dec ISSUE=$last_issue IDEA=$last_idea"
}

# Safe number parsing (defaults to 0 if empty/invalid)
parse_number() {
    local val=$1
    if [[ "$val" =~ ^[0-9]+$ ]]; then
        echo "$val"
    else
        echo "0"
    fi
}

# Curate with retry logic
curate_with_retry() {
    local content=$1
    local retry=0

    while [ $retry -lt $MAX_RETRIES ]; do
        local exit_code=0
        timeout $TIMEOUT brv curate "$content" --headless &>/dev/null || exit_code=$?

        if [ $exit_code -eq 0 ]; then
            log "INFO" "Curated: ${content:0:50}..."
            echo "  ✓ Curated"
            return 0
        fi
        retry=$((retry + 1))
        if [ $retry -lt $MAX_RETRIES ]; then
            echo "  ⚠ Retry $retry/$MAX_RETRIES (exit=$exit_code)..."
            log "WARN" "Retry $retry (exit=$exit_code) for: ${content:0:50}..."
            sleep $((retry * 2))
        fi
    done

    log "ERROR" "Failed after $MAX_RETRIES retries: ${content:0:50}..."
    echo "  ✗ Failed after $MAX_RETRIES retries"
    return 1
}

# Extract title from YAML with better parsing
extract_title() {
    local id=$1
    local file=$2
    local title

    # Try to extract title, handling various YAML formats
    title=$(grep -A5 "^[[:space:]]*${id}:" "$file" 2>/dev/null | \
            grep -E "^[[:space:]]+(title|name):" | \
            head -1 | \
            sed 's/.*:\s*//' | \
            sed 's/^["'\'']//' | \
            sed 's/["'\'']$//' | \
            sed 's/^[[:space:]]*//' | \
            sed 's/[[:space:]]*$//')

    # Return empty if extraction failed
    if [ -z "$title" ] || [ "$title" = "null" ]; then
        echo ""
    else
        echo "$title"
    fi
}

echo "=============================================="
echo "ByteRover Governance Sync"
echo "=============================================="
echo ""

log "INFO" "=== Sync started ==="

# Check if brv is available
if ! command -v brv &> /dev/null; then
    log "ERROR" "brv command not found"
    echo "ERROR: brv command not found."
    exit 1
fi

# Check if brv REPL is running
STATUS=$(brv status 2>&1)
if echo "$STATUS" | grep -q "No instance running"; then
    log "ERROR" "brv REPL not running"
    echo "ERROR: brv REPL not running."
    echo "Start it first: cd ~/dev && brv"
    exit 1
fi

# Health check - verify brv is responsive
echo "Verifying ByteRover connection..."
if ! timeout 15 brv query "health check" --headless &>/dev/null; then
    log "ERROR" "brv not responding to queries"
    echo "ERROR: brv not responding to queries. Try restarting the REPL."
    exit 1
fi

echo "ByteRover connected and responsive."
echo ""

# Initialize sync state if not exists
if [ ! -f "$SYNC_STATE_FILE" ]; then
    echo "First run - creating sync state file..."
    log "INFO" "First run - initializing state"
    update_state 0 0 0
fi

# Read sync state safely (no source - prevents code injection)
LAST_DEC=$(parse_number "$(grep '^LAST_DEC=' "$SYNC_STATE_FILE" 2>/dev/null | cut -d'=' -f2)")
LAST_ISSUE=$(parse_number "$(grep '^LAST_ISSUE=' "$SYNC_STATE_FILE" 2>/dev/null | cut -d'=' -f2)")
LAST_IDEA=$(parse_number "$(grep '^LAST_IDEA=' "$SYNC_STATE_FILE" 2>/dev/null | cut -d'=' -f2)")

# Check governance files exist
if [ ! -f "$GOVERNANCE_DIR/DECISIONS-SUMMARY.yaml" ]; then
    log "ERROR" "DECISIONS-SUMMARY.yaml not found"
    echo "ERROR: $GOVERNANCE_DIR/DECISIONS-SUMMARY.yaml not found"
    exit 1
fi

echo "Checking for new governance entries..."

# Get latest entry numbers with safe parsing
LATEST_DEC_RAW=$(grep -oP 'DEC-\K\d+' "$GOVERNANCE_DIR/DECISIONS-SUMMARY.yaml" 2>/dev/null | sort -n | tail -1)
LATEST_ISSUE_RAW=$(grep -oP 'ISSUE-\K\d+' "$GOVERNANCE_DIR/ISSUES-SUMMARY.yaml" 2>/dev/null | sort -n | tail -1)
LATEST_IDEA_RAW=$(grep -oP 'IDEA-\K\d+' "$GOVERNANCE_DIR/IDEAS-SUMMARY.yaml" 2>/dev/null | sort -n | tail -1)

LATEST_DEC=$(parse_number "${LATEST_DEC_RAW:-0}")
LATEST_ISSUE=$(parse_number "${LATEST_ISSUE_RAW:-0}")
LATEST_IDEA=$(parse_number "${LATEST_IDEA_RAW:-0}")

# Calculate new entries with negative number guard
NEW_DECS=$((LATEST_DEC - LAST_DEC))
NEW_ISSUES=$((LATEST_ISSUE - LAST_ISSUE))
NEW_IDEAS=$((LATEST_IDEA - LAST_IDEA))

# Guard against negative numbers (can happen if IDs renumbered or state corrupted)
if [ "$NEW_DECS" -lt 0 ]; then
    log "WARN" "Negative DEC count ($NEW_DECS). Resetting state. LATEST=$LATEST_DEC LAST=$LAST_DEC"
    echo "WARNING: Decision count mismatch detected. Resetting state."
    NEW_DECS=0
    LAST_DEC=$LATEST_DEC
fi

if [ "$NEW_ISSUES" -lt 0 ]; then
    log "WARN" "Negative ISSUE count ($NEW_ISSUES). Resetting state."
    echo "WARNING: Issue count mismatch detected. Resetting state."
    NEW_ISSUES=0
    LAST_ISSUE=$LATEST_ISSUE
fi

if [ "$NEW_IDEAS" -lt 0 ]; then
    log "WARN" "Negative IDEA count ($NEW_IDEAS). Resetting state."
    echo "WARNING: Idea count mismatch detected. Resetting state."
    NEW_IDEAS=0
    LAST_IDEA=$LATEST_IDEA
fi

echo "  Decisions: $NEW_DECS new (DEC-$((LAST_DEC + 1)) to DEC-$LATEST_DEC)"
echo "  Issues: $NEW_ISSUES new (ISSUE-$((LAST_ISSUE + 1)) to ISSUE-$LATEST_ISSUE)"
echo "  Ideas: $NEW_IDEAS new (IDEA-$((LAST_IDEA + 1)) to IDEA-$LATEST_IDEA)"
echo ""

if [ "$NEW_DECS" -eq 0 ] && [ "$NEW_ISSUES" -eq 0 ] && [ "$NEW_IDEAS" -eq 0 ]; then
    echo "No new governance entries to sync."
    log "INFO" "No new entries to sync"
    update_state "$LATEST_DEC" "$LATEST_ISSUE" "$LATEST_IDEA"
    exit 0
fi

# Track sync progress for partial recovery
SYNCED_DEC=$LAST_DEC
SYNCED_ISSUE=$LAST_ISSUE
SYNCED_IDEA=$LAST_IDEA
TOTAL_SYNCED=0
TOTAL_FAILED=0

# Sync new decisions
if [ "$NEW_DECS" -gt 0 ]; then
    echo "Syncing $NEW_DECS new decisions..."
    for i in $(seq $((LAST_DEC + 1)) $LATEST_DEC); do
        DEC_ID="DEC-$(printf '%03d' $i)"
        SUMMARY=$(extract_title "$DEC_ID" "$GOVERNANCE_DIR/DECISIONS-SUMMARY.yaml")

        if [ -n "$SUMMARY" ]; then
            echo "  $DEC_ID: $SUMMARY"
            if curate_with_retry "Decision $DEC_ID: $SUMMARY. See DECISIONS-LOG.md for details."; then
                SYNCED_DEC=$i
                TOTAL_SYNCED=$((TOTAL_SYNCED + 1))
            else
                TOTAL_FAILED=$((TOTAL_FAILED + 1))
            fi
            sleep 2
        else
            log "WARN" "No title found for $DEC_ID"
            echo "  $DEC_ID: (no title found, skipping)"
        fi
    done
    # Save progress after decisions
    update_state "$SYNCED_DEC" "$SYNCED_ISSUE" "$SYNCED_IDEA"
    echo ""
fi

# Sync new issues
if [ "$NEW_ISSUES" -gt 0 ]; then
    echo "Syncing $NEW_ISSUES new issues..."
    for i in $(seq $((LAST_ISSUE + 1)) $LATEST_ISSUE); do
        ISSUE_ID="ISSUE-$(printf '%03d' $i)"
        SUMMARY=$(extract_title "$ISSUE_ID" "$GOVERNANCE_DIR/ISSUES-SUMMARY.yaml")

        if [ -n "$SUMMARY" ]; then
            echo "  $ISSUE_ID: $SUMMARY"
            if curate_with_retry "Issue $ISSUE_ID: $SUMMARY. See ISSUES-TRACKER.md for details."; then
                SYNCED_ISSUE=$i
                TOTAL_SYNCED=$((TOTAL_SYNCED + 1))
            else
                TOTAL_FAILED=$((TOTAL_FAILED + 1))
            fi
            sleep 2
        else
            log "WARN" "No title found for $ISSUE_ID"
            echo "  $ISSUE_ID: (no title found, skipping)"
        fi
    done
    # Save progress after issues
    update_state "$SYNCED_DEC" "$SYNCED_ISSUE" "$SYNCED_IDEA"
    echo ""
fi

# Sync new ideas
if [ "$NEW_IDEAS" -gt 0 ]; then
    echo "Syncing $NEW_IDEAS new ideas..."
    for i in $(seq $((LAST_IDEA + 1)) $LATEST_IDEA); do
        IDEA_ID="IDEA-$(printf '%03d' $i)"
        SUMMARY=$(extract_title "$IDEA_ID" "$GOVERNANCE_DIR/IDEAS-SUMMARY.yaml")

        if [ -n "$SUMMARY" ]; then
            echo "  $IDEA_ID: $SUMMARY"
            if curate_with_retry "Idea $IDEA_ID: $SUMMARY. See IDEAS-BACKLOG.md for details."; then
                SYNCED_IDEA=$i
                TOTAL_SYNCED=$((TOTAL_SYNCED + 1))
            else
                TOTAL_FAILED=$((TOTAL_FAILED + 1))
            fi
            sleep 2
        else
            log "WARN" "No title found for $IDEA_ID"
            echo "  $IDEA_ID: (no title found, skipping)"
        fi
    done
    echo ""
fi

# Final state update
update_state "$SYNCED_DEC" "$SYNCED_ISSUE" "$SYNCED_IDEA"

echo "=============================================="
echo "Governance Sync Complete!"
echo "=============================================="
echo ""
echo "Synced: $TOTAL_SYNCED entries ($TOTAL_FAILED failed)"
echo "State saved to: $SYNC_STATE_FILE"
echo "Log: $LOG_FILE"

log "INFO" "=== Sync complete: $TOTAL_SYNCED synced, $TOTAL_FAILED failed ==="

if [ "$TOTAL_FAILED" -gt 0 ]; then
    exit 1
fi
