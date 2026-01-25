#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="$HOME/.claude/logs"
METRICS_FILE="$LOG_DIR/project-memory-metrics.jsonl"
LOCK_FILE="$LOG_DIR/project-memory-metrics.lock"
ERROR_LOG="$LOG_DIR/project-memory-metrics.errors.log"

usage() {
    cat << 'EOF'
project-memory-metrics.sh --mode save|restore --project NAME --duration-ms MS [fields...]

Save mode fields:
  --files-captured N
  --decisions-captured N
  --session-count N

Restore mode fields:
  --files-presented N
  --sessions-available N
  --memory-file-size-bytes N
EOF
}

mode=""
project=""
duration_ms=""
files_captured=""
decisions_captured=""
session_count=""
files_presented=""
sessions_available=""
memory_file_size_bytes=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            mode="${2:-}"
            shift 2
            ;;
        --project)
            project="${2:-}"
            shift 2
            ;;
        --duration-ms)
            duration_ms="${2:-}"
            shift 2
            ;;
        --files-captured)
            files_captured="${2:-}"
            shift 2
            ;;
        --decisions-captured)
            decisions_captured="${2:-}"
            shift 2
            ;;
        --session-count)
            session_count="${2:-}"
            shift 2
            ;;
        --files-presented)
            files_presented="${2:-}"
            shift 2
            ;;
        --sessions-available)
            sessions_available="${2:-}"
            shift 2
            ;;
        --memory-file-size-bytes)
            memory_file_size_bytes="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

require_int() {
    local name="$1"
    local value="$2"
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "ERROR: $name must be a non-negative integer" >&2
        exit 1
    fi
}

if [[ -z "$mode" || -z "$project" || -z "$duration_ms" ]]; then
    echo "ERROR: --mode, --project, and --duration-ms are required" >&2
    usage
    exit 1
fi

require_int duration_ms "$duration_ms"

case "$mode" in
    save)
        if [[ -z "$files_captured" || -z "$decisions_captured" || -z "$session_count" ]]; then
            echo "ERROR: save mode requires --files-captured, --decisions-captured, --session-count" >&2
            exit 1
        fi
        require_int files_captured "$files_captured"
        require_int decisions_captured "$decisions_captured"
        require_int session_count "$session_count"
        ;;
    restore)
        if [[ -z "$files_presented" || -z "$sessions_available" || -z "$memory_file_size_bytes" ]]; then
            echo "ERROR: restore mode requires --files-presented, --sessions-available, --memory-file-size-bytes" >&2
            exit 1
        fi
        require_int files_presented "$files_presented"
        require_int sessions_available "$sessions_available"
        require_int memory_file_size_bytes "$memory_file_size_bytes"
        ;;
    *)
        echo "ERROR: --mode must be 'save' or 'restore'" >&2
        exit 1
        ;;
esac

timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_FILE"
if ! flock -w 10 200; then
    echo "ERROR: Could not acquire lock: $LOCK_FILE" >&2
    exit 1
fi
trap 'flock -u 200 || true' EXIT

if [[ -f "$METRICS_FILE" ]]; then
    file_size=$(stat -c%s "$METRICS_FILE" 2>/dev/null || echo 0)
    line_count=$(wc -l < "$METRICS_FILE" 2>/dev/null || echo 0)
    if [[ "$file_size" -gt 5242880 || "$line_count" -gt 50000 ]]; then
        tmp_file="${METRICS_FILE}.tmp"
        tail -n 50000 "$METRICS_FILE" > "$tmp_file"
        mv "$tmp_file" "$METRICS_FILE"
    fi
fi

json_line=$(python3 - \
    "$timestamp" \
    "$mode" \
    "$duration_ms" \
    "$project" \
    "$files_captured" \
    "$decisions_captured" \
    "$session_count" \
    "$files_presented" \
    "$sessions_available" \
    "$memory_file_size_bytes" << 'PYTHON'
import json
import sys

(
    ts,
    mode,
    duration,
    project,
    files_captured,
    decisions_captured,
    session_count,
    files_presented,
    sessions_available,
    memory_size,
) = sys.argv[1:]

data = {
    "timestamp": ts,
    "mode": mode,
    "duration_ms": int(duration),
    "project": project,
}

if mode == "save":
    data.update({
        "files_captured": int(files_captured),
        "decisions_captured": int(decisions_captured),
        "session_count": int(session_count),
    })
else:
    data.update({
        "files_presented": int(files_presented),
        "sessions_available": int(sessions_available),
        "memory_file_size_bytes": int(memory_size),
    })

print(json.dumps(data, ensure_ascii=False))
PYTHON
)

printf '%s\n' "$json_line" >> "$METRICS_FILE"

last_line=$(tail -n 1 "$METRICS_FILE" 2>/dev/null || true)
if ! python3 - "$last_line" "$mode" << 'PYTHON'
import json
import sys

line = sys.argv[1]
mode = sys.argv[2]

try:
    data = json.loads(line)
    ok = data.get("mode") == mode and isinstance(data.get("duration_ms"), int) and data.get("duration_ms") >= 0
    raise SystemExit(0 if ok else 1)
except Exception:
    raise SystemExit(1)
PYTHON
then
    printf '%s WARN project-memory metrics validation failed\n' "$timestamp" >> "$ERROR_LOG"
fi
