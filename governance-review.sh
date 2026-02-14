#!/usr/bin/env bash
# governance-review.sh — Weekly governance staleness checker (IDEA-382)
#
# Scans DECISIONS-LOG.md, ISSUES-TRACKER.md, IDEAS-BACKLOG.md
# Flags items not reviewed/updated within threshold:
#   HIGH/CRITICAL severity: 30 days
#   MEDIUM: 60 days
#   LOW: 90 days
#
# Usage:
#   governance-review.sh              # Show all item counts
#   governance-review.sh --stale      # Show only stale items
#   governance-review.sh --deps       # Include downstream reference counts
#   governance-review.sh --output FILE  # Write results to FILE
#   governance-review.sh --notify     # Send notification based on health status
#
# Header formats parsed:
#   DEC:   ### DEC-NNN | YYYY-MM-DD | CATEGORY | STATUS | Title
#   ISSUE: ### ISSUE-NNN | YYYY-MM-DD | STATUS | SEVERITY | Title
#   IDEA:  ### IDEA-NNN: Title  (date from **Source:** body field)
#          ### IDEA-NNN | YYYY-MM-DD | Title  (20 items with header date)
#
# Date parsing uses GNU date (-d flag). Requires: bash 4+, GNU coreutils.
#
# Exit codes (DEC-102 evaluation pipeline):
#   0 = healthy (0 stale items)
#   1 = warning (1-5 stale items)
#   2 = critical (>5 stale items OR any HIGH/CRITICAL severity stale)

set -euo pipefail

DOCS_DIR="${HOME}/dev/infrastructure/dev-env-docs"
DECISIONS_FILE="${DOCS_DIR}/DECISIONS-LOG.md"
ISSUES_FILE="${DOCS_DIR}/ISSUES-TRACKER.md"
IDEAS_FILE="${DOCS_DIR}/IDEAS-BACKLOG.md"
TODAY_EPOCH=$(date +%s)
METRICS_FILE="${HOME}/.metrics/governance-review-events.jsonl"
NOTIFY_BIN="${HOME}/bin/notify.sh"

# Defaults
SHOW_STALE_ONLY=false
SHOW_DEPS=false
OUTPUT_FILE=""
SEND_NOTIFICATION=false
RUN_TRIGGER="manual"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --stale)   SHOW_STALE_ONLY=true; shift ;;
        --deps)    SHOW_DEPS=true; shift ;;
        --output)  OUTPUT_FILE="$2"; shift 2 ;;
        --notify)  SEND_NOTIFICATION=true; shift ;;
        --trigger) RUN_TRIGGER="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: governance-review.sh [--stale] [--deps] [--output FILE] [--notify] [--trigger TYPE]"
            echo "  --stale    Show only stale items (past threshold)"
            echo "  --deps     Include downstream reference counts"
            echo "  --output   Write markdown output to FILE"
            echo "  --notify   Send notification based on health status"
            echo "  --trigger  Set run trigger (cron/manual/event) for logging"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Verify files exist
for f in "$DECISIONS_FILE" "$ISSUES_FILE" "$IDEAS_FILE"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: Missing governance file: $f" >&2
        exit 1
    fi
done

# Calculate age in days from a YYYY-MM-DD date string
# Returns empty string if date is unparseable
age_days() {
    local date_str="$1"
    local item_epoch
    item_epoch=$(date -d "$date_str" +%s 2>/dev/null) || return 1
    echo $(( (TODAY_EPOCH - item_epoch) / 86400 ))
}

# Check if an item is stale based on age and severity
# Returns 0 (stale) or 1 (not stale)
is_stale() {
    local age="$1"
    local severity="$2"
    case "$severity" in
        CRITICAL|HIGH) [[ "$age" -gt 30 ]] && return 0 ;;
        MEDIUM)        [[ "$age" -gt 60 ]] && return 0 ;;
        LOW|*)         [[ "$age" -gt 90 ]] && return 0 ;;
    esac
    return 1
}

# Count downstream references to an item ID across governance files
count_refs() {
    local item_id="$1"
    local count=0
    for f in "$DECISIONS_FILE" "$ISSUES_FILE" "$IDEAS_FILE"; do
        local c
        c=$(grep -c "$item_id" "$f" 2>/dev/null || true)
        # Subtract 1 for the item's own header
        if [[ "$c" -gt 1 ]]; then
            count=$((count + c - 1))
        fi
    done
    echo "$count"
}

# Collect results
declare -a RESULTS=()
stale_count=0
total_count=0

# ─── Parse DECISIONS ─────────────────────────────────────────────
# Format: ### DEC-NNN | YYYY-MM-DD | CATEGORY | STATUS | Title
while IFS='|' read -r id_part date_part cat_part status_part title_part; do
    item_id=$(echo "$id_part" | grep -oE "DEC-[0-9]+" || continue)
    item_date=$(echo "$date_part" | tr -d ' ')
    item_status=$(echo "$status_part" | tr -d ' ')
    item_title=$(echo "$title_part" | sed 's/^ *//')

    # Skip terminal statuses
    case "$item_status" in
        REJECTED|SUPERSEDED|DEPRECATED) continue ;;
    esac

    days=$(age_days "$item_date") || continue
    total_count=$((total_count + 1))

    # DECs don't have explicit severity — use MEDIUM as default
    severity="MEDIUM"
    stale_flag=""
    if is_stale "$days" "$severity"; then
        stale_flag="STALE"
        stale_count=$((stale_count + 1))
    fi

    if [[ "$SHOW_STALE_ONLY" == "true" && "$stale_flag" != "STALE" ]]; then
        continue
    fi

    refs=""
    if [[ "$SHOW_DEPS" == "true" ]]; then
        refs=" | refs: $(count_refs "$item_id")"
    fi

    RESULTS+=("| ${item_id} | ${item_date} | ${days}d | ${severity} | ${stale_flag:-ok} | ${item_status} | ${item_title}${refs} |")
done < <(grep -E "^### DEC-[0-9]+ \| [0-9]{4}-[0-9]{2}-[0-9]{2}" "$DECISIONS_FILE")

# ─── Parse ISSUES ────────────────────────────────────────────────
# Format: ### ISSUE-NNN | YYYY-MM-DD | STATUS | SEVERITY | Title
while IFS='|' read -r id_part date_part status_part sev_part title_part; do
    item_id=$(echo "$id_part" | grep -oE "ISSUE-[0-9]+" || continue)
    item_date=$(echo "$date_part" | tr -d ' ')
    item_status=$(echo "$status_part" | tr -d ' ')
    severity=$(echo "$sev_part" | tr -d ' ')
    item_title=$(echo "$title_part" | sed 's/^ *//')

    # Skip resolved/closed
    case "$item_status" in
        RESOLVED|WONT_FIX|PARTIALLY_RESOLVED) continue ;;
    esac

    days=$(age_days "$item_date") || continue
    total_count=$((total_count + 1))

    stale_flag=""
    if is_stale "$days" "$severity"; then
        stale_flag="STALE"
        stale_count=$((stale_count + 1))
    fi

    if [[ "$SHOW_STALE_ONLY" == "true" && "$stale_flag" != "STALE" ]]; then
        continue
    fi

    refs=""
    if [[ "$SHOW_DEPS" == "true" ]]; then
        refs=" | refs: $(count_refs "$item_id")"
    fi

    RESULTS+=("| ${item_id} | ${item_date} | ${days}d | ${severity} | ${stale_flag:-ok} | ${item_status} | ${item_title}${refs} |")
done < <(grep -E "^### ISSUE-[0-9]+ \| [0-9]{4}-[0-9]{2}-[0-9]{2}" "$ISSUES_FILE")

# ─── Parse IDEAS ─────────────────────────────────────────────────
# Two formats:
#   With date:    ### IDEA-NNN | YYYY-MM-DD | Title
#   Without date: ### IDEA-NNN: Title  (date from **Source:** line)

# First: IDEAs with dates in header
while IFS='|' read -r id_part date_part title_part; do
    item_id=$(echo "$id_part" | grep -oE "IDEA-[0-9]+" || continue)
    item_date=$(echo "$date_part" | tr -d ' ')
    item_title=$(echo "$title_part" | sed 's/^ *//')

    days=$(age_days "$item_date") || continue
    total_count=$((total_count + 1))

    # Try to find Priority in body (search next 10 lines after header)
    line_num=$(grep -nE "^### ${item_id} \|" "$IDEAS_FILE" | head -1 | cut -d: -f1)
    severity="LOW"  # default for IDEAS
    if [[ -n "$line_num" ]]; then
        body_priority=$(sed -n "$((line_num+1)),$((line_num+10))p" "$IDEAS_FILE" | grep -oiE '\*\*Priority:\*\* *(CRITICAL|HIGH|MEDIUM|LOW)' | grep -oiE 'CRITICAL|HIGH|MEDIUM|LOW' | head -1 || true)
        if [[ -n "$body_priority" ]]; then
            severity=$(echo "$body_priority" | tr '[:lower:]' '[:upper:]')
        fi
    fi

    stale_flag=""
    if is_stale "$days" "$severity"; then
        stale_flag="STALE"
        stale_count=$((stale_count + 1))
    fi

    if [[ "$SHOW_STALE_ONLY" == "true" && "$stale_flag" != "STALE" ]]; then
        continue
    fi

    refs=""
    if [[ "$SHOW_DEPS" == "true" ]]; then
        refs=" | refs: $(count_refs "$item_id")"
    fi

    RESULTS+=("| ${item_id} | ${item_date} | ${days}d | ${severity} | ${stale_flag:-ok} | IDEA | ${item_title}${refs} |")
done < <(grep -E "^### IDEA-[0-9]+ \| [0-9]{4}-[0-9]{2}-[0-9]{2}" "$IDEAS_FILE")

# Second: IDEAs without header dates — extract from **Source:** field
while IFS=: read -r line_num rest; do
    item_id=$(echo "$rest" | grep -oE "IDEA-[0-9]+" || continue)

    # Skip if we already processed this IDEA (had date in header)
    if echo "${RESULTS[*]:-}" | grep -q "$item_id"; then
        continue
    fi

    item_title=$(echo "$rest" | sed 's/^### IDEA-[0-9]*: *//')

    # Search body (next 15 lines) for **Source:** with a date
    source_date=$(sed -n "$((line_num+1)),$((line_num+15))p" "$IDEAS_FILE" | grep -oE '\*\*Source:\*\* *[0-9]{4}-[0-9]{2}-[0-9]{2}' | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1 || true)

    if [[ -z "$source_date" ]]; then
        # No date found — skip (can't calculate staleness)
        continue
    fi

    item_date="$source_date"
    days=$(age_days "$item_date") || continue
    total_count=$((total_count + 1))

    # Extract priority from body
    severity="LOW"
    body_priority=$(sed -n "$((line_num+1)),$((line_num+10))p" "$IDEAS_FILE" | grep -oiE '\*\*Priority:\*\* *(CRITICAL|HIGH|MEDIUM|LOW)' | grep -oiE 'CRITICAL|HIGH|MEDIUM|LOW' | head -1 || true)
    if [[ -n "$body_priority" ]]; then
        severity=$(echo "$body_priority" | tr '[:lower:]' '[:upper:]')
    fi

    stale_flag=""
    if is_stale "$days" "$severity"; then
        stale_flag="STALE"
        stale_count=$((stale_count + 1))
    fi

    if [[ "$SHOW_STALE_ONLY" == "true" && "$stale_flag" != "STALE" ]]; then
        continue
    fi

    refs=""
    if [[ "$SHOW_DEPS" == "true" ]]; then
        refs=" | refs: $(count_refs "$item_id")"
    fi

    RESULTS+=("| ${item_id} | ${item_date} | ${days}d | ${severity} | ${stale_flag:-ok} | IDEA | ${item_title}${refs} |")
done < <(grep -nE "^### IDEA-[0-9]+:" "$IDEAS_FILE")

# ─── Log Event to JSONL ──────────────────────────────────────────
log_event() {
    mkdir -p "$(dirname "$METRICS_FILE")"

    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local stale_items=()
    local critical_stale=false

    # Extract stale item IDs and check for HIGH/CRITICAL severity
    if [[ ${#RESULTS[@]} -gt 0 ]]; then
        for result in "${RESULTS[@]}"; do
            if echo "$result" | grep -q "STALE"; then
                local item_id=$(echo "$result" | cut -d'|' -f2 | tr -d ' ')
                local severity=$(echo "$result" | cut -d'|' -f5 | tr -d ' ')
                stale_items+=("$item_id")

                if [[ "$severity" == "CRITICAL" || "$severity" == "HIGH" ]]; then
                    critical_stale=true
                fi
            fi
        done
    fi

    # Convert array to JSON array
    local stale_json="[]"
    if [[ ${#stale_items[@]} -gt 0 ]]; then
        stale_json=$(printf '%s\n' "${stale_items[@]}" | jq -R . | jq -s .)
    fi

    # Create JSONL event (compact format)
    jq -nc \
        --arg ts "$timestamp" \
        --arg trigger "$RUN_TRIGGER" \
        --argjson total "$total_count" \
        --argjson stale "$stale_count" \
        --argjson items "$stale_json" \
        --argjson critical "$critical_stale" \
        '{
            timestamp: $ts,
            event: "governance_review",
            run_trigger: $trigger,
            total_scanned: $total,
            stale_count: $stale,
            stale_items: $items,
            critical_stale: $critical
        }' >> "$METRICS_FILE"
}

# ─── Send Notification ───────────────────────────────────────────
send_notification() {
    local priority="low"
    local message=""

    # Determine priority based on health criteria
    if [[ $stale_count -eq 0 ]]; then
        priority="low"
        message="✅ Healthy: ${total_count} items scanned, 0 stale"
    elif [[ $stale_count -le 5 ]]; then
        priority="medium"
        message="⚠️  Warning: ${stale_count} stale items found (${total_count} total scanned)"
    else
        priority="high"
        message="🚨 Critical: ${stale_count} stale items found (${total_count} total scanned)"
    fi

    # Check for HIGH/CRITICAL severity stale items
    local has_critical=false
    if [[ ${#RESULTS[@]} -gt 0 ]]; then
        for result in "${RESULTS[@]}"; do
            if echo "$result" | grep -q "STALE"; then
                local severity=$(echo "$result" | cut -d'|' -f5 | tr -d ' ')
                if [[ "$severity" == "CRITICAL" || "$severity" == "HIGH" ]]; then
                    has_critical=true
                    priority="high"
                    message="🚨 Critical: ${stale_count} stale items (including HIGH/CRITICAL severity)"
                    break
                fi
            fi
        done
    fi

    if [[ -x "$NOTIFY_BIN" ]]; then
        "$NOTIFY_BIN" "Governance Review" "$message" --priority "$priority" --channel auto 2>/dev/null || true
    fi
}

# ─── Generate Output ─────────────────────────────────────────────
generate_output() {
    echo "# Governance Review Queue"
    echo ""
    echo "Generated: $(date '+%Y-%m-%d %H:%M')"
    echo "Thresholds: HIGH/CRITICAL=30d, MEDIUM=60d, LOW=90d"
    echo ""
    echo "**Summary:** ${total_count} active items scanned, ${stale_count} stale"
    echo ""

    if [[ ${#RESULTS[@]} -eq 0 ]]; then
        if [[ "$SHOW_STALE_ONLY" == "true" ]]; then
            echo "No stale items found."
        else
            echo "No items found."
        fi
        return
    fi

    # Table header
    if [[ "$SHOW_DEPS" == "true" ]]; then
        echo "| Item | Date | Age | Severity | Status | Type | Title | Refs |"
        echo "|------|------|-----|----------|--------|------|-------|------|"
    else
        echo "| Item | Date | Age | Severity | Status | Type | Title |"
        echo "|------|------|-----|----------|--------|------|-------|"
    fi

    # Sort by staleness (STALE first), then by age descending
    printf '%s\n' "${RESULTS[@]}" | sort -t'|' -k6,6r -k4,4nr
    echo ""

    if [[ "$stale_count" -gt 0 ]]; then
        echo "## Action Required"
        echo ""
        echo "${stale_count} items exceed their staleness threshold."
        echo "Review each STALE item and either:"
        echo "1. Update the item (confirm it's still relevant)"
        echo "2. Close/resolve it (if no longer needed)"
        echo "3. Change its priority (if threshold is wrong)"
    fi
}

if [[ -n "$OUTPUT_FILE" ]]; then
    generate_output > "$OUTPUT_FILE"
    echo "Review queue written to: $OUTPUT_FILE"
    echo "Summary: ${total_count} active items, ${stale_count} stale"
else
    generate_output
fi

# ─── Log Event & Send Notification ──────────────────────────────
log_event

if [[ "$SEND_NOTIFICATION" == "true" ]]; then
    send_notification
fi

# ─── Exit with appropriate status code ──────────────────────────
# Determine health status and exit code
has_critical_stale=false
if [[ ${#RESULTS[@]} -gt 0 ]]; then
    for result in "${RESULTS[@]}"; do
        if echo "$result" | grep -q "STALE"; then
            local severity=$(echo "$result" | cut -d'|' -f5 | tr -d ' ')
            if [[ "$severity" == "CRITICAL" || "$severity" == "HIGH" ]]; then
                has_critical_stale=true
                break
            fi
        fi
    done
fi

if [[ $stale_count -eq 0 ]]; then
    exit 0  # Healthy
elif [[ $stale_count -le 5 && "$has_critical_stale" == "false" ]]; then
    exit 1  # Warning
else
    exit 2  # Critical
fi
