#!/bin/bash
#
# cleanup-byterover.sh - Remove ByteRover v2 OAuth and cache
#
# Purpose: Clean up deprecated ByteRover v2 OAuth registration and cached state
# Usage: ./cleanup-byterover.sh [--dry-run]
#
# What it does:
# 1. Checks if Claude Code is running (warns user to exit)
# 2. Backs up .credentials.json
# 3. Removes ByteRover OAuth entry
# 4. Clears any ByteRover-related cache
# 5. Reports what was cleaned
#
# Safety: Creates backups before modifying files
# Author: Infrastructure automation
# Date: 2026-02-04

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
CHANGES_MADE=0
DRY_RUN=false

# Parse arguments
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo -e "${BLUE}=== DRY RUN MODE ===${NC}"
    echo "No changes will be made. Showing what would happen."
    echo ""
fi

# Function to log actions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
    ((CHANGES_MADE++))
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Claude Code is running
check_claude_running() {
    log_info "Checking if Claude Code is running..."

    if pgrep -f "claude-code" > /dev/null 2>&1; then
        log_error "Claude Code is currently running!"
        echo ""
        echo "Please exit Claude Code before running this script."
        echo "The CLI caches credentials in memory, so changes won't persist if it's running."
        echo ""
        echo "To exit Claude Code:"
        echo "  1. Type 'exit' in all active sessions"
        echo "  2. Close the terminal running Claude Code"
        echo "  3. Or run: pkill -f claude-code"
        echo ""

        if [[ "$DRY_RUN" == true ]]; then
            log_warning "Dry-run mode: continuing to show what would happen"
        else
            read -p "Continue anyway? (not recommended) [y/N] " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    else
        log_success "Claude Code is not running"
    fi
}

# Backup credentials file
backup_credentials() {
    local creds_file="$HOME/.claude/.credentials.json"
    local backup_file="${creds_file}.backup-$(date +%Y%m%d-%H%M%S)"

    if [[ ! -f "$creds_file" ]]; then
        log_warning "Credentials file not found: $creds_file"
        return
    fi

    log_info "Backing up credentials file..."

    if [[ "$DRY_RUN" == true ]]; then
        log_info "Would create: $backup_file"
    else
        cp "$creds_file" "$backup_file"
        log_success "Backup created: $backup_file"
    fi
}

# Remove ByteRover OAuth entry
remove_oauth_entry() {
    local creds_file="$HOME/.claude/.credentials.json"

    if [[ ! -f "$creds_file" ]]; then
        log_warning "Credentials file not found: $creds_file"
        return
    fi

    log_info "Checking for ByteRover OAuth entry..."

    # Check if jq is available
    if ! command -v jq &> /dev/null; then
        log_error "jq is not installed. Please install it: sudo apt install jq"
        exit 1
    fi

    # Check if ByteRover OAuth entry exists
    local has_byterover=$(jq -r '.mcpOAuth | has("byterover-mcp|32f85da8fce8cfae")' "$creds_file" 2>/dev/null || echo "false")

    if [[ "$has_byterover" == "true" ]]; then
        log_info "Found ByteRover OAuth entry - removing..."

        if [[ "$DRY_RUN" == true ]]; then
            log_info "Would remove ByteRover OAuth entry from $creds_file"
        else
            # Remove the specific ByteRover entry
            jq 'del(.mcpOAuth."byterover-mcp|32f85da8fce8cfae")' "$creds_file" > "${creds_file}.tmp"
            mv "${creds_file}.tmp" "$creds_file"
            log_success "Removed ByteRover OAuth entry"
        fi
    else
        log_info "No ByteRover OAuth entry found (already clean)"
    fi
}

# Clear cache directories
clear_cache() {
    log_info "Checking for ByteRover cache..."

    local cache_dirs=(
        "$HOME/.claude/cache"
        "$HOME/.config/claude/cache"
        "$HOME/.local/share/claude/cache"
    )

    local found_cache=false

    for cache_dir in "${cache_dirs[@]}"; do
        if [[ -d "$cache_dir" ]]; then
            # Search for ByteRover-related cache
            local byterover_files=$(find "$cache_dir" -type f -iname "*byterover*" 2>/dev/null || true)

            if [[ -n "$byterover_files" ]]; then
                found_cache=true
                log_info "Found ByteRover cache in: $cache_dir"

                while IFS= read -r file; do
                    if [[ "$DRY_RUN" == true ]]; then
                        log_info "Would remove: $file"
                    else
                        rm -f "$file"
                        log_success "Removed: $file"
                    fi
                done <<< "$byterover_files"
            fi
        fi
    done

    if [[ "$found_cache" == false ]]; then
        log_info "No ByteRover cache files found (already clean)"
    fi
}

# Check browser instructions
show_browser_instructions() {
    echo ""
    log_info "Manual cleanup needed:"
    echo ""
    echo "  ${YELLOW}Browser Cache/Cookies${NC}"
    echo "  Clear browser data for these domains:"
    echo "    - byterover.dev"
    echo "    - app.byterover.dev"
    echo "    - app-v2.byterover.dev"
    echo "    - mcp.byterover.dev"
    echo ""
    echo "  How to clear (Chrome/Edge):"
    echo "    1. Press F12 (DevTools)"
    echo "    2. Right-click the refresh button"
    echo "    3. Select 'Empty Cache and Hard Reload'"
    echo "    4. Or visit chrome://settings/siteData and search 'byterover'"
    echo ""
}

# Summary
show_summary() {
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${BLUE}DRY RUN SUMMARY${NC}"
        echo "Would make $CHANGES_MADE changes"
    else
        echo -e "${GREEN}CLEANUP SUMMARY${NC}"
        echo "Made $CHANGES_MADE changes"
    fi
    echo "═══════════════════════════════════════════════════════════"
    echo ""

    if [[ "$DRY_RUN" == false && "$CHANGES_MADE" -gt 0 ]]; then
        log_success "ByteRover cleanup complete!"
        echo ""
        echo "Next steps:"
        echo "  1. Clear browser cache/cookies (see above)"
        echo "  2. Restart Claude Code"
        echo "  3. Verify ByteRover OAuth no longer triggers"
        echo ""
        echo "If OAuth registration still appears:"
        echo "  - Check ACTIVE-SERVERS.md for any references"
        echo "  - File an issue: ISSUE-xxx in dev-env-docs/ISSUES-TRACKER.md"
        echo ""
        echo "Backups created in:"
        echo "  ~/.claude/.credentials.json.backup-*"
    elif [[ "$DRY_RUN" == true ]]; then
        echo "Run without --dry-run to apply changes"
    else
        log_info "No changes needed - ByteRover already cleaned"
    fi
}

# Main execution
main() {
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  ByteRover v2 Cleanup Script"
    echo "  Removes deprecated OAuth registration and cache"
    echo "═══════════════════════════════════════════════════════════"
    echo ""

    check_claude_running
    echo ""

    backup_credentials
    echo ""

    remove_oauth_entry
    echo ""

    clear_cache
    echo ""

    show_browser_instructions

    show_summary
}

# Run main function
main "$@"
