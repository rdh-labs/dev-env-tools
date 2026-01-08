#!/usr/bin/env bash

# Program Review Packet Generator
# Purpose: Compile key inputs for a program review into a single markdown file.

set -euo pipefail

DATE=$(date +%Y-%m-%d)
OUTPUT_FILE=${1:-"/tmp/PROGRAM-REVIEW-PACKET-${DATE}.md"}
BASE_DIR=${2:-"$(pwd)"}

append_header() {
    cat <<HDR >> "$OUTPUT_FILE"
# Program Review Packet

**Generated:** ${DATE}
**Base Directory:** ${BASE_DIR}

HDR
}

append_section_title() {
    local title="$1"
    cat <<HDR >> "$OUTPUT_FILE"

## ${title}

HDR
}

append_file_snippet() {
    local file="$1"

    if [ ! -f "$file" ]; then
        echo "- MISSING: ${file}" >> "$OUTPUT_FILE"
        return
    fi

    local lines
    lines=$(wc -l < "$file" | tr -d ' ')

    echo "- INCLUDED: ${file} (${lines} lines)" >> "$OUTPUT_FILE"
    echo "" >> "$OUTPUT_FILE"
    echo '```' >> "$OUTPUT_FILE"

    if [ "$lines" -gt 200 ]; then
        sed -n '1,120p' "$file" >> "$OUTPUT_FILE"
        echo "" >> "$OUTPUT_FILE"
        echo "[...snip... last 60 lines]" >> "$OUTPUT_FILE"
        tail -n 60 "$file" >> "$OUTPUT_FILE"
    else
        cat "$file" >> "$OUTPUT_FILE"
    fi

    echo '```' >> "$OUTPUT_FILE"
}

append_header

append_section_title "Program Artifacts"
append_file_snippet "${BASE_DIR}/PROJECT-SUMMARY.md"
append_file_snippet "${BASE_DIR}/WHEN-TO-CREATE.md"

append_section_title "Session Handoffs"
shopt -s nullglob
handoffs=("${BASE_DIR}"/SESSION-HANDOFF-*.md)
if [ ${#handoffs[@]} -eq 0 ]; then
    echo "- NONE FOUND" >> "$OUTPUT_FILE"
else
    for f in "${handoffs[@]}"; do
        append_file_snippet "$f"
    done
fi
shopt -u nullglob

append_section_title "Ecosystem Signals"
append_file_snippet "$HOME/dev/infrastructure/dev-env-docs/IDEAS-BACKLOG.md"
append_file_snippet "$HOME/dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md"
append_file_snippet "$HOME/lessons.md"

append_section_title "Metrics Pointers"
cat <<'MET' >> "$OUTPUT_FILE"
- Run daily metrics: `~/dev/infrastructure/tools/metrics-collector.sh`
- Generate weekly summary: `~/dev/infrastructure/tools/weekly-report.sh`
MET

append_section_title "Notes"
cat <<'NOTES' >> "$OUTPUT_FILE"
- Use this packet with `conduct-program-review.md`.
- External best-practice research may require network access.
NOTES

echo "Review packet written to: $OUTPUT_FILE"
