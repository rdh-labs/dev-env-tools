#!/usr/bin/env bash
# Governance Calendar Scheduler
# Checks ~/.claude/governance-calendar.yaml for due events and sends ntfy notifications
# Created: 2026-01-24 (ISSUE-111)
# Cron: */15 * * * * ~/dev/infrastructure/tools/governance-scheduler.sh

set -euo pipefail

# Ensure PATH includes common locations for cron environment
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"

# Configuration
CALENDAR_FILE="$HOME/.claude/governance-calendar.yaml"
LOG_FILE="$HOME/.claude/logs/governance-notifications.jsonl"
SENT_FILE="$HOME/.claude/logs/governance-sent-today.txt"
LOCK_FILE="$HOME/.claude/logs/governance-scheduler.lock"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Acquire exclusive lock to prevent race conditions
acquire_lock() {
    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        echo "Another instance is running, exiting" >&2
        exit 0
    fi
    trap 'flock -u 200 || true' EXIT
}

reset_sent_file() {
    # Reset sent-today file at midnight
    local today
    today=$(date +%Y-%m-%d)
    if [[ -f "$SENT_FILE" ]]; then
        local sent_date
        sent_date=$(head -1 "$SENT_FILE" 2>/dev/null || echo "")
        if [[ "$sent_date" != "$today" ]]; then
            echo "$today" > "$SENT_FILE"
        fi
    else
        echo "$today" > "$SENT_FILE"
    fi
}

# Check if already sent today (to avoid duplicates)
# Uses -Fx for fixed string matching (prevents regex injection)
already_sent() {
    local event_id="$1"
    grep -Fxq "$event_id" "$SENT_FILE" 2>/dev/null
}

# Mark as sent
mark_sent() {
    local event_id="$1"
    echo "$event_id" >> "$SENT_FILE"
}

# Send ntfy notification
send_ntfy() {
    local message="$1"
    local priority="${2:-default}"
    local tags="${3:-calendar}"
    local endpoint

    # Use environment variable to avoid command injection
    endpoint=$(CALENDAR_FILE="$CALENDAR_FILE" python3 -c "
import yaml
import os
with open(os.environ['CALENDAR_FILE']) as f:
    config = yaml.safe_load(f)
print(config.get('ntfy', {}).get('endpoint', ''))
")

    if [[ -z "$endpoint" ]]; then
        echo "ERROR: No ntfy endpoint configured" >&2
        return 1
    fi

    # Send notification with timeout to prevent cron hangs
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -H "Priority: $priority" \
        -H "Tags: $tags" \
        --data-raw "$message" \
        "$endpoint")

    if [[ "$response" == "200" ]]; then
        log_notification "$message" "$priority" "success"
        return 0
    else
        log_notification "$message" "$priority" "failed:$response"
        return 1
    fi
}

# Log notification with proper JSON escaping
log_notification() {
    local message="$1"
    local priority="$2"
    local status="$3"
    local timestamp
    timestamp=$(date -Iseconds)

    # Use jq for proper JSON escaping if available, otherwise use Python
    if command -v jq &>/dev/null; then
        jq -nc \
            --arg ts "$timestamp" \
            --arg msg "$message" \
            --arg pri "$priority" \
            --arg st "$status" \
            '{timestamp: $ts, message: $msg, priority: $pri, status: $st}' >> "$LOG_FILE"
    else
        python3 - "$timestamp" "$message" "$priority" "$status" << 'PYTHON' >> "$LOG_FILE"
import json
import sys
print(json.dumps({
    'timestamp': sys.argv[1],
    'message': sys.argv[2],
    'priority': sys.argv[3],
    'status': sys.argv[4]
}))
PYTHON
    fi
}

# Check recurring events
check_recurring() {
    python3 << 'PYTHON'
import yaml
import sys
import os
from datetime import datetime, timedelta

CALENDAR_FILE = os.path.expanduser("~/.claude/governance-calendar.yaml")

try:
    with open(CALENDAR_FILE) as f:
        config = yaml.safe_load(f) or {}
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(config, dict):
    print("ERROR: Calendar file must contain a YAML mapping", file=sys.stderr)
    sys.exit(1)

now = datetime.now()
current_day = now.strftime("%A")  # Monday, Tuesday, etc.
current_time = now.strftime("%H:%M")
current_dom = now.day  # Day of month

recurring = config.get('recurring', {})

for event_id, event in recurring.items():
    schedule = event.get('schedule', '')
    notify_before = event.get('notify_before_minutes', 0)

    # Parse schedule: "Monday 09:00" or "1st 10:00"
    parts = schedule.split()
    if len(parts) != 2:
        continue

    day_part, time_part = parts
    try:
        event_hour, event_min = map(int, time_part.split(':'))
    except Exception:
        continue

    # Check if this is the right day
    is_right_day = False
    if day_part == current_day:
        is_right_day = True
    elif day_part == "1st" and current_dom == 1:
        is_right_day = True
    elif day_part == "15th" and current_dom == 15:
        is_right_day = True

    if not is_right_day:
        continue

    # Check if within notification window
    event_time = now.replace(hour=event_hour, minute=event_min, second=0, microsecond=0)
    notify_start = event_time - timedelta(minutes=notify_before)

    if notify_start <= now <= event_time:
        # Output: event_id|message|priority|tags
        print(f"{event_id}|{event.get('message', '')}|{event.get('priority', 'default')}|{event.get('tags', 'calendar')}")

PYTHON
}

# Check one-time events
check_one_time() {
    python3 << 'PYTHON'
import yaml
import sys
import os
from datetime import datetime, timedelta


CALENDAR_FILE = os.path.expanduser("~/.claude/governance-calendar.yaml")

try:
    with open(CALENDAR_FILE) as f:
        config = yaml.safe_load(f) or {}
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)

if not isinstance(config, dict):
    print("ERROR: Calendar file must contain a YAML mapping", file=sys.stderr)
    sys.exit(1)

now = datetime.now()

one_time = config.get('one_time', {})

for event_id, event in one_time.items():
    event_date = event.get('date', '')
    event_time = event.get('time', '09:00')
    notify_before = event.get('notify_before_minutes', 0)

    try:
        notify_before = max(int(notify_before), 0)
    except Exception:
        notify_before = 0

    try:
        event_dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M")
    except Exception:
        continue

    if event_dt.date() != now.date():
        continue

    start_of_day = event_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    notify_start = event_dt - timedelta(minutes=notify_before)
    if notify_start < start_of_day:
        notify_start = start_of_day

    # Check if current time is past notification window (same day)
    if now >= notify_start:
        # Output: event_id|message|priority|tags
        print(f"{event_id}|{event.get('message', '')}|{event.get('priority', 'default')}|{event.get('tags', 'calendar')}")

PYTHON
}

# Move completed one-time event to completed section
move_to_completed() {
    local event_id="$1"
    python3 - "$event_id" << 'PYTHON'
import yaml
import os
import sys
from datetime import datetime
import re
import tempfile

CALENDAR_FILE = os.path.expanduser("~/.claude/governance-calendar.yaml")

with open(CALENDAR_FILE) as f:
    config = yaml.safe_load(f) or {}

if not isinstance(config, dict):
    raise SystemExit("Calendar file must contain a YAML mapping")

event_id = sys.argv[1]

TIME_RE = re.compile(r"^\\d{1,2}:\\d{2}$")
DATE_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")

class Quoted(str):
    pass

def quoted_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

yaml.SafeDumper.add_representer(Quoted, quoted_representer)

def quote_strings(obj):
    if isinstance(obj, dict):
        return {k: quote_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [quote_strings(v) for v in obj]
    if isinstance(obj, str) and (TIME_RE.match(obj) or DATE_RE.match(obj)):
        return Quoted(obj)
    return obj

def atomic_write(path, data):
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".governance-calendar.", suffix=".tmp", dir=dir_name)
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

if event_id in config.get('one_time', {}):
    event = config['one_time'].pop(event_id)
    event['completed_at'] = datetime.now().isoformat()
    completed = config.get('completed', {})
    if isinstance(completed, list):
        completed_dict = {}
        for item in completed:
            if isinstance(item, dict):
                completed_dict.update(item)
        completed = completed_dict
    elif not isinstance(completed, dict):
        completed = {}
    config['completed'] = completed
    completed[event_id] = event

    atomic_write(CALENDAR_FILE, quote_strings(config))
    print(f"Moved {event_id} to completed")
PYTHON
}

# Main execution
main() {
    # Acquire lock to prevent race conditions from overlapping cron runs
    acquire_lock
    reset_sent_file

    if [[ ! -f "$CALENDAR_FILE" ]]; then
        echo "ERROR: Calendar file not found: $CALENDAR_FILE" >&2
        exit 1
    fi

    local events_sent=0

    # Check recurring events
    while IFS='|' read -r event_id message priority tags; do
        [[ -z "$event_id" ]] && continue

        if already_sent "recurring:$event_id"; then
            continue
        fi

        echo "Sending recurring: $event_id"
        if send_ntfy "$message" "$priority" "$tags"; then
            mark_sent "recurring:$event_id"
            ((++events_sent))
        fi
    done < <(check_recurring)

    # Check one-time events
    while IFS='|' read -r event_id message priority tags; do
        [[ -z "$event_id" ]] && continue

        if already_sent "onetime:$event_id"; then
            continue
        fi

        echo "Sending one-time: $event_id"
        if send_ntfy "$message" "$priority" "$tags"; then
            mark_sent "onetime:$event_id"
            move_to_completed "$event_id"
            ((++events_sent))
        fi
    done < <(check_one_time)

    if [[ $events_sent -gt 0 ]]; then
        echo "Sent $events_sent notification(s)"
    fi
}

# Test mode - send a test notification
test_notification() {
    echo "Sending test notification..."
    if send_ntfy "🧪 Governance scheduler test - $(date '+%Y-%m-%d %H:%M')" "default" "test,calendar"; then
        echo "✅ Test notification sent successfully"
        echo "Check your ntfy client for the notification"
    else
        echo "❌ Test notification failed"
        return 1
    fi
}

# Show upcoming events
show_upcoming() {
    echo "=== Governance Calendar ==="
    echo "Today: $(date '+%A %Y-%m-%d %H:%M')"
    echo
    python3 << 'PYTHON'
import yaml
import os
from datetime import datetime

with open(os.path.expanduser("~/.claude/governance-calendar.yaml")) as f:
    config = yaml.safe_load(f)

print("Recurring events:")
for k, v in config.get('recurring', {}).items():
    print(f"  • {v.get('description', k)}")
    print(f"    Schedule: {v.get('schedule')}")
print()
print("One-time events:")
for k, v in config.get('one_time', {}).items():
    print(f"  • {v.get('description', k)}")
    print(f"    Date: {v.get('date')} {v.get('time', '09:00')}")
PYTHON
}

# Run if executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        test)
            test_notification
            ;;
        show|list)
            show_upcoming
            ;;
        *)
            main "$@"
            ;;
    esac
fi
