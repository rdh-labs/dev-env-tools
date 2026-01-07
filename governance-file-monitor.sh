#!/bin/bash
# Governance File Monitor - Detects rogue governance files outside canonical locations
# Created: 2026-01-07 (DEC-076, IDEA-126)
# Usage: governance-file-monitor.sh [--fix] [--quiet]
#   --fix:   Auto-remediate by creating symlinks (after alerting)
#   --quiet: Suppress output except for alerts

set -euo pipefail

# Notification Configuration
NTFY_TOPIC="rdh-governance-a449c7a6cd934c3a"
NTFY_ENABLED=true
GITHUB_ISSUE_ENABLED=true
GITHUB_REPO="rdh-labs/dev-env-docs"

# Send notification via ntfy.sh
send_ntfy() {
    local title="$1"
    local message="$2"
    local priority="${3:-default}"  # default, low, high, urgent
    local tags="${4:-}"

    if [ "$NTFY_ENABLED" = true ]; then
        curl -s -o /dev/null \
            -H "Title: $title" \
            -H "Priority: $priority" \
            -H "Tags: $tags" \
            -d "$message" \
            "https://ntfy.sh/$NTFY_TOPIC" 2>/dev/null || true
    fi
}

# Create GitHub Issue for failures
create_github_issue() {
    local title="$1"
    local body="$2"

    if [ "$GITHUB_ISSUE_ENABLED" = true ] && command -v gh &>/dev/null; then
        gh issue create --repo "$GITHUB_REPO" \
            --title "$title" \
            --body "$body" \
            --label "governance,automated" 2>/dev/null || true
    fi
}

# Configuration: Canonical locations for governance files
declare -A CANONICAL_PATHS=(
    ["IDEAS-BACKLOG.md"]="$HOME/dev/infrastructure/dev-env-docs/IDEAS-BACKLOG.md"
    ["DECISIONS-LOG.md"]="$HOME/dev/infrastructure/dev-env-docs/DECISIONS-LOG.md"
    ["ISSUES-TRACKER.md"]="$HOME/dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md"
    ["CHANGE-LOG.md"]="$HOME/dev/infrastructure/dev-env-docs/CHANGE-LOG.md"
    ["lessons.md"]="$HOME/dev/infrastructure/dev-env-config/lessons.md"
)

# Allowed symlink locations (these should point to canonical)
declare -A ALLOWED_SYMLINKS=(
    ["IDEAS-BACKLOG.md"]="$HOME/dev/IDEAS-BACKLOG.md"
    ["DECISIONS-LOG.md"]=""  # No symlink expected
    ["ISSUES-TRACKER.md"]=""  # No symlink expected
    ["CHANGE-LOG.md"]="$HOME/dev/CHANGE-LOG.md"
    ["lessons.md"]="$HOME/lessons.md"
)

# Search paths to check for rogue files
SEARCH_PATHS=(
    "$HOME"
    "$HOME/dev"
    "$HOME/dev/projects"
)

# Parse arguments
FIX_MODE=false
QUIET_MODE=false
for arg in "$@"; do
    case $arg in
        --fix) FIX_MODE=true ;;
        --quiet) QUIET_MODE=true ;;
    esac
done

LOGFILE="$HOME/.governance-file-monitor.log"
ALERT_COUNT=0

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" >> "$LOGFILE"
    if [ "$QUIET_MODE" = false ]; then
        echo "$msg"
    fi
}

alert() {
    local msg="[ALERT] $1"
    echo "$msg" >> "$LOGFILE"
    echo "$msg"  # Always show alerts
    ((ALERT_COUNT++)) || true
}

log "Starting governance file monitor..."

# Check each governance file
for filename in "${!CANONICAL_PATHS[@]}"; do
    canonical="${CANONICAL_PATHS[$filename]}"
    allowed_symlink="${ALLOWED_SYMLINKS[$filename]}"

    log "Checking: $filename"

    # Verify canonical exists
    if [ ! -f "$canonical" ]; then
        alert "CANONICAL MISSING: $canonical"
        continue
    fi

    # Check for rogue files in search paths
    for search_path in "${SEARCH_PATHS[@]}"; do
        # Find files with this name (not in infrastructure/)
        while IFS= read -r found_file; do
            # Skip if it's the canonical file
            if [ "$found_file" = "$canonical" ]; then
                continue
            fi

            # Skip if it's inside infrastructure (could be project-specific, intentional)
            if [[ "$found_file" == *"/dev/infrastructure/"* ]]; then
                # Allow if it's in a project-specific investigations folder
                if [[ "$found_file" == *"/05-investigations/"* ]]; then
                    log "  OK: Project-specific: $found_file"
                    continue
                fi
                # Allow if it's in .wip (worktree)
                if [[ "$found_file" == *"/.wip/"* ]]; then
                    log "  OK: WIP worktree: $found_file"
                    continue
                fi
            fi

            # Skip .wip directories (git worktrees)
            if [[ "$found_file" == *"/.wip/"* ]]; then
                log "  OK: WIP worktree: $found_file"
                continue
            fi

            # Check if it's an allowed symlink
            if [ -n "$allowed_symlink" ] && [ "$found_file" = "$allowed_symlink" ]; then
                # Verify it's actually a symlink pointing to canonical
                if [ -L "$found_file" ]; then
                    target=$(readlink -f "$found_file" 2>/dev/null || echo "")
                    if [ "$target" = "$canonical" ]; then
                        log "  OK: Valid symlink: $found_file -> $canonical"
                        continue
                    else
                        alert "BROKEN SYMLINK: $found_file points to $target (should be $canonical)"
                    fi
                else
                    alert "ROGUE FILE (should be symlink): $found_file"
                    if [ "$FIX_MODE" = true ]; then
                        log "  FIX: Converting to symlink..."
                        # Backup rogue file
                        backup="$found_file.rogue-$(date +%Y%m%d-%H%M%S)"
                        mv "$found_file" "$backup"
                        ln -s "$canonical" "$found_file"
                        log "  FIX: Backed up to $backup, created symlink"
                    fi
                fi
                continue
            fi

            # It's a rogue file
            file_size=$(wc -c < "$found_file" 2>/dev/null || echo 0)
            file_lines=$(wc -l < "$found_file" 2>/dev/null || echo 0)
            alert "ROGUE FILE: $found_file (${file_lines} lines, ${file_size} bytes)"

        done < <(find "$search_path" -maxdepth 2 -name "$filename" -type f 2>/dev/null)
    done
done

# Summary
log "Monitor complete. Alerts: $ALERT_COUNT"

if [ "$ALERT_COUNT" -gt 0 ]; then
    echo ""
    echo "========================================"
    echo "GOVERNANCE FILE MONITOR: $ALERT_COUNT ALERT(S)"
    echo "========================================"
    echo "Review log: $LOGFILE"
    echo "Run with --fix to auto-remediate symlink issues"

    # Send failure notifications
    ALERT_DETAILS=$(grep "\[ALERT\]" "$LOGFILE" | tail -10)
    send_ntfy "🚨 Governance Alert" "$ALERT_COUNT rogue governance file(s) detected. Check $LOGFILE" "high" "warning,file_folder"

    ISSUE_BODY="## Governance File Monitor Alert

**Date:** $(date '+%Y-%m-%d %H:%M:%S')
**Alerts:** $ALERT_COUNT

### Details
\`\`\`
$ALERT_DETAILS
\`\`\`

### Resolution
Run: \`~/dev/infrastructure/tools/governance-file-monitor.sh --fix\`

---
*Auto-generated by governance-file-monitor.sh*"

    create_github_issue "[Automated] Governance Alert: $ALERT_COUNT rogue file(s) detected" "$ISSUE_BODY"

    exit 1
else
    # Send success notification
    send_ntfy "✅ Governance OK" "All 5 governance files in correct locations" "low" "white_check_mark"

    if [ "$QUIET_MODE" = false ]; then
        echo "All governance files OK"
    fi
    exit 0
fi
