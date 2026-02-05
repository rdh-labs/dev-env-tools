#!/usr/bin/env bash
# monthly-repo-review-reminder.sh
# Send monthly reminder to review deferred repositories (DEC-145)
# Designed for cron: 0 9 5 * * (9 AM on the 5th of each month)

set -euo pipefail

# Configuration
WATCHED_REPOS_FILE="${HOME}/dev/infrastructure/dev-env-docs/WATCHED-REPOSITORIES.md"
NOTIFY_SCRIPT="${HOME}/bin/notify.sh"
ACTIVITY_SCRIPT="${HOME}/dev/infrastructure/tools/check-watched-repo-activity.sh"

# Extract deferred/candidate repositories from WATCHED-REPOSITORIES.md
DEFERRED_REPOS=$(grep -A 50 "## Candidate Repositories" "${WATCHED_REPOS_FILE}" | \
                 grep "^\|" | grep -v "^|.*Reason to" | grep -v "^| Repository" | \
                 grep -oP '\[.*?\]\(https://github\.com/[^)]+\)' | \
                 sed 's/\[//g' | sed 's/](.*)//g' || echo "")

if [ -z "${DEFERRED_REPOS}" ]; then
    # No deferred repos, skip notification
    exit 0
fi

# Count deferred repositories
DEFERRED_COUNT=$(echo "${DEFERRED_REPOS}" | wc -l)

# Run activity check if script exists
if [ -x "${ACTIVITY_SCRIPT}" ]; then
    ACTIVITY_REPORT=$(${ACTIVITY_SCRIPT} 2>&1 | tail -5)
else
    ACTIVITY_REPORT="Activity check not available (script missing)"
fi

# Send notification
TITLE="📅 Monthly Repository Review"
MESSAGE="Time to review ${DEFERRED_COUNT} deferred repositories.

Repositories to review:
${DEFERRED_REPOS}

Activity Check:
${ACTIVITY_REPORT}

Next Steps:
1. Check for POC triggers (agent orchestration priority)
2. Review repository activity
3. Archive if no longer relevant
4. Update WATCHED-REPOSITORIES.md

Related: DEC-145, Task #2"

if [ -x "${NOTIFY_SCRIPT}" ]; then
    ${NOTIFY_SCRIPT} "${TITLE}" "${MESSAGE}" --priority medium --channel auto
else
    # Fallback: log to file
    LOG_DIR="${HOME}/dev/infrastructure/dev-env-docs/logs"
    mkdir -p "${LOG_DIR}"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ${TITLE}" >> "${LOG_DIR}/repo-review-reminders.log"
    echo "${MESSAGE}" >> "${LOG_DIR}/repo-review-reminders.log"
    echo "---" >> "${LOG_DIR}/repo-review-reminders.log"
fi

exit 0
