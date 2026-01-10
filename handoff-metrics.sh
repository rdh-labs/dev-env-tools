#!/usr/bin/env bash
# Minimal metrics helper for the Session Handoff System
#
# Modes:
#   handoff-metrics.sh record --agent NAME --project PATH --status complete|incomplete
#   handoff-metrics.sh summary
#   handoff-metrics.sh check-thresholds [--config FILE]
#
# Events are stored in: ~/.metrics/handoff-events.jsonl
# Thresholds config: ~/.config/handoff-metrics/thresholds.yaml

set -euo pipefail

LOG_DIR="$HOME/.metrics"
LOG_FILE="$LOG_DIR/handoff-events.jsonl"
METRICS_LOG="$LOG_DIR/handoff-metrics.log"
CONFIG_DIR="$HOME/.config/handoff-metrics"
CONFIG_FILE="$CONFIG_DIR/thresholds.yaml"

# Logging functions
log_info() {
  local message="$1"
  local timestamp
  timestamp="$(date -Iseconds)"
  echo "[$timestamp] [INFO] $message" >> "$METRICS_LOG"
}

log_error() {
  local message="$1"
  local timestamp
  timestamp="$(date -Iseconds)"
  echo "[$timestamp] [ERROR] $message" | tee -a "$METRICS_LOG" >&2
}

log_warning() {
  local message="$1"
  local timestamp
  timestamp="$(date -Iseconds)"
  echo "[$timestamp] [WARNING] $message" >> "$METRICS_LOG"
}

log_critical() {
  local message="$1"
  local timestamp
  timestamp="$(date -Iseconds)"
  echo "[$timestamp] [CRITICAL] $message" >> "$METRICS_LOG"
}

ensure_log_dir() {
  if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR"
  fi
}

record_event() {
  local agent=""
  local project=""
  local status=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent)
        agent="$2"; shift 2 ;;
      --project)
        project="$2"; shift 2 ;;
      --status)
        status="$2"; shift 2 ;;
      *)
        echo "Unknown argument: $1" >&2
        exit 1 ;;
    esac
  done

  if [[ -z "$agent" || -z "$status" ]]; then
    log_error "Missing required arguments: agent and status are required"
    echo "Usage: $0 record --agent NAME --project PATH --status complete|incomplete" >&2
    exit 1
  fi

  # Validate status value
  if [[ "$status" != "complete" && "$status" != "incomplete" ]]; then
    log_error "Invalid status: $status (must be complete or incomplete)"
    echo "Error: status must be 'complete' or 'incomplete'" >&2
    exit 1
  fi

  ensure_log_dir

  # Attempt to record the event
  local ts
  ts="$(date -Iseconds)"
  if printf '{"timestamp":"%s","agent":"%s","project":"%s","status":"%s"}\n' \
    "$ts" "$agent" "$project" "$status" >> "$LOG_FILE"; then
    log_info "Handoff event recorded: agent=$agent project=$project status=$status"
  else
    log_error "Failed to write handoff event to $LOG_FILE"
    exit 1
  fi
}

print_summary() {
  if [ ! -f "$LOG_FILE" ]; then
    echo "No handoff events logged yet ($LOG_FILE not found)."
    exit 0
  fi

  echo "=== Handoff Metrics Summary ==="
  echo "Log file: $LOG_FILE"
  echo
  echo "Total events: $(wc -l < "$LOG_FILE" | tr -d ' ')"
  echo
  echo "By agent:"
  awk -F'"' '/"agent"/ {a[$8]++} END {for (k in a) printf "  %s: %d\n", k, a[k]}' "$LOG_FILE" | sort
  echo
  echo "By status:"
  awk -F'"' '/"status"/ {s[$16]++} END {for (k in s) printf "  %s: %d\n", k, s[k]}' "$LOG_FILE" | sort
}

load_thresholds() {
  # Load thresholds from config file or use defaults
  local config="${1:-$CONFIG_FILE}"

  # Default thresholds
  MIN_HANDOFFS_PER_WEEK=1
  LOOKBACK_WEEKS=4
  INCOMPLETE_RATE_THRESHOLD=0.30
  MIN_SAMPLE_SIZE=10
  CRITICAL_NO_HANDOFFS_WEEKS=8

  # Override from config if exists and is readable
  if [ -f "$config" ] && [ -r "$config" ]; then
    # Parse YAML (simple key: value format)
    while IFS=: read -r key value; do
      # Trim whitespace
      key=$(echo "$key" | tr -d ' ')
      value=$(echo "$value" | tr -d ' ')

      case "$key" in
        min_handoffs_per_week) MIN_HANDOFFS_PER_WEEK="$value" ;;
        lookback_weeks) LOOKBACK_WEEKS="$value" ;;
        incomplete_rate_threshold) INCOMPLETE_RATE_THRESHOLD="$value" ;;
        min_sample_size) MIN_SAMPLE_SIZE="$value" ;;
        critical_no_handoffs_weeks) CRITICAL_NO_HANDOFFS_WEEKS="${value:-8}" ;;
      esac
    done < <(grep -v '^\s*#' "$config" | grep -v '^\s*$' | grep -v 'thresholds:')
  fi
}

check_thresholds() {
  local config_override=""

  # Parse arguments
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --config)
        config_override="$2"; shift 2 ;;
      *)
        echo "Unknown argument: $1" >&2
        exit 1 ;;
    esac
  done

  if [ ! -f "$LOG_FILE" ]; then
    # No data yet - return healthy (can't evaluate thresholds without data)
    echo '{"status":"HEALTHY","reason":"No data collected yet","violations":[],"metrics":{"total_handoffs":0,"complete":0,"incomplete":0,"incomplete_rate":0}}'
    exit 0
  fi

  # Load thresholds
  load_thresholds "$config_override"

  # Calculate metrics
  local total_handoffs
  local complete_count
  local incomplete_count
  local incomplete_rate

  total_handoffs=$(wc -l < "$LOG_FILE" | tr -d ' ')
  complete_count=$(grep -c '"status":"complete"' "$LOG_FILE" || true)
  incomplete_count=$(grep -c '"status":"incomplete"' "$LOG_FILE" || true)

  if [ "$total_handoffs" -eq 0 ]; then
    incomplete_rate=0
  else
    incomplete_rate=$(awk "BEGIN {printf \"%.2f\", $incomplete_count / $total_handoffs}")
  fi

  # Calculate time-based metrics
  local current_time
  local lookback_seconds
  local critical_lookback_seconds
  local recent_handoffs
  local very_recent_handoffs

  current_time=$(date +%s)
  lookback_seconds=$((LOOKBACK_WEEKS * 7 * 24 * 60 * 60))
  critical_lookback_seconds=$((CRITICAL_NO_HANDOFFS_WEEKS * 7 * 24 * 60 * 60))

  # Count handoffs in lookback window
  recent_handoffs=0
  very_recent_handoffs=0
  while IFS= read -r line; do
    # Extract timestamp from JSON
    timestamp=$(echo "$line" | grep -oP '"timestamp":"[^"]*"' | cut -d'"' -f4)
    if [ -n "$timestamp" ]; then
      event_time=$(date -d "$timestamp" +%s 2>/dev/null || echo "0")
      if [ "$event_time" -gt 0 ]; then
        age=$((current_time - event_time))
        if [ "$age" -le "$lookback_seconds" ]; then
          recent_handoffs=$((recent_handoffs + 1))
        fi
        if [ "$age" -le "$critical_lookback_seconds" ]; then
          very_recent_handoffs=$((very_recent_handoffs + 1))
        fi
      fi
    fi
  done < "$LOG_FILE"

  # Calculate handoffs per week (recent window)
  local handoffs_per_week
  if [ "$LOOKBACK_WEEKS" -gt 0 ]; then
    handoffs_per_week=$(awk "BEGIN {printf \"%.2f\", $recent_handoffs / $LOOKBACK_WEEKS}")
  else
    handoffs_per_week="$recent_handoffs"
  fi

  # Check thresholds
  local status="HEALTHY"
  local exit_code=0
  local violations=()

  # Threshold 1: CRITICAL - No handoffs in last N weeks
  if [ "$very_recent_handoffs" -eq 0 ] && [ "$total_handoffs" -gt 0 ]; then
    status="CRITICAL"
    exit_code=2
    violations+=("{\"threshold\":\"no_handoffs_critical\",\"expected\":1,\"actual\":0,\"severity\":\"CRITICAL\",\"message\":\"No handoffs in last $CRITICAL_NO_HANDOFFS_WEEKS weeks\"}")
  fi

  # Threshold 2: WARNING - Low handoff rate
  if [ "$(awk "BEGIN {print ($handoffs_per_week < $MIN_HANDOFFS_PER_WEEK)}")" -eq 1 ]; then
    if [ "$status" != "CRITICAL" ]; then
      status="WARNING"
      exit_code=1
    fi
    violations+=("{\"threshold\":\"min_handoffs_per_week\",\"expected\":$MIN_HANDOFFS_PER_WEEK,\"actual\":$handoffs_per_week,\"severity\":\"WARNING\",\"message\":\"Handoff rate below threshold\"}")
  fi

  # Threshold 3: WARNING - High incomplete rate (only if enough samples)
  if [ "$total_handoffs" -ge "$MIN_SAMPLE_SIZE" ]; then
    if [ "$(awk "BEGIN {print ($incomplete_rate > $INCOMPLETE_RATE_THRESHOLD)}")" -eq 1 ]; then
      if [ "$status" != "CRITICAL" ]; then
        status="WARNING"
        exit_code=1
      fi
      violations+=("{\"threshold\":\"incomplete_rate\",\"expected\":$INCOMPLETE_RATE_THRESHOLD,\"actual\":$incomplete_rate,\"severity\":\"WARNING\",\"message\":\"Incomplete rate above threshold\"}")
    fi
  fi

  # Build violations JSON array
  local violations_json="[]"
  if [ ${#violations[@]} -gt 0 ]; then
    violations_json="[$(IFS=,; echo "${violations[*]}")]"
  fi

  # Log threshold violations
  if [ "$status" = "CRITICAL" ]; then
    log_critical "Threshold check CRITICAL: $status (${#violations[@]} violations)"
  elif [ "$status" = "WARNING" ]; then
    log_warning "Threshold check WARNING: $status (${#violations[@]} violations)"
  else
    log_info "Threshold check HEALTHY: all thresholds passed"
  fi

  # Output JSON
  cat <<EOF
{
  "status": "$status",
  "violations": $violations_json,
  "metrics": {
    "total_handoffs": $total_handoffs,
    "complete": $complete_count,
    "incomplete": $incomplete_count,
    "incomplete_rate": $incomplete_rate,
    "recent_handoffs_${LOOKBACK_WEEKS}w": $recent_handoffs,
    "handoffs_per_week": $handoffs_per_week
  },
  "thresholds": {
    "min_handoffs_per_week": $MIN_HANDOFFS_PER_WEEK,
    "lookback_weeks": $LOOKBACK_WEEKS,
    "incomplete_rate_threshold": $INCOMPLETE_RATE_THRESHOLD,
    "min_sample_size": $MIN_SAMPLE_SIZE,
    "critical_no_handoffs_weeks": $CRITICAL_NO_HANDOFFS_WEEKS
  }
}
EOF

  exit $exit_code
}

case "${1:-}" in
  record)
    shift
    record_event "$@" ;;
  summary)
    shift || true
    print_summary ;;
  check-thresholds)
    shift || true
    check_thresholds "$@" ;;
  ""|-h|--help)
    echo "Usage: $0 record --agent NAME --project PATH --status complete|incomplete" >&2
    echo "       $0 summary" >&2
    echo "       $0 check-thresholds [--config FILE]" >&2 ;;
  *)
    echo "Unknown command: ${1:-}" >&2
    echo "Use --help for usage." >&2
    exit 1 ;;
esac
