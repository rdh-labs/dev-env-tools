#!/usr/bin/env bash
# GitKraken Workspace Auto-Sync
# Automatically discovers and syncs git repositories into GitKraken workspaces
#
# Purpose: Achieve 100% repository coverage for AI agents (DEC-###)
# Related: DEC-086 (GitKraken MCP Integration), DEC-085 (Perception Gap Protocol)
# Metrics: ~/.metrics/gitkraken-mcp-events.jsonl

set -euo pipefail

# Configuration
LOG_FILE="$HOME/.logs/gk-sync.log"
METRICS_FILE="$HOME/.metrics/gk-workspace-sync-events.jsonl"
DRY_RUN="${DRY_RUN:-false}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Ensure log and metrics directories exist
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$METRICS_FILE")"

# Initialize log (write to file only, not stdout)
log() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*" >> "$LOG_FILE"
}

# Log and echo (for user-facing messages)
log_echo() {
    local msg="[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

# Record metrics event
record_metric() {
    local status="$1"
    local repos_processed="$2"
    local repos_added="$3"
    local elapsed_seconds="$4"

    local event=$(jq -n \
        --arg ts "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
        --arg status "$status" \
        --arg processed "$repos_processed" \
        --arg added "$repos_added" \
        --arg elapsed "$elapsed_seconds" \
        '{
            timestamp: $ts,
            status: $status,
            repos_processed: ($processed | tonumber),
            repos_added: ($added | tonumber),
            elapsed_seconds: ($elapsed | tonumber)
        }')

    echo "$event" >> "$METRICS_FILE"
}

# Categorize repository into appropriate workspace
categorize_repo() {
    local repo="$1"

    case "$repo" in
        # Infrastructure repositories
        */dev/infrastructure/*)
            echo "Infrastructure-Repos"
            ;;

        # True Valence Mapper projects
        */dev/projects/true-valence-mapper*)
            echo "True-Valence-Mapper-Projects"
            ;;

        # Proactive Resolution projects
        */dev/projects/proactive*)
            echo "Proactive-Projects"
            ;;

        # CHEEV projects
        */dev/projects/CHEEV*)
            echo "Client-Projects"
            ;;

        # Other client/product projects
        */dev/projects/*)
            echo "Client-Projects"
            ;;

        # Sandbox/POC repositories
        */dev/sandbox/*)
            echo "Sandbox-POCs"
            ;;

        # MCP servers
        */.local/share/mcp-servers/*)
            echo "MCP-Servers"
            ;;

        # Claude configuration
        */.claude*)
            echo "Infrastructure-Repos"
            ;;

        # Uncategorized (fallback)
        *)
            echo "Uncategorized"
            ;;
    esac
}

# Check if GitKraken CLI is available
check_gk_available() {
    if ! command -v gk &> /dev/null; then
        log "${RED}ERROR: GitKraken CLI (gk) not found in PATH${NC}"
        log "Install: https://help.gitkraken.com/gitkraken-desktop/gitkraken-cli/"
        exit 1
    fi

    # Test GitKraken CLI connectivity
    if ! gk --version &> /dev/null; then
        log "${RED}ERROR: GitKraken CLI not responding${NC}"
        exit 1
    fi
}

# Discover all git repositories
discover_repos() {
    log "Discovering git repositories in ~/dev and ~/.local/share/mcp-servers..."

    # Find all .git directories with safeguards
    local repos=()
    local search_paths=(
        "$HOME/dev/infrastructure"
        "$HOME/dev/projects"
        "$HOME/dev/sandbox"
        "$HOME/.local/share/mcp-servers"
        "$HOME/.claude"
    )

    for search_path in "${search_paths[@]}"; do
        if [[ ! -d "$search_path" ]]; then
            continue
        fi

        log "  Scanning: $search_path"

        # Find with depth limit and exclusions
        while IFS= read -r git_dir; do
            local repo="${git_dir%/.git}"

            # Skip if inside node_modules
            if [[ "$repo" =~ node_modules ]]; then
                continue
            fi

            # Skip if in archive
            if [[ "$repo" =~ /archive/ ]] || [[ "$repo" =~ /\.archive/ ]]; then
                continue
            fi

            # Skip .git directories themselves
            if [[ "$repo" =~ /\.git/ ]]; then
                continue
            fi

            repos+=("$repo")
        done < <(timeout 60 find "$search_path" -maxdepth 6 -name .git -type d \
            -not -path "*/node_modules/*" \
            -not -path "*/\.git/*" \
            -not -path "*/archive/*" \
            2>/dev/null || true)
    done

    # Deduplicate and sort
    local unique_repos=($(printf '%s\n' "${repos[@]}" | sort -u))

    log "  Found ${#unique_repos[@]} repositories"
    printf '%s\n' "${unique_repos[@]}"
}

# Check if repo already exists in workspace
# Note: Simplified to always attempt add (gk workspace update is idempotent)
repo_in_workspace() {
    local workspace="$1"
    local repo="$2"

    # Strip ANSI codes and check if repo path exists in workspace info
    if gk workspace info "$workspace" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep -qF "$repo"; then
        return 0  # Found
    else
        return 1  # Not found
    fi
}

# Add repository to workspace
add_repo_to_workspace() {
    local workspace="$1"
    local repo="$2"

    if [[ "$DRY_RUN" == "true" ]]; then
        log "${BLUE}[DRY-RUN] Would add: $repo -> $workspace${NC}"
        return 0
    fi

    log "${GREEN}Adding: $repo -> $workspace${NC}"

    # Create workspace if it doesn't exist
    if ! gk workspace list 2>/dev/null | grep -qF "$workspace"; then
        log "Creating workspace: $workspace"
        gk workspace create "$workspace" --add-repos "$repo" || {
            log "${RED}ERROR: Failed to create workspace $workspace${NC}"
            return 1
        }
        return 0  # Workspace created with repo, no need to update
    fi

    # Add repo to workspace (note: --add-repos is plural)
    if gk workspace update "$workspace" --add-repos "$repo" 2>&1 >> "$LOG_FILE"; then
        return 0
    else
        log "${RED}ERROR: Failed to add $repo to $workspace${NC}"
        return 1
    fi
}

# Main sync function
sync_workspaces() {
    local start_time=$(date +%s)
    local repos_processed=0
    local repos_added=0

    log_echo "======================================="
    log_echo "GitKraken Workspace Sync Starting"
    log_echo "======================================="

    # Check prerequisites
    check_gk_available

    # Discover repositories
    local repos
    mapfile -t repos < <(discover_repos)

    if [[ ${#repos[@]} -eq 0 ]]; then
        log "${YELLOW}WARNING: No repositories discovered${NC}"
        record_metric "no_repos" 0 0 0
        return 0
    fi

    log "Found ${#repos[@]} repositories"

    # Process each repository
    for repo in "${repos[@]}"; do
        repos_processed=$((repos_processed + 1))
        log "Processing repo $repos_processed/${#repos[@]}: $repo"

        # Determine workspace
        local workspace=$(categorize_repo "$repo")
        log "  → Workspace: $workspace"

        # Check if already in workspace (with timeout)
        if timeout 10 bash -c "gk workspace info '$workspace' 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep -qF '$repo'" 2>/dev/null; then
            log "  ✓ Already in workspace"
        else
            log "  → Not in workspace, would add"
            # Add to workspace
            if add_repo_to_workspace "$workspace" "$repo"; then
                repos_added=$((repos_added + 1))
            fi
        fi
    done

    # Calculate elapsed time
    local end_time=$(date +%s)
    local elapsed=$((end_time - start_time))

    # Summary
    log_echo "======================================="
    log_echo "Sync Complete"
    log_echo "  Processed: $repos_processed repositories"
    log_echo "  Added: $repos_added repositories"
    log_echo "  Elapsed: ${elapsed}s"
    log_echo "======================================="

    # Record metrics
    if [[ $repos_added -gt 0 ]]; then
        record_metric "success_with_additions" "$repos_processed" "$repos_added" "$elapsed"
    else
        record_metric "success_no_changes" "$repos_processed" 0 "$elapsed"
    fi
}

# Display usage
usage() {
    cat << EOF
GitKraken Workspace Auto-Sync

Usage:
  $(basename "$0") [OPTIONS]

Options:
  --dry-run     Show what would be done without making changes
  --help        Display this help message

Environment Variables:
  DRY_RUN       Set to 'true' to enable dry-run mode

Examples:
  # Run sync
  $(basename "$0")

  # Dry-run to preview changes
  $(basename "$0") --dry-run

  # Add to crontab for daily sync at 9am
  0 9 * * * $HOME/dev/infrastructure/tools/gk-workspace-sync.sh >> $LOG_FILE 2>&1

Logs:
  Execution: $LOG_FILE
  Metrics: $METRICS_FILE

EOF
}

# Parse arguments
case "${1:-}" in
    --dry-run)
        DRY_RUN=true
        sync_workspaces
        ;;
    --help|-h)
        usage
        ;;
    "")
        sync_workspaces
        ;;
    *)
        echo "Unknown option: $1"
        usage
        exit 1
        ;;
esac
