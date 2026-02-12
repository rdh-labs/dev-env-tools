#!/bin/bash
# Cross-Document Consistency Check for Decision Records
# Detects inconsistencies between DEC-### entries in DECISIONS-LOG.md
# and their corresponding detailed documents
#
# Usage: decision-consistency-check.sh [--verbose]
#
# Created: 2026-02-12
# Related: IDEA-364, DEC-151, DEC-116

set -e

DECISIONS_LOG="$HOME/dev/infrastructure/dev-env-docs/DECISIONS-LOG.md"
VERBOSE=false
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Color codes for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Parse arguments
JSON_OUTPUT=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose)
            VERBOSE=true
            shift
            ;;
        --json)
            JSON_OUTPUT=true
            shift
            ;;
        *)
            echo "Usage: $0 [--verbose] [--json]"
            exit 2
            ;;
    esac
done

log() {
    local level=$1
    shift
    local msg="$*"

    case "$level" in
        ERROR)
            echo -e "${RED}[ERROR]${NC} $msg" >&2
            ;;
        WARN)
            echo -e "${YELLOW}[WARN]${NC} $msg"
            ;;
        INFO)
            echo -e "${GREEN}[INFO]${NC} $msg"
            ;;
        DEBUG)
            if [[ "$VERBOSE" == "true" ]]; then
                echo "[DEBUG] $msg"
            fi
            ;;
    esac
}

# Extract technology terms from text
# Looks for: Capitalized words, quoted terms, common tech patterns
extract_tech_terms() {
    local file=$1
    local terms_file="$TEMP_DIR/terms_$(basename "$file").txt"

    # Extract potential technology terms:
    # - Capitalized words (excluding common English words)
    # - Quoted phrases
    # - Common technology patterns (e.g., CSS-only, Chart.js)
    # - File extensions and script names

    {
        # Capitalized proper nouns (2+ chars, not sentence starts)
        grep -oE '\b[A-Z][a-z]*[A-Z][a-zA-Z]*\b' "$file" 2>/dev/null || true

        # Technology compounds (CSS-only, Chart.js, etc.)
        grep -oE '\b[A-Z][A-Za-z]+-[a-z]+\b|\b[A-Z][a-z]+\.[a-z]+\b' "$file" 2>/dev/null || true

        # Quoted terms
        grep -oE '"[^"]+"' "$file" 2>/dev/null | sed 's/"//g' || true

        # Script/file names
        grep -oE '[a-z_-]+\.(py|sh|js|ts|md|yaml|json)' "$file" 2>/dev/null || true

        # Common tech names (case-insensitive search, normalize to title case)
        grep -ioE '\b(grafana|budibase|docker|python|javascript|typescript|html|css|chart\.?js|postgres|redis|nginx|react|vue|angular|kubernetes|prometheus|influxdb)\b' "$file" 2>/dev/null | \
            awk '{print toupper(substr($0,1,1)) tolower(substr($0,2))}' || true
    } | sort -u > "$terms_file"

    echo "$terms_file"
}

# Compare two term lists and find mismatches
compare_terms() {
    local source_terms=$1
    local detailed_terms=$2
    local dec_id=$3

    # Find terms in source but not in detailed doc (potential inconsistencies)
    local source_only="$TEMP_DIR/${dec_id}_source_only.txt"
    local detailed_only="$TEMP_DIR/${dec_id}_detailed_only.txt"

    comm -23 "$source_terms" "$detailed_terms" > "$source_only"
    comm -13 "$source_terms" "$detailed_terms" > "$detailed_only"

    # Check for significant mismatches (excluding common words)
    local significant_mismatches=false

    # Terms in DECISIONS-LOG but not detailed doc
    if [[ -s "$source_only" ]]; then
        log DEBUG "Terms in DECISIONS-LOG.md but not detailed doc:"
        while IFS= read -r term; do
            # Filter out likely false positives (common English words, single chars, etc.)
            if [[ ${#term} -ge 3 ]] && ! [[ "$term" =~ ^(The|And|But|For|From|With|When|Where|What|How|This|That|Each|Some|More|Most|Less|Best)$ ]]; then
                log DEBUG "  - $term"
                significant_mismatches=true
            fi
        done < "$source_only"
    fi

    # Terms in detailed doc but not DECISIONS-LOG (usually OK, detailed docs have more detail)
    if [[ -s "$detailed_only" ]]; then
        log DEBUG "Terms in detailed doc but not DECISIONS-LOG.md (usually expected):"
        while IFS= read -r term; do
            if [[ ${#term} -ge 3 ]]; then
                log DEBUG "  - $term"
            fi
        done < "$detailed_only"
    fi

    echo "$significant_mismatches"
}

# Extract status from DEC entry
extract_status_from_dec() {
    local dec_content=$1
    grep -oP '(?<=\| )[A-Z]+(?= \|)' "$dec_content" | head -1 || echo "UNKNOWN"
}

# Extract status from detailed doc
extract_status_from_detailed() {
    local detailed_doc=$1
    grep -iE '^\*\*Status:\*\*' "$detailed_doc" | grep -oE '[A-Z]+' | head -1 || echo "UNKNOWN"
}

# Check a single DEC entry for consistency
check_dec_consistency() {
    local dec_id=$1
    local dec_section="$TEMP_DIR/${dec_id}_section.txt"

    # Extract the DEC section from DECISIONS-LOG.md
    awk "/^### ${dec_id} /,/^### DEC-[0-9]+ / {print}" "$DECISIONS_LOG" | head -n -1 > "$dec_section"

    if [[ ! -s "$dec_section" ]]; then
        log WARN "Could not extract section for $dec_id from DECISIONS-LOG.md"
        return 1
    fi

    # Look for reference to detailed document
    # Matches patterns like: "**Full experiment results:** `path/to/doc.md`"
    # MUST have backticks to avoid false positives
    local detailed_path

    # Extract path - require backticks to ensure it's a file reference, not just text containing ".md"
    detailed_path=$(grep -E '`[^`]*\.md`' "$dec_section" | sed -n 's/.*`\([^`]*\.md\)`.*/\1/p' | head -1 || true)

    if [[ -z "$detailed_path" ]]; then
        log DEBUG "$dec_id: No detailed document reference found (this is OK)"
        return 0
    fi

    # Expand ~ to home directory
    detailed_path="${detailed_path/#\~/$HOME}"

    if [[ ! -f "$detailed_path" ]]; then
        log ERROR "$dec_id: Referenced detailed doc not found: $detailed_path"
        echo "INCONSISTENCY|$dec_id|MISSING_DETAILED_DOC|$detailed_path"
        return 1
    fi

    log DEBUG "$dec_id: Checking consistency with $detailed_path"

    # Extract and compare technology terms
    local source_terms
    local detailed_terms
    source_terms=$(extract_tech_terms "$dec_section")
    detailed_terms=$(extract_tech_terms "$detailed_path")

    local has_mismatches
    has_mismatches=$(compare_terms "$source_terms" "$detailed_terms" "$dec_id")

    # Check status alignment
    local source_status
    local detailed_status
    source_status=$(extract_status_from_dec "$dec_section")
    detailed_status=$(extract_status_from_detailed "$detailed_path")

    if [[ "$source_status" != "UNKNOWN" ]] && [[ "$detailed_status" != "UNKNOWN" ]] && [[ "$source_status" != "$detailed_status" ]]; then
        log WARN "$dec_id: Status mismatch - DECISIONS-LOG: $source_status, Detailed doc: $detailed_status"
        echo "INCONSISTENCY|$dec_id|STATUS_MISMATCH|$source_status vs $detailed_status"
    fi

    if [[ "$has_mismatches" == "true" ]]; then
        log WARN "$dec_id: Technology term mismatches detected (see debug output with --verbose)"
        echo "INCONSISTENCY|$dec_id|TECH_TERM_MISMATCH|See verbose output for details"
    fi

    if [[ "$has_mismatches" == "false" ]] && [[ "$source_status" == "$detailed_status" || "$source_status" == "UNKNOWN" || "$detailed_status" == "UNKNOWN" ]]; then
        log DEBUG "$dec_id: ✓ Consistent with detailed document"
    fi
}

# Main execution
main() {
    log INFO "Starting cross-document consistency check..."
    log INFO "Source: $DECISIONS_LOG"
    echo ""

    if [[ ! -f "$DECISIONS_LOG" ]]; then
        log ERROR "DECISIONS-LOG.md not found at $DECISIONS_LOG"
        exit 1
    fi

    # Extract all DEC IDs from DECISIONS-LOG.md
    local dec_ids
    dec_ids=$(grep -oP '^### DEC-\K[0-9]+' "$DECISIONS_LOG" || true)

    if [[ -z "$dec_ids" ]]; then
        log ERROR "No DEC entries found in DECISIONS-LOG.md"
        exit 1
    fi

    local total_count=0
    local checked_count=0
    local inconsistency_count=0
    local inconsistencies_file="$TEMP_DIR/inconsistencies.txt"

    while IFS= read -r dec_num; do
        total_count=$((total_count + 1))
        local dec_id="DEC-${dec_num}"

        if check_dec_consistency "$dec_id" > "$TEMP_DIR/${dec_id}_result.txt"; then
            checked_count=$((checked_count + 1))
        fi

        # Collect inconsistencies
        if [[ -f "$TEMP_DIR/${dec_id}_result.txt" ]]; then
            grep '^INCONSISTENCY' "$TEMP_DIR/${dec_id}_result.txt" >> "$inconsistencies_file" 2>/dev/null || true
        fi
    done <<< "$dec_ids"

    # Summary
    if [[ -f "$inconsistencies_file" ]]; then
        inconsistency_count=$(wc -l < "$inconsistencies_file")
    fi

    # JSON output mode
    if [[ "$JSON_OUTPUT" == "true" ]]; then
        echo "{"
        echo "  \"timestamp\": \"$(date -Iseconds)\","
        echo "  \"total_entries\": $total_count,"
        echo "  \"entries_with_detailed_docs\": $checked_count,"
        echo "  \"inconsistencies_found\": $inconsistency_count,"
        if [[ $inconsistency_count -gt 0 ]]; then
            echo "  \"inconsistencies\": ["
            local first=true
            while IFS='|' read -r _ dec_id issue_type details; do
                if [[ "$first" == "false" ]]; then
                    echo ","
                fi
                first=false
                echo -n "    {\"dec_id\": \"$dec_id\", \"type\": \"$issue_type\", \"details\": \"$details\"}"
            done < "$inconsistencies_file"
            echo ""
            echo "  ],"
        fi
        echo "  \"status\": \"$([ $inconsistency_count -eq 0 ] && echo 'pass' || echo 'fail')\""
        echo "}"
        exit $([ $inconsistency_count -eq 0 ] && echo 0 || echo 1)
    fi

    # Human-readable output
    echo ""
    log INFO "========================================"
    log INFO "Consistency Check Summary"
    log INFO "========================================"
    log INFO "Total DEC entries: $total_count"
    log INFO "Entries with detailed docs: $checked_count"

    if [[ $inconsistency_count -eq 0 ]]; then
        log INFO "Inconsistencies found: 0 ✓"
        log INFO ""
        log INFO "All decision records are consistent!"
    else
        log WARN "Inconsistencies found: $inconsistency_count"
        log WARN ""
        log WARN "Detected inconsistencies:"
        echo ""

        while IFS='|' read -r _ dec_id issue_type details; do
            case "$issue_type" in
                MISSING_DETAILED_DOC)
                    echo "  • $dec_id: Referenced detailed document not found"
                    echo "    Path: $details"
                    ;;
                STATUS_MISMATCH)
                    echo "  • $dec_id: Status mismatch between documents"
                    echo "    Details: $details"
                    ;;
                TECH_TERM_MISMATCH)
                    echo "  • $dec_id: Technology term mismatches detected"
                    echo "    Run with --verbose for details"
                    ;;
            esac
            echo ""
        done < "$inconsistencies_file"

        exit 1
    fi
}

# Run main function
main "$@"
