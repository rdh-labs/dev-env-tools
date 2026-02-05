#!/bin/bash
# ByteRover Weekly Sync Reminder
# Sends a notification to remind user to sync ByteRover
#
# Add to crontab for weekly reminders:
#   crontab -e
#   0 9 * * 1 ~/dev/infrastructure/tools/byterover-weekly-reminder.sh
#
# Created: 2026-02-04
# Updated: 2026-02-04 - R2 fixes: injection-safe state parsing

SYNC_STATE_FILE="$HOME/.brv-governance-sync-state"
LOG_FILE="$HOME/.brv-reminder.log"

# Logging function
log() {
    echo "$(date -Iseconds) $1" >> "$LOG_FILE"
}

log "=== Reminder check started ==="

# Check if notify.sh exists
if [ ! -x "$HOME/bin/notify.sh" ]; then
    log "ERROR: notify.sh not found or not executable"
    exit 1
fi

if [ -f "$SYNC_STATE_FILE" ]; then
    # Read state safely (no source - prevents code injection)
    LAST_SYNC=$(grep '^LAST_SYNC=' "$SYNC_STATE_FILE" 2>/dev/null | cut -d'=' -f2)

    # Validate LAST_SYNC format (ISO 8601: YYYY-MM-DD...)
    if ! [[ "$LAST_SYNC" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2} ]]; then
        log "ERROR: Invalid LAST_SYNC format: $LAST_SYNC"
        MESSAGE="ByteRover sync state corrupted (invalid date). Run manually: cd ~/dev && brv, then ~/dev/infrastructure/tools/byterover-governance-sync.sh"
        ~/bin/notify.sh "ByteRover Sync Error" "$MESSAGE" --priority high --channel auto
        log "Sent error notification"
        exit 1
    fi

    # Extract date and calculate days since
    LAST_SYNC_DATE=$(echo "$LAST_SYNC" | cut -d'T' -f1)

    # Validate the extracted date is parseable
    if ! date -d "$LAST_SYNC_DATE" +%s &>/dev/null; then
        log "ERROR: Cannot parse date: $LAST_SYNC_DATE"
        MESSAGE="ByteRover sync state has unparseable date. Run sync manually."
        ~/bin/notify.sh "ByteRover Sync Error" "$MESSAGE" --priority high --channel auto
        exit 1
    fi

    LAST_SYNC_EPOCH=$(date -d "$LAST_SYNC_DATE" +%s)
    NOW_EPOCH=$(date +%s)
    DAYS_SINCE=$(( (NOW_EPOCH - LAST_SYNC_EPOCH) / 86400 ))

    log "Last sync: $LAST_SYNC_DATE ($DAYS_SINCE days ago)"

    if [ "$DAYS_SINCE" -lt 7 ]; then
        log "Synced recently ($DAYS_SINCE days), no reminder needed"
        exit 0
    fi

    MESSAGE="ByteRover last synced $DAYS_SINCE days ago. Run: cd ~/dev && brv, then ~/dev/infrastructure/tools/byterover-governance-sync.sh"
else
    log "State file not found - never synced"
    MESSAGE="ByteRover governance sync never run. Initialize with: cd ~/dev && brv, then ~/dev/infrastructure/tools/byterover-governance-sync.sh"
fi

# Send notification
if ~/bin/notify.sh "ByteRover Sync Reminder" "$MESSAGE" --priority default --channel auto; then
    log "Reminder sent successfully: $MESSAGE"
    echo "Reminder sent: $MESSAGE"
else
    log "ERROR: Failed to send notification"
    echo "ERROR: Failed to send notification" >&2
    exit 1
fi
