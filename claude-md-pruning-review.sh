#!/usr/bin/env bash
# claude-md-pruning-review.sh
# Quarterly governance review helper for CLAUDE.md
# ISSUE-2179 item 3 — runs quarterly via cron, or invoke manually when CLAUDE.md needs trimming
#
# Usage: ./claude-md-pruning-review.sh [--notify]
#   --notify: send a push notification with the summary (used by cron)

set -euo pipefail

CLAUDE_MD="${HOME}/dev/infrastructure/dev-env-config/claude/CLAUDE.md"
BLOCK_THRESHOLD=40000
WARN_THRESHOLD=38000
NOTIFY_FLAG="${1:-}"

if [[ ! -f "$CLAUDE_MD" ]]; then
    echo "ERROR: CLAUDE.md not found at $CLAUDE_MD" >&2
    exit 1
fi

current_chars=$(wc -m < "$CLAUDE_MD")
headroom=$((BLOCK_THRESHOLD - current_chars))

echo "================================================================"
echo "CLAUDE.md Quarterly Pruning Review"
echo "Date: $(date '+%Y-%m-%d %H:%M')"
echo "================================================================"
echo ""
echo "Current size : ${current_chars} chars"
echo "Hard limit   : ${BLOCK_THRESHOLD} chars"
echo "Headroom     : ${headroom} chars"
echo ""

if (( current_chars >= BLOCK_THRESHOLD )); then
    echo "STATUS: OVER LIMIT — trim immediately before any new content can be added"
    severity="CRIT"
elif (( current_chars >= WARN_THRESHOLD )); then
    echo "STATUS: WARNING — within 2K of limit, trim before next addition"
    severity="HIGH"
else
    echo "STATUS: OK"
    severity="OK"
fi

echo ""
echo "--- Section Breakdown (chars, largest first) ---"
awk '
    /^## / {
        if (section != "") printf "  %6d  %s\n", chars, section
        section = substr($0, 1, 80)
        chars = length($0) + 1
        next
    }
    { chars += length($0) + 1 }
    END { if (section != "") printf "  %6d  %s\n", chars, section }
' "$CLAUDE_MD" | sort -rn

echo ""
echo "--- Pruning Candidates (check each) ---"
echo "  1. Sections with inline detail that should move to governance-patterns/ or capabilities/"
echo "     Pattern: look for subsections >500 chars without a 'Full protocol:' pointer"
echo "  2. Rules referencing RESOLVED ISSUEs/DECs that can be summarized or removed"
echo "     Scan: grep -n 'ISSUE-\|DEC-' CLAUDE.md | check each against ISSUES-TRACKER.md"
echo "  3. Duplicate rules across sections (same constraint stated twice)"
echo "  4. Anti-pattern examples with verbose scenarios — shorten or convert to one-liner"
echo "  5. Quick Reference entries that belong in AGENTS.md or capabilities/ docs"
echo "  6. MANDATORY labels on items that are already enforced by hooks (redundant)"
echo ""
echo "--- Standard Procedure ---"
echo "  Agent: run this script, identify top 3 candidates"
echo "  Agent: externalize each to governance-patterns/ or capabilities/ with a 'Full protocol:' pointer"
echo "  Agent: verify CLAUDE.md shrinks and the pointer resolves correctly"
echo "  Agent: commit to dev-env-config repo (rdh-labs/dev-env-config.git)"
echo "  Agent: update IDEA-605 status in IDEAS-BACKLOG.md if applicable"
echo ""
echo "  Trim target: keep CLAUDE.md below ${WARN_THRESHOLD} chars (current headroom: ${headroom})"
echo ""

# Send notification for quarterly cron runs or when severity is not OK
if [[ "$NOTIFY_FLAG" == "--notify" ]] || [[ "$severity" != "OK" ]]; then
    NOTIFY_BIN="${HOME}/bin/notify.sh"
    if [[ -x "$NOTIFY_BIN" ]]; then
        msg="CLAUDE.md: ${current_chars}/${BLOCK_THRESHOLD} chars (${headroom} remaining). Status: ${severity}"
        "$NOTIFY_BIN" "CLAUDE.md Quarterly Review" "$msg" --priority medium --channel auto 2>/dev/null || true
    fi
fi

echo "Review complete."
