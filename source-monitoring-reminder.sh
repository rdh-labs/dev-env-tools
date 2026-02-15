#!/usr/bin/env bash
# Weekly reminder for manual source monitoring pilot (IDEA-399)
# Duration: 4 weeks (2026-02-15 to 2026-03-15)
# Frequency: Every Sunday
# Time estimate: 15-20 minutes

set -euo pipefail

# Configuration
LOG_FILE="$HOME/dev/infrastructure/dev-env-docs/knowledge/source-monitoring-log.md"
START_DATE="2026-02-15"
END_DATE="2026-03-15"
NOTIFICATION_SCRIPT="$HOME/bin/notify.sh"

# Calculate current week
current_date=$(date +%Y-%m-%d)
start_epoch=$(date -d "$START_DATE" +%s)
end_epoch=$(date -d "$END_DATE" +%s)
current_epoch=$(date +%s)

# Check if pilot is still active
if [[ $current_epoch -lt $start_epoch ]]; then
    echo "Pilot hasn't started yet (starts $START_DATE)"
    exit 0
fi

if [[ $current_epoch -gt $end_epoch ]]; then
    # Pilot completed - send completion notification
    if [[ -x "$NOTIFICATION_SCRIPT" ]]; then
        "$NOTIFICATION_SCRIPT" \
            "Source Monitoring Pilot Complete" \
            "4-week pilot finished. Review results in source-monitoring-log.md and create IDEA-399 implementation plan." \
            --priority high \
            --channel auto
    fi
    # Remove this cron job (pilot is done)
    crontab -l | grep -v "source-monitoring-reminder.sh" | crontab - 2>/dev/null || true
    exit 0
fi

# Calculate week number (1-4)
days_since_start=$(( (current_epoch - start_epoch) / 86400 ))
week_num=$(( (days_since_start / 7) + 1 ))

# Send reminder notification
message="Week $week_num of 4: Check best practice sources (15-20 min)

Sources to check:
- LangGraph releases
- AutoGen releases
- Anthropic Engineering blog
- Claude Code changelog
- OpenAI Platform changelog

Log findings in: $LOG_FILE"

if [[ -x "$NOTIFICATION_SCRIPT" ]]; then
    "$NOTIFICATION_SCRIPT" \
        "📚 Weekly Source Monitoring" \
        "$message" \
        --priority medium \
        --channel auto
else
    # Fallback to echo if notification script not available
    echo "$message"
fi

# Log reminder sent
echo "[$(date -Iseconds)] Week $week_num reminder sent" >> "$HOME/dev/infrastructure/metrics/source-monitoring-reminders.log"
