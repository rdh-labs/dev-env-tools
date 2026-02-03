#!/bin/bash
# Memory Layer Evaluation Metrics Collection
# Tracks events relevant to IDEA-259 evaluation triggers
#
# Usage:
#   memory-layer-metrics.sh record-context-loss "description"
#   memory-layer-metrics.sh record-handoff <success|failure> "notes"
#   memory-layer-metrics.sh check-triggers
#   memory-layer-metrics.sh summary
#
# Configuration:
#   METRICS_LOG - Log file path (default: ~/.cache/memory-layer-eval/metrics.jsonl)
#   ISSUES_TRACKER - Path to ISSUES-TRACKER.md
#   HANDOFF_METRICS - Path to handoff metrics log

set -euo pipefail

# === Configuration ===
METRICS_LOG="${METRICS_LOG:-$HOME/.cache/memory-layer-eval/metrics.jsonl}"
ISSUES_TRACKER="${ISSUES_TRACKER:-$HOME/dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md}"
HANDOFF_METRICS="${HANDOFF_METRICS:-$HOME/.metrics/handoff-events.jsonl}"
NOTIFY_SCRIPT="${NOTIFY_SCRIPT:-$HOME/bin/notify.sh}"

# Ensure log directory exists
mkdir -p "$(dirname "$METRICS_LOG")"

# === Helper Functions ===

timestamp() {
    date -Iseconds
}

log_event() {
    local event_type="$1"
    local details="$2"
    local ts
    ts=$(timestamp)

    echo "{\"timestamp\":\"${ts}\",\"type\":\"${event_type}\",\"details\":\"${details}\"}" >> "$METRICS_LOG"
}

# === Command Functions ===

record_context_loss() {
    local description="${1:-No description provided}"
    log_event "context_loss" "$description"
    echo "Recorded context loss incident: $description"
}

record_handoff() {
    local status="${1:-unknown}"
    local notes="${2:-}"
    log_event "handoff_${status}" "$notes"
    echo "Recorded handoff $status: $notes"
}

check_issue_140_status() {
    # Check if ISSUE-140 is resolved
    if [[ -f "$ISSUES_TRACKER" ]]; then
        if grep -q "ISSUE-140.*RESOLVED\|ISSUE-140.*Resolved" "$ISSUES_TRACKER"; then
            echo "RESOLVED"
            return 0
        else
            echo "OPEN"
            return 1
        fi
    else
        echo "UNKNOWN"
        return 2
    fi
}

check_handoff_failure_rate() {
    # Calculate handoff failure rate from last 7 days
    local threshold="${1:-20}"

    if [[ ! -f "$HANDOFF_METRICS" ]]; then
        echo "0"
        return 1
    fi

    local week_ago
    week_ago=$(date -d "7 days ago" +%Y-%m-%d)

    local total failed rate
    total=$(grep -c "\"status\"" "$HANDOFF_METRICS" 2>/dev/null || echo "0")
    failed=$(grep -c "\"status\":\"incomplete\"\|\"status\":\"failed\"" "$HANDOFF_METRICS" 2>/dev/null || echo "0")

    if [[ "$total" -eq 0 ]]; then
        echo "0"
        return 1
    fi

    rate=$((failed * 100 / total))
    echo "$rate"

    if [[ "$rate" -gt "$threshold" ]]; then
        return 0  # Trigger: failure rate exceeded
    else
        return 1  # No trigger
    fi
}

check_context_loss_count() {
    # Count context loss incidents in last 7 days
    local threshold="${1:-3}"

    if [[ ! -f "$METRICS_LOG" ]]; then
        echo "0"
        return 1
    fi

    local week_ago count
    week_ago=$(date -d "7 days ago" -Iseconds)

    # Count context_loss events in last 7 days
    count=$(grep '"type":"context_loss"' "$METRICS_LOG" 2>/dev/null | \
            jq -r --arg since "$week_ago" 'select(.timestamp > $since)' 2>/dev/null | \
            wc -l || echo "0")

    echo "$count"

    if [[ "$count" -ge "$threshold" ]]; then
        return 0  # Trigger: too many context loss incidents
    else
        return 1  # No trigger
    fi
}

check_triggers() {
    echo "=== Memory Layer Evaluation Trigger Check ==="
    echo "Timestamp: $(timestamp)"
    echo ""

    local triggers_fired=0

    # Trigger 1: ISSUE-140 Resolved
    echo -n "1. ISSUE-140 Status: "
    local issue_status
    issue_status=$(check_issue_140_status)
    echo "$issue_status"
    if [[ "$issue_status" == "RESOLVED" ]]; then
        echo "   >>> TRIGGER FIRED: ISSUE-140 resolved"
        triggers_fired=$((triggers_fired + 1))
    fi

    # Trigger 2: Handoff Failure Rate
    echo -n "2. Handoff Failure Rate (7d): "
    local failure_rate
    failure_rate=$(check_handoff_failure_rate 20 || true)
    echo "${failure_rate}%"
    if [[ "$failure_rate" -gt 20 ]]; then
        echo "   >>> TRIGGER FIRED: Failure rate ${failure_rate}% > 20%"
        triggers_fired=$((triggers_fired + 1))
    fi

    # Trigger 3: Context Loss Incidents
    echo -n "3. Context Loss Incidents (7d): "
    local loss_count
    loss_count=$(check_context_loss_count 3 || true)
    echo "$loss_count"
    if [[ "$loss_count" -ge 3 ]]; then
        echo "   >>> TRIGGER FIRED: ${loss_count} incidents >= 3"
        triggers_fired=$((triggers_fired + 1))
    fi

    echo ""
    echo "=== Summary ==="
    echo "Triggers fired: $triggers_fired"

    if [[ "$triggers_fired" -gt 0 ]]; then
        echo "ACTION: Memory layer evaluation recommended (IDEA-259)"
        return 0
    else
        echo "STATUS: No triggers active"
        return 1
    fi
}

summary() {
    echo "=== Memory Layer Metrics Summary ==="
    echo "Metrics log: $METRICS_LOG"
    echo ""

    if [[ ! -f "$METRICS_LOG" ]]; then
        echo "No metrics recorded yet."
        return
    fi

    echo "Total events: $(wc -l < "$METRICS_LOG")"
    echo ""

    echo "Events by type:"
    jq -r '.type' "$METRICS_LOG" 2>/dev/null | sort | uniq -c | sort -rn || echo "  (unable to parse)"

    echo ""
    echo "Last 5 events:"
    tail -5 "$METRICS_LOG" | jq -c '.' 2>/dev/null || tail -5 "$METRICS_LOG"

    echo ""
    echo "=== Current Trigger Status ==="
    check_triggers || true
}

# === Main ===

show_help() {
    cat << 'EOF'
Memory Layer Evaluation Metrics

Usage:
  memory-layer-metrics.sh <command> [args]

Commands:
  record-context-loss "description"   Record a context loss incident
  record-handoff <success|failure>    Record handoff result
  check-triggers                      Check if any event triggers are fired
  summary                             Display metrics summary
  help                                Show this help

Examples:
  memory-layer-metrics.sh record-context-loss "Lost context mid-session"
  memory-layer-metrics.sh record-handoff failure "Sandbox permission error"
  memory-layer-metrics.sh check-triggers
  memory-layer-metrics.sh summary

Event Triggers (IDEA-259):
  1. ISSUE-140 (Runtime Context Erosion) marked RESOLVED
  2. Handoff failure rate > 20% in last 7 days
  3. 3+ context loss incidents in last 7 days

Related:
  IDEA-259 (Memory Layer Tools Evaluation)
  schedule-memory-layer-eval.sh (notification scheduler)
EOF
}

case "${1:-help}" in
    record-context-loss)
        record_context_loss "${2:-}"
        ;;
    record-handoff)
        record_handoff "${2:-}" "${3:-}"
        ;;
    check-triggers)
        check_triggers
        ;;
    summary)
        summary
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: $1" >&2
        echo "Use 'memory-layer-metrics.sh help' for usage" >&2
        exit 1
        ;;
esac
