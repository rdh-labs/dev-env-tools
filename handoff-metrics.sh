#!/usr/bin/env bash
# Minimal metrics helper for the Session Handoff System
#
# Modes:
#   handoff-metrics.sh record --agent NAME --project PATH --status complete|incomplete
#   handoff-metrics.sh summary
#
# Events are stored in: ~/.metrics/handoff-events.jsonl

set -euo pipefail

LOG_DIR="$HOME/.metrics"
LOG_FILE="$LOG_DIR/handoff-events.jsonl"

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
    echo "Usage: $0 record --agent NAME --project PATH --status complete|incomplete" >&2
    exit 1
  fi

  ensure_log_dir
  local ts
  ts="$(date -Iseconds)"
  printf '{"timestamp":"%s","agent":"%s","project":"%s","status":"%s"}\n' \
    "$ts" "$agent" "$project" "$status" >> "$LOG_FILE"
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
  awk -F'"' '/"agent"/ {a[$4]++} END {for (k in a) printf "  %s: %d\n", k, a[k]}' "$LOG_FILE" | sort
  echo
  echo "By status:"
  awk -F'"' '/"status"/ {s[$4]++} END {for (k in s) printf "  %s: %d\n", k, s[k]}' "$LOG_FILE" | sort
}

case "${1:-}" in
  record)
    shift
    record_event "$@" ;;
  summary)
    shift || true
    print_summary ;;
  ""|-h|--help)
    echo "Usage: $0 record --agent NAME --project PATH --status complete|incomplete" >&2
    echo "       $0 summary" >&2 ;;
  *)
    echo "Unknown command: ${1:-}" >&2
    echo "Use --help for usage." >&2
    exit 1 ;;
esac
