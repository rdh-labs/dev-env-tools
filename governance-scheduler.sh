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
VENV_DIR="$HOME/dev/infrastructure/tools/.venv-governance-scheduler"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Ensure Python venv is available
ensure_python() {
    if [[ ! -x "$PYTHON_BIN" ]]; then
        echo "ERROR: Python venv not found: $PYTHON_BIN" >&2
        echo "Create it with:" >&2
        echo "  python3 -m venv \"$VENV_DIR\"" >&2
        echo "  \"$PYTHON_BIN\" -m pip install --upgrade pip" >&2
        echo "  \"$PYTHON_BIN\" -m pip install ruamel.yaml" >&2
        exit 1
    fi
}

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

# Mark one-time event as notified (early/day_of) to prevent duplicate reminders across days
mark_notified() {
    local event_id="$1"
    local stage="${2:-day_of}"
    ensure_python
    "$PYTHON_BIN" - "$event_id" "$stage" << 'PYTHON'
import yaml
import os
import sys
from datetime import datetime
import re
import tempfile

CALENDAR_FILE = os.path.expanduser("~/.claude/governance-calendar.yaml")

event_id = sys.argv[1]
stage = sys.argv[2] if len(sys.argv) > 2 else "day_of"
if stage not in {"early", "day_of"}:
    stage = "day_of"

TIME_RE = re.compile(r"^\\d{1,2}:\\d{2}$")
DATE_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")
ISO_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}")

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString
    _RUAMEL = True
except Exception:
    _RUAMEL = False

if not _RUAMEL:
    class Quoted(str):
        pass

    def quoted_representer(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    yaml.SafeDumper.add_representer(Quoted, quoted_representer)

def quote_scalar(value: str):
    if _RUAMEL:
        return DoubleQuotedScalarString(value)
    return Quoted(value)

def load_yaml(path):
    if _RUAMEL:
        yaml_rt = YAML(typ="rt")
        yaml_rt.preserve_quotes = True
        with open(path) as f:
            return yaml_rt, (yaml_rt.load(f) or {})
    with open(path) as f:
        return None, (yaml.safe_load(f) or {})

def dump_yaml(data, file_obj, yaml_rt=None):
    if _RUAMEL:
        yaml_rt.dump(data, file_obj)
    else:
        yaml.safe_dump(data, file_obj, default_flow_style=False, sort_keys=False)

def quote_strings(obj):
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            obj[key] = quote_strings(obj[key])
        return obj
    if isinstance(obj, list):
        for idx in range(len(obj)):
            obj[idx] = quote_strings(obj[idx])
        return obj
    if isinstance(obj, str) and (TIME_RE.match(obj) or DATE_RE.match(obj) or ISO_RE.match(obj)):
        return quote_scalar(obj)
    return obj

def atomic_write(path, data, yaml_rt=None):
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".governance-calendar.", suffix=".tmp", dir=dir_name)
    try:
        with os.fdopen(fd, "w") as f:
            dump_yaml(data, f, yaml_rt)
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

try:
    yaml_rt, config = load_yaml(CALENDAR_FILE)
    if not isinstance(config, dict):
        raise ValueError("Calendar file must contain a YAML mapping")

    one_time = config.get('one_time', {})
    if event_id not in one_time:
        print(f"WARN: Event '{event_id}' not found for notified_at update", file=sys.stderr)
        raise SystemExit(0)

    event = one_time[event_id]
    notified_at = event.get('notified_at', {})
    if not isinstance(notified_at, dict):
        notified_at = {}
    notified_at[stage] = datetime.now().isoformat()
    event['notified_at'] = notified_at

    config = quote_strings(config)
    atomic_write(CALENDAR_FILE, config, yaml_rt)
except Exception as e:
    print(f"WARN: notified_at update failed: {e}", file=sys.stderr)
    raise SystemExit(0)
PYTHON
}

# Send ntfy notification
send_ntfy() {
    local message="$1"
    local priority="${2:-default}"
    local tags="${3:-calendar}"
    local endpoint
    ensure_python

    # Use environment variable to avoid command injection
    endpoint=$(CALENDAR_FILE="$CALENDAR_FILE" "$PYTHON_BIN" -c "
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
        "$PYTHON_BIN" - "$timestamp" "$message" "$priority" "$status" << 'PYTHON' >> "$LOG_FILE"
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
    "$PYTHON_BIN" << 'PYTHON'
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
    ALLOW_DAY_OF="${ALLOW_DAY_OF:-true}" SIMULATE_REMINDER="${SIMULATE_REMINDER:-false}" SIMULATE_STAGE="${SIMULATE_STAGE:-both}" "$PYTHON_BIN" << 'PYTHON'
import yaml
import sys
import os
from datetime import datetime, timedelta


CALENDAR_FILE = os.path.expanduser("~/.claude/governance-calendar.yaml")
ALLOW_DAY_OF = os.environ.get("ALLOW_DAY_OF", "true").lower() == "true"
SIMULATE_REMINDER = os.environ.get("SIMULATE_REMINDER", "false").lower() == "true"
SIMULATE_STAGE = os.environ.get("SIMULATE_STAGE", "both")

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

    if now.date() > event_dt.date():
        continue

    notified_at = event.get('notified_at', {})
    if not isinstance(notified_at, dict):
        notified_at = {}

    if SIMULATE_REMINDER:
        tags = event.get('tags', 'calendar')
        if 'simulated' not in tags.split(','):
            tags = f"{tags},simulated"
        stages = []
        if SIMULATE_STAGE in ("early", "both"):
            stages.append("early")
        if SIMULATE_STAGE in ("day_of", "both"):
            stages.append("day_of")
        for stage in stages:
            print(f"{event_id}|{event.get('message', '')}|{event.get('priority', 'default')}|{tags}|{stage}")
        continue

    if now.date() == event_dt.date() and now >= event_dt:
        if ALLOW_DAY_OF and 'day_of' not in notified_at:
            print(f"{event_id}|{event.get('message', '')}|{event.get('priority', 'default')}|{event.get('tags', 'calendar')}|day_of")
        continue

    if notify_before <= 0:
        continue

    notify_start = event_dt - timedelta(minutes=notify_before)
    if now >= notify_start and 'early' not in notified_at:
        print(f"{event_id}|{event.get('message', '')}|{event.get('priority', 'default')}|{event.get('tags', 'calendar')}|early")

PYTHON
}

# Move completed one-time event to completed section
move_to_completed() {
    local event_id="$1"
    "$PYTHON_BIN" - "$event_id" << 'PYTHON'
import yaml
import os
import sys
from datetime import datetime
import re
import tempfile

CALENDAR_FILE = os.path.expanduser("~/.claude/governance-calendar.yaml")

event_id = sys.argv[1]

TIME_RE = re.compile(r"^\\d{1,2}:\\d{2}$")
DATE_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")
ISO_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}")

try:
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString
    _RUAMEL = True
except Exception:
    _RUAMEL = False

if not _RUAMEL:
    class Quoted(str):
        pass

    def quoted_representer(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    yaml.SafeDumper.add_representer(Quoted, quoted_representer)

def quote_scalar(value: str):
    if _RUAMEL:
        return DoubleQuotedScalarString(value)
    return Quoted(value)

def load_yaml(path):
    if _RUAMEL:
        yaml_rt = YAML(typ="rt")
        yaml_rt.preserve_quotes = True
        with open(path) as f:
            return yaml_rt, (yaml_rt.load(f) or {})
    with open(path) as f:
        return None, (yaml.safe_load(f) or {})

def dump_yaml(data, file_obj, yaml_rt=None):
    if _RUAMEL:
        yaml_rt.dump(data, file_obj)
    else:
        yaml.safe_dump(data, file_obj, default_flow_style=False, sort_keys=False)

def quote_strings(obj):
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            obj[key] = quote_strings(obj[key])
        return obj
    if isinstance(obj, list):
        for idx in range(len(obj)):
            obj[idx] = quote_strings(obj[idx])
        return obj
    if isinstance(obj, str) and (TIME_RE.match(obj) or DATE_RE.match(obj) or ISO_RE.match(obj)):
        return quote_scalar(obj)
    return obj

def atomic_write(path, data, yaml_rt=None):
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".governance-calendar.", suffix=".tmp", dir=dir_name)
    try:
        with os.fdopen(fd, "w") as f:
            dump_yaml(data, f, yaml_rt)
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

yaml_rt, config = load_yaml(CALENDAR_FILE)
if not isinstance(config, dict):
    raise SystemExit("Calendar file must contain a YAML mapping")

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

    config = quote_strings(config)
    atomic_write(CALENDAR_FILE, config, yaml_rt)
    print(f"Moved {event_id} to completed")
PYTHON
}

# Main execution
main() {
    # Acquire lock to prevent race conditions from overlapping cron runs
    acquire_lock
    ensure_python
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
    while IFS='|' read -r event_id message priority tags stage; do
        [[ -z "$event_id" ]] && continue
        stage="${stage:-day_of}"
        local sent_key="onetime:${stage}:${event_id}"

        if [[ "${SIMULATE_REMINDER:-false}" == "true" ]]; then
            echo "Simulated one-time (${stage}): $event_id"
            send_ntfy "$message" "$priority" "$tags" || true
            continue
        fi

        if already_sent "$sent_key"; then
            continue
        fi

        echo "Sending one-time (${stage}): $event_id"
        if send_ntfy "$message" "$priority" "$tags"; then
            mark_sent "$sent_key"
            mark_notified "$event_id" "$stage"
            if [[ "$stage" == "day_of" ]]; then
                move_to_completed "$event_id"
            fi
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
    ensure_python
    "$PYTHON_BIN" << 'PYTHON'
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
        --simulate)
            SIMULATE_REMINDER=true
            case "${2:-both}" in
                early|day_of|both)
                    SIMULATE_STAGE="${2:-both}"
                    ;;
                *)
                    echo "ERROR: Invalid simulate stage. Use early, day_of, or both." >&2
                    exit 1
                    ;;
            esac
            main "$@"
            ;;
        *)
            main "$@"
            ;;
    esac
fi
