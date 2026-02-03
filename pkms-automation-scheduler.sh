#!/bin/bash
# PKMS Automation Scheduler - Verifies and configures PKMS automation jobs
# Created: 2026-01-31 (Phase 1b)
# Purpose: Idempotent scheduler for readwise_sync.py and pkms-weekly-summary.sh

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Configuration
READWISE_SCRIPT="/home/ichardart/dev/infrastructure/constellation/readwise_sync.py"
WEEKLY_SCRIPT="/home/ichardart/dev/infrastructure/pkms-exploration/scripts/pkms-weekly-summary.sh"
LOG_DIR="/home/ichardart/.local/logs"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Function to check if cron job exists
check_cron() {
    local pattern="$1"
    if crontab -l 2>/dev/null | grep -qF "$pattern"; then
        return 0  # exists
    else
        return 1  # doesn't exist
    fi
}

# Function to add cron job safely
add_cron() {
    local job="$1"
    (crontab -l 2>/dev/null || true; echo "$job") | crontab -
}

echo "═══════════════════════════════════════════════════════════"
echo "  PKMS Automation Scheduler"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Check 1: Readwise sync
echo -n "Checking readwise_sync.py schedule... "
if check_cron "readwise_sync.py"; then
    echo -e "${GREEN}✓ SCHEDULED${NC}"
else
    echo -e "${YELLOW}⚠ NOT SCHEDULED${NC}"
    echo "  Adding to crontab..."
    READWISE_JOB="0 8 * * * cd /home/ichardart/dev/infrastructure/constellation && /usr/bin/python3 readwise_sync.py sync-queue >> $LOG_DIR/readwise-sync.log 2>&1"
    add_cron "$READWISE_JOB"
    echo -e "  ${GREEN}✓ Added: Daily at 8:00 AM${NC}"
fi

# Check 2: Weekly summary
echo -n "Checking pkms-weekly-summary.sh schedule... "
if check_cron "pkms-weekly-summary.sh"; then
    echo -e "${GREEN}✓ SCHEDULED${NC}"
else
    echo -e "${YELLOW}⚠ NOT SCHEDULED${NC}"
    echo "  Adding to crontab..."
    WEEKLY_JOB="0 9 * * 5 $WEEKLY_SCRIPT >> $LOG_DIR/pkms-weekly.log 2>&1"
    add_cron "$WEEKLY_JOB"
    echo -e "  ${GREEN}✓ Added: Fridays at 9:00 AM${NC}"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Current PKMS Crontab Entries"
echo "═══════════════════════════════════════════════════════════"
echo ""
crontab -l 2>/dev/null | grep -E "(readwise|pkms)" || echo "  (none found)"
echo ""

# Verification
echo "═══════════════════════════════════════════════════════════"
echo "  Verification"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Check if scripts exist
echo -n "Readwise script exists: "
if [[ -f "$READWISE_SCRIPT" ]]; then
    echo -e "${GREEN}✓${NC} $READWISE_SCRIPT"
else
    echo -e "${RED}✗${NC} $READWISE_SCRIPT (MISSING!)"
fi

echo -n "Weekly summary script exists: "
if [[ -f "$WEEKLY_SCRIPT" ]]; then
    echo -e "${GREEN}✓${NC} $WEEKLY_SCRIPT"
else
    echo -e "${RED}✗${NC} $WEEKLY_SCRIPT (MISSING!)"
fi

# Check 1Password token
echo -n "1Password service account: "
if source ~/.bashrc_claude && op whoami &>/dev/null; then
    echo -e "${GREEN}✓ Valid${NC}"
else
    echo -e "${RED}✗ Invalid or not configured${NC}"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Automation Status Summary"
echo "═══════════════════════════════════════════════════════════"
echo ""

if check_cron "readwise_sync.py" && check_cron "pkms-weekly-summary.sh"; then
    echo -e "${GREEN}✓ All PKMS automation is configured and scheduled${NC}"
    echo ""
    echo "Next runs:"
    echo "  • Readwise sync: Daily at 8:00 AM"
    echo "  • Weekly summary: Fridays at 9:00 AM"
    echo ""
    echo "Logs:"
    echo "  • $LOG_DIR/readwise-sync.log"
    echo "  • $LOG_DIR/pkms-weekly.log"
else
    echo -e "${YELLOW}⚠ Some automation not scheduled (see above)${NC}"
    echo ""
    echo "Run this script again to schedule missing jobs."
fi

echo ""
