#!/bin/bash
# Dart MCP Health Check
# Pings the Dart MCP endpoint and notifies on failure.
# Deduplicates: only notifies once per failure window (re-alerts after recovery + re-failure).
#
# Usage:
#   dart-health-check.sh          # Check and notify if down
#   dart-health-check.sh --quiet  # Check only, exit code 0=healthy 1=unhealthy
#
# Cron (every 6 hours):
#   0 */6 * * * ~/bin/dart-health-check.sh >> ~/.metrics/logs/dart-health.log 2>&1
#
# References: ISSUE-3036, ISSUE-R002

set -euo pipefail

DART_MCP_URL="https://mcp.dartai.com/mcp"
DART_AUTH_HEADER=$(python3 -c "
import json
d = json.load(open('$HOME/.claude.json'))
print(d.get('mcpServers', {}).get('dart', {}).get('headers', {}).get('Authorization', ''))
" 2>/dev/null || echo "")

NOTIFY_SCRIPT="$HOME/bin/notify.sh"
STATE_DIR="$HOME/.cache/dart"
STATE_FILE="$STATE_DIR/health-state"
LOG_DIR="$HOME/.metrics/logs"

mkdir -p "$STATE_DIR" "$LOG_DIR"

QUIET=false
[[ "${1:-}" == "--quiet" ]] && QUIET=true

TIMESTAMP=$(date -Iseconds)

if [[ -z "$DART_AUTH_HEADER" ]]; then
    echo "[$TIMESTAMP] ERROR: No Dart auth header in ~/.claude.json" >&2
    exit 1
fi

# Ping endpoint - any non-5xx, non-timeout response = healthy
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: ${DART_AUTH_HEADER}" \
    --max-time 15 \
    "${DART_MCP_URL}" 2>/dev/null || echo "000")

PREV_STATE="unknown"
[[ -f "$STATE_FILE" ]] && PREV_STATE=$(cat "$STATE_FILE")

if [[ "$HTTP_STATUS" == "000" || "$HTTP_STATUS" == 5* ]]; then
    # UNHEALTHY
    echo "[$TIMESTAMP] UNHEALTHY: Dart MCP returned HTTP $HTTP_STATUS"

    if [[ "$PREV_STATE" != "unhealthy" ]]; then
        echo "unhealthy" > "$STATE_FILE"
        if [[ "$QUIET" == false && -x "$NOTIFY_SCRIPT" ]]; then
            "$NOTIFY_SCRIPT" "Dart MCP Down" \
                "Dart MCP endpoint returned HTTP $HTTP_STATUS. Write operations will fail. Fallback: ~/.claude/dart-task-queue.md. Ref: ISSUE-3036" \
                --priority high --channel auto
        fi
    else
        echo "[$TIMESTAMP] (already notified, suppressing duplicate)"
    fi
    exit 1
else
    # HEALTHY
    echo "[$TIMESTAMP] HEALTHY: Dart MCP returned HTTP $HTTP_STATUS"

    if [[ "$PREV_STATE" == "unhealthy" ]]; then
        echo "healthy" > "$STATE_FILE"
        if [[ "$QUIET" == false && -x "$NOTIFY_SCRIPT" ]]; then
            "$NOTIFY_SCRIPT" "Dart MCP Recovered" \
                "Dart MCP endpoint is back (HTTP $HTTP_STATUS). Write operations should work. Retry any tasks in ~/.claude/dart-task-queue.md" \
                --priority default --channel auto
        fi
    else
        echo "healthy" > "$STATE_FILE"
    fi
    exit 0
fi
