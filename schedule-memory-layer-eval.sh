#!/bin/bash
# Memory Layer Evaluation Notification Scheduler
# Manages date-triggered and event-triggered notifications for IDEA-259
#
# Usage:
#   schedule-memory-layer-eval.sh setup          # Initial setup (at jobs + cron)
#   schedule-memory-layer-eval.sh check-triggers # Check event triggers and notify
#   schedule-memory-layer-eval.sh send-ready     # Send "ready to evaluate" notification
#   schedule-memory-layer-eval.sh send-reminder  # Send reminder notification
#   schedule-memory-layer-eval.sh status         # Show current schedule status
#
# Configuration:
#   NOTIFY_SCRIPT - Path to notify.sh
#   METRICS_SCRIPT - Path to memory-layer-metrics.sh
#   STATE_FILE - Tracks notification state to avoid duplicates

set -euo pipefail

# === Configuration ===
NOTIFY_SCRIPT="${NOTIFY_SCRIPT:-$HOME/bin/notify.sh}"
METRICS_SCRIPT="${METRICS_SCRIPT:-$HOME/dev/infrastructure/tools/memory-layer-metrics.sh}"
STATE_DIR="$HOME/.cache/memory-layer-eval"
STATE_FILE="$STATE_DIR/notification-state.json"
LOG_FILE="$STATE_DIR/scheduler.log"

# Notification dates
READY_DATE="2026-02-05"
REMINDER_DATE="2026-02-12"

# Ensure directories exist
mkdir -p "$STATE_DIR"

# === Helper Functions ===

timestamp() {
    date -Iseconds
}

log() {
    echo "[$(timestamp)] $*" >> "$LOG_FILE"
}

init_state() {
    if [[ ! -f "$STATE_FILE" ]]; then
        cat > "$STATE_FILE" << 'EOF'
{
    "ready_notification_sent": false,
    "reminder_notification_sent": false,
    "event_notifications": {},
    "last_check": null
}
EOF
        log "Initialized state file"
    fi
}

get_state() {
    local key="$1"
    jq -r ".$key // empty" "$STATE_FILE" 2>/dev/null || echo ""
}

set_state() {
    local key="$1"
    local value="$2"
    local tmp
    tmp=$(mktemp)
    jq ".$key = $value" "$STATE_FILE" > "$tmp" && mv "$tmp" "$STATE_FILE"
    log "State updated: $key = $value"
}

notify() {
    local title="$1"
    local message="$2"
    local priority="${3:-high}"
    local channel="${4:-both}"

    if [[ -x "$NOTIFY_SCRIPT" ]]; then
        "$NOTIFY_SCRIPT" "$title" "$message" --priority "$priority" --channel "$channel"
        log "Notification sent: $title"
        return 0
    else
        echo "ERROR: Notify script not found at $NOTIFY_SCRIPT" >&2
        log "ERROR: Notify script not found"
        return 1
    fi
}

# === Command Functions ===

setup() {
    echo "=== Memory Layer Evaluation Scheduler Setup ==="
    init_state

    # Check if 'at' command is available
    if ! command -v at &>/dev/null; then
        echo "WARNING: 'at' command not available. Using cron-only fallback."
        echo "Install 'at' for better scheduling: sudo apt install at"
        log "WARNING: at command not available"
    else
        echo "Scheduling date-triggered notifications..."

        # Schedule Feb 5 notification
        echo "$0 send-ready" | at 09:00 "$READY_DATE" 2>/dev/null && \
            echo "  Scheduled: Feb 5, 9 AM - Ready notification" || \
            echo "  WARNING: Could not schedule Feb 5 notification"

        # Schedule Feb 12 reminder
        echo "$0 send-reminder" | at 09:00 "$REMINDER_DATE" 2>/dev/null && \
            echo "  Scheduled: Feb 12, 9 AM - Reminder notification" || \
            echo "  WARNING: Could not schedule Feb 12 notification"
    fi

    echo ""
    echo "Cron entry for daily event trigger check:"
    echo "  0 9 * * * $0 check-triggers >> $LOG_FILE 2>&1"
    echo ""
    echo "To add to crontab, run:"
    echo "  (crontab -l 2>/dev/null; echo '0 9 * * * $0 check-triggers >> $LOG_FILE 2>&1') | crontab -"
    echo ""
    echo "Setup complete. Use 'schedule-memory-layer-eval.sh status' to verify."
    log "Setup completed"
}

check_triggers() {
    init_state
    log "Checking event triggers..."

    local triggers_fired=0
    local today
    today=$(date +%Y-%m-%d)

    # Check date-based triggers first (cron fallback for 'at' command)
    if [[ "$today" == "$READY_DATE" ]] || [[ "$today" > "$READY_DATE" ]]; then
        local ready_sent
        ready_sent=$(get_state "ready_notification_sent")
        if [[ "$ready_sent" != "true" ]]; then
            log "Date trigger: Ready date reached ($READY_DATE)"
            send_ready
            triggers_fired=$((triggers_fired + 1))
        fi
    fi

    if [[ "$today" == "$REMINDER_DATE" ]] || [[ "$today" > "$REMINDER_DATE" ]]; then
        local reminder_sent
        reminder_sent=$(get_state "reminder_notification_sent")
        if [[ "$reminder_sent" != "true" ]]; then
            log "Date trigger: Reminder date reached ($REMINDER_DATE)"
            send_reminder
            triggers_fired=$((triggers_fired + 1))
        fi
    fi

    # Check event-based triggers
    local trigger_results
    trigger_results=$("$METRICS_SCRIPT" check-triggers 2>&1) || true

    # Parse results and send notifications for any triggered events

    # Check ISSUE-140 trigger
    if echo "$trigger_results" | grep -q "TRIGGER FIRED: ISSUE-140"; then
        local key="issue_140_resolved"
        local sent
        sent=$(jq -r ".event_notifications.\"$key\" // false" "$STATE_FILE")
        if [[ "$sent" != "true" ]]; then
            notify "IDEA-259: Event Trigger Fired" \
                "ISSUE-140 (Runtime Context Erosion) has been resolved. Memory layer evaluation recommended. See IDEA-259 and MEMORY-LAYER-TOOLS-INVENTORY.md" \
                "high" "both"
            jq ".event_notifications.\"$key\" = true" "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
            triggers_fired=$((triggers_fired + 1))
        fi
    fi

    # Check handoff failure rate trigger
    if echo "$trigger_results" | grep -q "TRIGGER FIRED: Failure rate"; then
        local key="handoff_failure_rate"
        local sent
        sent=$(jq -r ".event_notifications.\"$key\" // false" "$STATE_FILE")
        if [[ "$sent" != "true" ]]; then
            notify "IDEA-259: Event Trigger Fired" \
                "Handoff failure rate exceeds 20%. Memory layer evaluation recommended to address session continuity issues. See IDEA-259." \
                "high" "both"
            jq ".event_notifications.\"$key\" = true" "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
            triggers_fired=$((triggers_fired + 1))
        fi
    fi

    # Check context loss trigger
    if echo "$trigger_results" | grep -q "TRIGGER FIRED:.*incidents"; then
        local key="context_loss_threshold"
        local sent
        sent=$(jq -r ".event_notifications.\"$key\" // false" "$STATE_FILE")
        if [[ "$sent" != "true" ]]; then
            notify "IDEA-259: Event Trigger Fired" \
                "3+ context loss incidents in last 7 days. Memory layer evaluation recommended. See IDEA-259 and record incidents with memory-layer-metrics.sh" \
                "high" "both"
            jq ".event_notifications.\"$key\" = true" "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
            triggers_fired=$((triggers_fired + 1))
        fi
    fi

    # Update last check timestamp
    jq ".last_check = \"$(timestamp)\"" "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"

    echo "$trigger_results"
    echo ""
    echo "Notifications sent this check: $triggers_fired"
    log "Trigger check complete. Notifications sent: $triggers_fired"
}

send_ready() {
    init_state

    local sent
    sent=$(get_state "ready_notification_sent")
    if [[ "$sent" == "true" ]]; then
        echo "Ready notification already sent. Skipping."
        log "Ready notification skipped (already sent)"
        return 0
    fi

    notify "IDEA-259: Memory Layer Evaluation Ready" \
        "NO-ADD period has expired (Feb 5). Memory layer tools evaluation can now begin. See IDEA-259, MEMORY-LAYER-TOOLS-INVENTORY.md, and MEMORY-LAYER-EVALUATION-CRITERIA.md" \
        "high" "both"

    set_state "ready_notification_sent" "true"
    echo "Ready notification sent."
}

send_reminder() {
    init_state

    local sent
    sent=$(get_state "reminder_notification_sent")
    if [[ "$sent" == "true" ]]; then
        echo "Reminder notification already sent. Skipping."
        log "Reminder notification skipped (already sent)"
        return 0
    fi

    notify "IDEA-259 Reminder: Memory Layer Evaluation" \
        "Memory layer tools evaluation has not yet started. See IDEA-259 and MEMORY-LAYER-TOOLS-INVENTORY.md to begin." \
        "medium" "ntfy"

    set_state "reminder_notification_sent" "true"
    echo "Reminder notification sent."
}

status() {
    echo "=== Memory Layer Evaluation Scheduler Status ==="
    echo ""

    init_state

    echo "State file: $STATE_FILE"
    if [[ -f "$STATE_FILE" ]]; then
        echo "Contents:"
        jq '.' "$STATE_FILE"
    else
        echo "  (not initialized)"
    fi

    echo ""
    echo "Scheduled at jobs:"
    if command -v atq &>/dev/null; then
        atq 2>/dev/null | head -10 || echo "  (none or unable to query)"
    else
        echo "  (at command not available)"
    fi

    echo ""
    echo "Cron entry check:"
    if crontab -l 2>/dev/null | grep -q "schedule-memory-layer-eval"; then
        crontab -l | grep "schedule-memory-layer-eval"
    else
        echo "  NOT FOUND - Add with setup command"
    fi

    echo ""
    echo "Current trigger status:"
    "$METRICS_SCRIPT" check-triggers 2>/dev/null || echo "  (unable to check)"
}

reset_state() {
    echo "Resetting notification state..."
    rm -f "$STATE_FILE"
    init_state
    echo "State reset complete."
    log "State reset by user"
}

# === Main ===

show_help() {
    cat << 'EOF'
Memory Layer Evaluation Notification Scheduler

Usage:
  schedule-memory-layer-eval.sh <command>

Commands:
  setup           Initial setup (schedules at jobs, shows cron entry)
  check-triggers  Check event triggers and send notifications if fired
  send-ready      Send "ready to evaluate" notification (Feb 5)
  send-reminder   Send reminder notification (Feb 12)
  status          Show current schedule and state
  reset           Reset notification state (allows re-sending)
  help            Show this help

Schedule:
  Date triggers:
    - Feb 5, 2026 @ 9 AM: Ready notification
    - Feb 12, 2026 @ 9 AM: Reminder notification

  Event triggers (checked daily via cron):
    - ISSUE-140 resolved
    - Handoff failure rate > 20%
    - 3+ context loss incidents in 7 days

Related:
  IDEA-259 (Memory Layer Tools Evaluation)
  memory-layer-metrics.sh (metrics collection)
  ~/bin/notify.sh (notification delivery)
EOF
}

case "${1:-help}" in
    setup)
        setup
        ;;
    check-triggers)
        check_triggers
        ;;
    send-ready)
        send_ready
        ;;
    send-reminder)
        send_reminder
        ;;
    status)
        status
        ;;
    reset)
        reset_state
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: $1" >&2
        echo "Use 'schedule-memory-layer-eval.sh help' for usage" >&2
        exit 1
        ;;
esac
