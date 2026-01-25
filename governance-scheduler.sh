#!/usr/bin/env bash
# Governance Calendar Scheduler
# Checks ~/.claude/governance-calendar.yaml for due events and sends ntfy notifications
# Created: 2026-01-24 (ISSUE-111)
# Cron: */15 * * * * ~/dev/infrastructure/tools/governance-scheduler.sh

set -euo pipefail

# Configuration
CALENDAR_FILE="$HOME/.claude/governance-calendar.yaml"
LOG_FILE="$HOME/.claude/logs/governance-notifications.jsonl"
SENT_FILE="$HOME/.claude/logs/governance-sent-today.txt"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Reset sent-today file at midnight
TODAY=$(date +%Y-%m-%d)
if [[ -f "$SENT_FILE" ]]; then
    SENT_DATE=$(head -1 "$SENT_FILE" 2>/dev/null || echo "")
    if [[ "$SENT_DATE" != "$TODAY" ]]; then
        echo "$TODAY" > "$SENT_FILE"
    fi
else
    echo "$TODAY" > "$SENT_FILE"
fi

# Check if already sent today (to avoid duplicates)
already_sent() {
    local event_id="$1"
    grep -q "^$event_id$" "$SENT_FILE" 2>/dev/null
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

    endpoint=$(python3 -c "
import yaml
with open('$CALENDAR_FILE') as f:
    config = yaml.safe_load(f)
print(config.get('ntfy', {}).get('endpoint', ''))
")

    if [[ -z "$endpoint" ]]; then
        echo "ERROR: No ntfy endpoint configured" >&2
        return 1
    fi

    # Send notification
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Priority: $priority" \
        -H "Tags: $tags" \
        -d "$message" \
        "$endpoint")

    if [[ "$response" == "200" ]]; then
        log_notification "$message" "$priority" "success"
        return 0
    else
        log_notification "$message" "$priority" "failed:$response"
        return 1
    fi
}

# Log notification
log_notification() {
    local message="$1"
    local priority="$2"
    local status="$3"

    # Escape message for JSON
    local escaped_message
    escaped_message=$(echo "$message" | sed 's/"/\\"/g' | tr '\n' ' ')

    echo "{\"timestamp\":\"$(date -Iseconds)\",\"message\":\"$escaped_message\",\"priority\":\"$priority\",\"status\":\"$status\"}" >> "$LOG_FILE"
}

# Check recurring events
check_recurring() {
    python3 << 'PYTHON'
import yaml
import sys
from datetime import datetime, timedelta

CALENDAR_FILE = "$HOME/.claude/governance-calendar.yaml".replace("$HOME", __import__('os').environ['HOME'])

try:
    with open(CALENDAR_FILE) as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
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
    event_hour, event_min = map(int, time_part.split(':'))

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
from datetime import datetime, timedelta

CALENDAR_FILE = "$HOME/.claude/governance-calendar.yaml".replace("$HOME", __import__('os').environ['HOME'])

try:
    with open(CALENDAR_FILE) as f:
        config = yaml.safe_load(f)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
current_time = now.strftime("%H:%M")

one_time = config.get('one_time', {})

for event_id, event in one_time.items():
    event_date = event.get('date', '')
    event_time = event.get('time', '09:00')

    if event_date != today:
        continue

    # Check if current time is past event time (within same day)
    if current_time >= event_time:
        # Output: event_id|message|priority|tags
        print(f"{event_id}|{event.get('message', '')}|{event.get('priority', 'default')}|{event.get('tags', 'calendar')}")

PYTHON
}

# Main execution
main() {
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
            ((events_sent++))
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
            ((events_sent++))
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
from datetime import datetime

with open("$HOME/.claude/governance-calendar.yaml".replace("$HOME", __import__('os').environ['HOME'])) as f:
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
