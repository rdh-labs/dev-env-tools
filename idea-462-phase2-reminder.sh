#!/bin/bash
# IDEA-462 Phase 2 Decision Reminder + Metrics Check
# Triggers 2 weeks before decision date (2026-03-16)
# Also runs weekly threshold checks with metrics

set -euo pipefail

DECISION_DATE="2026-03-16"
TODAY=$(date +%Y-%m-%d)
PROPOSAL_DOC="$HOME/dev/infrastructure/dev-env-docs/proposals/IDEA-462-phase1-IMPLEMENTATION-COMPLETE.md"
METRICS_SCRIPT="$HOME/.metrics/quality-gates-capability-metrics.sh"

# Calculate days until decision
DAYS_UNTIL=$(( ($(date -d "$DECISION_DATE" +%s) - $(date -d "$TODAY" +%s)) / 86400 ))

# Always run threshold check (weekly via cron)
if [[ -x "$METRICS_SCRIPT" ]]; then
    echo "Running weekly Phase 1 metrics check..."
    "$METRICS_SCRIPT" check-thresholds
else
    echo "Warning: Metrics script not found at $METRICS_SCRIPT" >&2
fi

# Trigger decision reminder 2 weeks before deadline
if [ $DAYS_UNTIL -le 14 ] && [ $DAYS_UNTIL -ge 0 ]; then
    # Get current metrics for notification
    METRICS_OUTPUT=$("$METRICS_SCRIPT" phase1-status 2>/dev/null | grep -A10 "Reminders\|Success Rate" || echo "Unable to fetch metrics")

    ~/bin/notify.sh "IDEA-462 Phase 2 Decision Approaching" \
        "Decision date: $DECISION_DATE ($DAYS_UNTIL days remaining)

Current Phase 1 Metrics:
$METRICS_OUTPUT

Review: $PROPOSAL_DOC
Run: quality-gates-capability-metrics.sh phase1-status

Decision: Proceed to Phase 2 (governance agent) or close IDEA-462?" \
        --priority high --channel auto
fi
