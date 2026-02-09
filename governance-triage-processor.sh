#!/usr/bin/env bash
#
# Governance Triage Queue Processor
#
# Processes detected capabilities from PostToolUse hook queue and sends
# governance prompts via notify.sh. Part of Autonomous Capability Governance
# Triage (IDEA-324).
#
# Features (2026 best practices):
# - Async queue-based processing
# - Dead Letter Queue (DLQ) for failed notifications
# - Time-to-Live (TTL): 30 days for resolved, 90 days for DLQ
# - Retry logic: 3 attempts with escalation
# - Atomic queue operations with flock
#
# Usage: Run via cron every 15 minutes
#   */15 * * * * ~/dev/infrastructure/tools/governance-triage-processor.sh
#
# Review DLQ entries:
#   jq . ~/.claude/queues/governance-triage-dlq.jsonl
#
# Related: IDEA-324, Opus v2.1.2 optimizations

set -euo pipefail

QUEUE_FILE="$HOME/.claude/queues/governance-triage.jsonl"
DLQ_FILE="$HOME/.claude/queues/governance-triage-dlq.jsonl"
QUARANTINE_FILE="$HOME/.claude/queues/governance-triage-quarantine.jsonl"
LOCK_FILE="$HOME/.claude/locks/governance-triage.lock"
INVENTORY="$HOME/dev/infrastructure/dev-env-docs/CAPABILITIES-INVENTORY.md"
METRICS_SCRIPT="$HOME/dev/infrastructure/dev-env-docs/scripts/governance-triage-metrics.sh"
MAX_QUEUE_SIZE=10485760  # 10MB
DLQ_TTL_DAYS=90  # DLQ entries expire after 90 days

# Ensure queue and lock directories exist
mkdir -p "$(dirname "$QUEUE_FILE")"
mkdir -p "$(dirname "$LOCK_FILE")"

# Lock to prevent concurrent execution
exec 200>"$LOCK_FILE"
flock -n 200 || { echo "Already running"; exit 0; }

# BLOCKER FIX #4: Queue size safeguard
if [[ -f "$QUEUE_FILE" ]]; then
    queue_size=$(stat -c%s "$QUEUE_FILE" 2>/dev/null || stat -f%z "$QUEUE_FILE" 2>/dev/null || echo 0)
    if [ "$queue_size" -gt "$MAX_QUEUE_SIZE" ]; then
        ~/bin/notify.sh \
            "Governance Queue Overflow" \
            "Queue size: $queue_size bytes (max: $MAX_QUEUE_SIZE). Manual intervention required." \
            --priority urgent \
            --channel both
        "$METRICS_SCRIPT" record queue_overflow '{"size": '"$queue_size"'}'
        exit 1
    fi
else
    queue_size=0
fi

# Exit early if queue doesn't exist or is empty
if [[ ! -f "$QUEUE_FILE" ]] || [[ $queue_size -eq 0 ]]; then
    echo "Queue empty or not found"
    exit 0
fi

# HIGH FIX (Codex #3): Validate and quarantine corrupted queue lines
# Prevents one malformed line from stopping entire processor
quarantined_count=0
temp_clean_queue=$(mktemp)

while IFS= read -r line; do
    # Skip empty lines
    if [[ -z "$line" ]]; then
        continue
    fi

    # Validate JSON structure
    if echo "$line" | jq -e . >/dev/null 2>&1; then
        # Valid JSON - keep it
        echo "$line" >> "$temp_clean_queue"
    else
        # Invalid JSON - quarantine it
        echo "$line" >> "$QUARANTINE_FILE"
        ((quarantined_count++))
    fi
done < "$QUEUE_FILE"

# Replace queue with clean version if any lines were quarantined
if [ "$quarantined_count" -gt 0 ]; then
    mv "$temp_clean_queue" "$QUEUE_FILE"
    "$METRICS_SCRIPT" record queue_quarantine '{"quarantined": '"$quarantined_count"'}' || true
    echo "Quarantined $quarantined_count malformed entries"
else
    rm "$temp_clean_queue"
fi

# Process queue entries
processed_count=0
failed_count=0

while IFS= read -r entry; do
    file_path=$(echo "$entry" | jq -r '.file_path')
    cap_type=$(echo "$entry" | jq -r '.capability_type')
    retry_count=$(echo "$entry" | jq -r '.retry_count // 0')

    # Check if already in inventory
    if grep -q "$file_path" "$INVENTORY"; then
        echo "Already registered: $file_path"
        "$METRICS_SCRIPT" record already_registered '{"file_path": "'"$file_path"'"}'
        continue
    fi

    # BLOCKER FIX #1: Notification failure handling with retry
    if ~/bin/notify.sh \
        "Capability Governance Triage" \
        "New $cap_type detected: $file_path

Respond with: governance-respond $file_path yes/no" \
        --priority high \
        --channel auto; then

        # Success: Update status in-place
        temp_queue=$(mktemp)
        jq -c --arg path "$file_path" \
            'if .file_path == $path then . + {status: "notified", notified_at: (now | todate)} else . end' \
            "$QUEUE_FILE" > "$temp_queue"
        mv "$temp_queue" "$QUEUE_FILE"

        ((processed_count++))
        "$METRICS_SCRIPT" record notification_sent '{"file_path": "'"$file_path"'", "capability_type": "'"$cap_type"'"}'
    else
        # Failure: Update retry_count in-place (Opus OPT-2: single queue)
        ((failed_count++))
        new_retry_count=$((retry_count + 1))

        temp_queue=$(mktemp)
        if [ "$new_retry_count" -le 3 ]; then
            # Update for retry
            jq -c --arg path "$file_path" --arg count "$new_retry_count" \
                'if .file_path == $path then . + {status: "notification_failed", retry_count: ($count | tonumber), last_retry: (now | todate)} else . end' \
                "$QUEUE_FILE" > "$temp_queue"
            mv "$temp_queue" "$QUEUE_FILE"
            "$METRICS_SCRIPT" record notification_retry '{"file_path": "'"$file_path"'", "retry_count": '"$new_retry_count"'}'
        else
            # Move to DLQ after 3 retries (2026 best practice: Dead Letter Queue)
            dlq_entry=$(jq -c --arg path "$file_path" \
                'select(.file_path == $path) | . + {status: "escalated", escalated_at: (now | todate), moved_to_dlq: (now | todate)}' \
                "$QUEUE_FILE")

            # Append to DLQ
            echo "$dlq_entry" >> "$DLQ_FILE"

            # Remove from main queue
            jq -c --arg path "$file_path" \
                'select(.file_path != $path)' \
                "$QUEUE_FILE" > "$temp_queue"
            mv "$temp_queue" "$QUEUE_FILE"

            ~/bin/notify.sh \
                "Governance Notification Failure" \
                "Failed to notify for $file_path after 3 retries. Moved to DLQ. Review with: jq 'select(.file_path == \"$file_path\")' ~/.claude/queues/governance-triage-dlq.jsonl" \
                --priority urgent \
                --channel both
            "$METRICS_SCRIPT" record notification_failed '{"file_path": "'"$file_path"'", "retry_count": '"$new_retry_count"', "moved_to_dlq": true}'
        fi
    fi

done < <(jq -c 'select(.status == "pending_evaluation" or .status == "notification_failed")' "$QUEUE_FILE")

# Cleanup: Remove only RESOLVED entries older than 30 days (Opus MED-2: escalate unresolved, don't delete)
thirty_days_ago=$(date -d "30 days ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -v-30d +"%Y-%m-%dT%H:%M:%SZ")
jq -c --arg cutoff "$thirty_days_ago" \
    'select(
        (.timestamp > $cutoff) or
        (.status != "evaluated" and .status != "already_registered")
    )' \
    "$QUEUE_FILE" > "$QUEUE_FILE.tmp"
mv "$QUEUE_FILE.tmp" "$QUEUE_FILE"

# Escalate old unresolved items (30+ days in pending/notified status)
old_unresolved=$(jq -c --arg cutoff "$thirty_days_ago" \
    'select(
        (.timestamp < $cutoff) and
        (.status == "pending_evaluation" or .status == "notified")
    )' \
    "$QUEUE_FILE" | wc -l)

if [ "$old_unresolved" -gt 0 ]; then
    ~/bin/notify.sh \
        "Governance Triage: Stale Items" \
        "$old_unresolved capabilities detected 30+ days ago still awaiting decision. Run: jq '.status==\"pending_evaluation\" or .status==\"notified\"' ~/.claude/queues/governance-triage.jsonl" \
        --priority high \
        --channel auto
fi

# DLQ cleanup: Remove entries older than TTL (2026 best practice)
if [[ -f "$DLQ_FILE" ]]; then
    dlq_ttl_cutoff=$(date -d "$DLQ_TTL_DAYS days ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -v-${DLQ_TTL_DAYS}d +"%Y-%m-%dT%H:%M:%SZ")
    jq -c --arg cutoff "$dlq_ttl_cutoff" \
        'select(.moved_to_dlq > $cutoff)' \
        "$DLQ_FILE" > "$DLQ_FILE.tmp" 2>/dev/null || true

    if [[ -f "$DLQ_FILE.tmp" ]]; then
        mv "$DLQ_FILE.tmp" "$DLQ_FILE"
    fi
fi

# Record metrics
dlq_count=$(if [[ -f "$DLQ_FILE" ]]; then wc -l < "$DLQ_FILE"; else echo 0; fi)
"$METRICS_SCRIPT" record processor_cycle '{"processed": '"$processed_count"', "failed": '"$failed_count"', "queue_size": '"$queue_size"', "dlq_count": '"$dlq_count"'}'

echo "Processed: $processed_count, Failed: $failed_count, DLQ: $dlq_count"
