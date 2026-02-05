#!/usr/bin/env bash
# check-watched-repo-activity.sh
# Automated GitHub repository activity checker for WATCHED-REPOSITORIES.md
# Part of DEC-145 automation implementation
# OPTIMIZED: Parallel processing support via GNU parallel or bash jobs

set -euo pipefail

# Configuration
WATCHED_REPOS_FILE="${HOME}/dev/infrastructure/dev-env-docs/WATCHED-REPOSITORIES.md"
OUTPUT_DIR="${HOME}/dev/infrastructure/dev-env-docs/logs"
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
REPORT_FILE="${OUTPUT_DIR}/watched-repos-activity-${TIMESTAMP}.md"
TEMP_DIR="${OUTPUT_DIR}/.tmp-${TIMESTAMP}"
MAX_PARALLEL_JOBS=4  # Adjust based on API rate limits and system resources

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Ensure output directory exists
mkdir -p "${OUTPUT_DIR}"
mkdir -p "${TEMP_DIR}"

# Cleanup temp directory on exit
trap 'rm -rf "${TEMP_DIR}"' EXIT

echo "📊 Watched Repositories Activity Check"
echo "======================================="
echo "Date: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# Check for parallel processing capability
if command -v parallel &> /dev/null; then
    PARALLEL_METHOD="gnu-parallel"
    echo "Parallel processing: GNU Parallel (${MAX_PARALLEL_JOBS} jobs)"
else
    PARALLEL_METHOD="bash-jobs"
    echo "Parallel processing: Bash job control (${MAX_PARALLEL_JOBS} jobs)"
fi
echo ""

# Function to check a single repository
# This function is designed to be called in parallel
check_repository() {
    local REPO_URL="$1"
    local TEMP_DIR="$2"

    # Extract owner/repo from URL
    local OWNER_REPO=$(echo "${REPO_URL}" | sed 's|https://github.com/||' | sed 's|/$||')
    local REPO_NAME=$(basename "${OWNER_REPO}")
    local TEMP_FILE="${TEMP_DIR}/${REPO_NAME//\//_}.txt"

    # Initialize result variables
    local STATUS=""
    local RESULT_TYPE=""  # updated, unchanged, error

    # Use gh CLI to get repository info
    if command -v gh &> /dev/null; then
        # Get last commit date, stars, and latest release
        local REPO_INFO=$(gh repo view "${OWNER_REPO}" --json name,pushedAt,stargazerCount,latestRelease 2>/dev/null || echo "ERROR")

        if [ "${REPO_INFO}" = "ERROR" ]; then
            RESULT_TYPE="error"
            cat > "${TEMP_FILE}" <<EOF
TYPE:error
STATUS:❌ Error fetching data
REPO:${OWNER_REPO}
URL:${REPO_URL}
EOF
        else
            # Parse JSON (requires jq)
            if command -v jq &> /dev/null; then
                local LAST_COMMIT=$(echo "${REPO_INFO}" | jq -r '.pushedAt // "Unknown"')
                local STARS=$(echo "${REPO_INFO}" | jq -r '.stargazerCount // 0')
                local LATEST_RELEASE=$(echo "${REPO_INFO}" | jq -r '.latestRelease.tagName // "None"')

                # Format last commit date
                if [ "${LAST_COMMIT}" != "Unknown" ]; then
                    local LAST_COMMIT_FORMATTED=$(date -d "${LAST_COMMIT}" '+%Y-%m-%d' 2>/dev/null || echo "${LAST_COMMIT}")
                else
                    local LAST_COMMIT_FORMATTED="Unknown"
                fi

                # Determine if recently updated (within last 30 days)
                if [ "${LAST_COMMIT_FORMATTED}" != "Unknown" ]; then
                    local DAYS_AGO=$(( ( $(date +%s) - $(date -d "${LAST_COMMIT_FORMATTED}" +%s) ) / 86400 ))
                    if [ ${DAYS_AGO} -le 30 ]; then
                        STATUS="🟢 Recently Updated"
                        RESULT_TYPE="updated"
                    else
                        STATUS="⚪ No Recent Activity"
                        RESULT_TYPE="unchanged"
                    fi
                else
                    STATUS="⚪ Status Unknown"
                    RESULT_TYPE="unchanged"
                    DAYS_AGO="?"
                fi

                cat > "${TEMP_FILE}" <<EOF
TYPE:${RESULT_TYPE}
STATUS:${STATUS}
REPO:${OWNER_REPO}
LAST_COMMIT:${LAST_COMMIT_FORMATTED}
DAYS_AGO:${DAYS_AGO}
STARS:${STARS}
RELEASE:${LATEST_RELEASE}
URL:${REPO_URL}
EOF
            else
                RESULT_TYPE="unchanged"
                cat > "${TEMP_FILE}" <<EOF
TYPE:unchanged
STATUS:⚪ jq not available
REPO:${OWNER_REPO}
URL:${REPO_URL}
EOF
            fi
        fi
    else
        RESULT_TYPE="error"
        cat > "${TEMP_FILE}" <<EOF
TYPE:error
STATUS:❌ gh CLI not available
REPO:${OWNER_REPO}
URL:${REPO_URL}
NOTE:Install gh CLI: https://cli.github.com/
EOF
    fi
}

# Export function for parallel execution
export -f check_repository

# Initialize report
cat > "${REPORT_FILE}" <<EOF
# Watched Repositories Activity Report

**Generated:** $(date '+%Y-%m-%d %H:%M:%S')
**Source:** ${WATCHED_REPOS_FILE}
**Parallel Method:** ${PARALLEL_METHOD}

---

## Summary

EOF

# Extract GitHub URLs from WATCHED-REPOSITORIES.md
# Look for markdown links in the format [text](https://github.com/...)
REPOS=$(grep -oP '\[.*?\]\(https://github\.com/[^)]+\)' "${WATCHED_REPOS_FILE}" | \
        grep -oP 'https://github\.com/[^)]+' | sort -u || true)

if [ -z "${REPOS}" ]; then
    echo -e "${RED}❌ No GitHub repositories found in ${WATCHED_REPOS_FILE}${NC}"
    exit 1
fi

# Count repositories
REPO_COUNT=$(echo "${REPOS}" | wc -l)
echo "Found ${REPO_COUNT} repositories to check"
echo ""

# Execute repository checks in parallel
if [ "${PARALLEL_METHOD}" = "gnu-parallel" ]; then
    # Use GNU parallel for optimal performance
    echo "${REPOS}" | parallel -j "${MAX_PARALLEL_JOBS}" check_repository {} "${TEMP_DIR}"
else
    # Fallback to bash job control
    JOB_COUNT=0
    while IFS= read -r REPO_URL; do
        check_repository "${REPO_URL}" "${TEMP_DIR}" &
        JOB_COUNT=$((JOB_COUNT + 1))

        # Wait if we've hit max parallel jobs
        if [ $((JOB_COUNT % MAX_PARALLEL_JOBS)) -eq 0 ]; then
            wait
        fi
    done <<< "${REPOS}"

    # Wait for remaining jobs
    wait
fi

echo "All checks complete. Processing results..."
echo ""

# Initialize counters
UPDATED_COUNT=0
UNCHANGED_COUNT=0
ERROR_COUNT=0

# Process results from temp files (in deterministic order)
for TEMP_FILE in $(ls "${TEMP_DIR}"/*.txt 2>/dev/null | sort); do
    # Read result data
    TYPE=$(grep "^TYPE:" "${TEMP_FILE}" | cut -d: -f2)
    STATUS=$(grep "^STATUS:" "${TEMP_FILE}" | cut -d: -f2-)
    REPO=$(grep "^REPO:" "${TEMP_FILE}" | cut -d: -f2)
    URL=$(grep "^URL:" "${TEMP_FILE}" | cut -d: -f2-)

    # Update counters
    case "${TYPE}" in
        updated)
            UPDATED_COUNT=$((UPDATED_COUNT + 1))
            COLOR="${GREEN}"
            ;;
        unchanged)
            UNCHANGED_COUNT=$((UNCHANGED_COUNT + 1))
            COLOR="${NC}"
            ;;
        error)
            ERROR_COUNT=$((ERROR_COUNT + 1))
            COLOR="${RED}"
            ;;
    esac

    # Terminal output
    echo -e "${COLOR}✓ ${REPO}: ${STATUS}${NC}"

    # Build report entry
    cat >> "${REPORT_FILE}" <<EOF

### ${REPO}
- **Status:** ${STATUS}
EOF

    # Add optional fields if present
    if grep -q "^LAST_COMMIT:" "${TEMP_FILE}"; then
        LAST_COMMIT=$(grep "^LAST_COMMIT:" "${TEMP_FILE}" | cut -d: -f2)
        DAYS_AGO=$(grep "^DAYS_AGO:" "${TEMP_FILE}" | cut -d: -f2)
        STARS=$(grep "^STARS:" "${TEMP_FILE}" | cut -d: -f2)
        RELEASE=$(grep "^RELEASE:" "${TEMP_FILE}" | cut -d: -f2)

        cat >> "${REPORT_FILE}" <<EOF
- **Last Commit:** ${LAST_COMMIT} (${DAYS_AGO} days ago)
- **Stars:** ${STARS}
- **Latest Release:** ${RELEASE}
EOF
    fi

    # Add URL and optional note
    echo "- **URL:** ${URL}" >> "${REPORT_FILE}"
    if grep -q "^NOTE:" "${TEMP_FILE}"; then
        NOTE=$(grep "^NOTE:" "${TEMP_FILE}" | cut -d: -f2-)
        echo "- **Note:** ${NOTE}" >> "${REPORT_FILE}"
    fi

    echo "" >> "${REPORT_FILE}"
done

# Update summary in report
sed -i "/^## Summary$/a\\
\\
**Total Repositories:** ${REPO_COUNT}\\
**Recently Updated:** ${UPDATED_COUNT}\\
**No Recent Activity:** ${UNCHANGED_COUNT}\\
**Errors:** ${ERROR_COUNT}\\
" "${REPORT_FILE}"

# Print summary
echo "======================================="
echo -e "Summary:"
echo -e "  Total: ${REPO_COUNT}"
echo -e "  ${GREEN}Recently Updated: ${UPDATED_COUNT}${NC}"
echo -e "  No Recent Activity: ${UNCHANGED_COUNT}"
echo -e "  ${RED}Errors: ${ERROR_COUNT}${NC}"
echo ""
echo "Report saved to: ${REPORT_FILE}"

# If there are recently updated repos, suggest review
if [ ${UPDATED_COUNT} -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}📢 Action Required:${NC} ${UPDATED_COUNT} repositories have recent updates."
    echo "   Review WATCHED-REPOSITORIES.md and consider re-evaluation."
fi

exit 0
