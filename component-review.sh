#!/usr/bin/env bash
# Component Review Automation (IDEA-393, GOV-013)
# Assists with systematic component fitness evaluation

set -euo pipefail

# Configuration
REVIEW_DIR="$HOME/dev/infrastructure/dev-env-docs/component-reviews"
TEMPLATE_DIR="$REVIEW_DIR/templates"
COMPLETED_DIR="$REVIEW_DIR/completed"
STATUS_FILE="$REVIEW_DIR/status.yaml"
HISTORY_FILE="$REVIEW_DIR/review-history.jsonl"
SOURCE_REGISTRY="$HOME/dev/infrastructure/dev-env-docs/knowledge/best-practice-sources.yaml"
CAPABILITIES_INVENTORY="$HOME/dev/infrastructure/dev-env-docs/CAPABILITIES-INVENTORY.md"

# Component categories
declare -A CATEGORY_NAMES=(
    ["ai-tools"]="AI Tools & APIs"
    ["mcp"]="MCP Infrastructure"
    ["hooks"]="Governance Hooks"
    ["dev-tools"]="Development Tools"
    ["automation"]="Automation Scripts"
    ["docs"]="Documentation"
    ["templates"]="Project Templates"
)

# Usage
usage() {
    cat <<EOF
Usage: component-review.sh [OPTIONS]

Systematic component fitness evaluation against best practices.

OPTIONS:
    --category CATEGORY     Review category (ai-tools|mcp|hooks|dev-tools|automation|docs|templates)
    --component NAME        Review specific component
    --list                  List all components by category
    --status                Show component review status dashboard
    --trigger EVENT         Event that triggered review (for logging)
    --urgent                Mark as urgent (out-of-band review)
    --help                  Show this help

EXAMPLES:
    # Review all MCP components (monthly scheduled)
    component-review.sh --category mcp

    # Review specific component
    component-review.sh --component mcp-gemini-integration

    # Urgent review triggered by source monitoring
    component-review.sh --category ai-tools --trigger "source-update:Anthropic" --urgent

    # Show status dashboard
    component-review.sh --status

EOF
    exit 1
}

# Parse arguments
CATEGORY=""
COMPONENT=""
TRIGGER="scheduled"
URGENT=false
LIST=false
STATUS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --category)
            CATEGORY="$2"
            shift 2
            ;;
        --component)
            COMPONENT="$2"
            shift 2
            ;;
        --trigger)
            TRIGGER="$2"
            shift 2
            ;;
        --urgent)
            URGENT=true
            shift
            ;;
        --list)
            LIST=true
            shift
            ;;
        --status)
            STATUS=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Show status dashboard
if [[ "$STATUS" == true ]]; then
    if [[ -f "$STATUS_FILE" ]]; then
        echo "=== Component Review Status Dashboard ==="
        echo ""
        cat "$STATUS_FILE"
    else
        echo "No status file found at $STATUS_FILE"
        echo "Run a component review to create it."
    fi
    exit 0
fi

# List components by category
if [[ "$LIST" == true ]]; then
    echo "=== Component Inventory by Category ==="
    echo ""

    for cat in "${!CATEGORY_NAMES[@]}"; do
        echo "## ${CATEGORY_NAMES[$cat]} ($cat)"

        # Extract components from CAPABILITIES-INVENTORY.md for each category
        # This is a placeholder - actual implementation would parse CAPABILITIES-INVENTORY.md
        echo "  [Run with --category $cat to see details]"
        echo ""
    done
    exit 0
fi

# Validate category
if [[ -n "$CATEGORY" ]] && [[ ! -v "CATEGORY_NAMES[$CATEGORY]" ]]; then
    echo "Error: Invalid category '$CATEGORY'"
    echo "Valid categories: ${!CATEGORY_NAMES[*]}"
    exit 1
fi

# Main review logic
if [[ -z "$CATEGORY" ]] && [[ -z "$COMPONENT" ]]; then
    echo "Error: Must specify --category or --component"
    usage
fi

# Determine what to review
if [[ -n "$COMPONENT" ]]; then
    REVIEW_TARGETS=("$COMPONENT")
else
    # Get all components in category
    # Placeholder - actual implementation would discover components
    echo "Discovering components in category: ${CATEGORY_NAMES[$CATEGORY]}"
    REVIEW_TARGETS=("placeholder-component-1" "placeholder-component-2")
fi

echo "=== Component Review System (GOV-013) ==="
echo "Category: ${CATEGORY_NAMES[$CATEGORY]:-N/A}"
echo "Trigger: $TRIGGER"
echo "Urgent: $URGENT"
echo "Components to review: ${#REVIEW_TARGETS[@]}"
echo ""

# Review each component
for component in "${REVIEW_TARGETS[@]}"; do
    echo "--- Reviewing: $component ---"

    # Generate review file from template
    review_date=$(date +%Y-%m-%d)
    review_file="$COMPLETED_DIR/${review_date}-${component}.md"

    # Copy template and populate with component info
    if [[ -f "$TEMPLATE_DIR/review-template.md" ]]; then
        cp "$TEMPLATE_DIR/review-template.md" "$review_file"

        # Placeholder for template population
        # Actual implementation would fill in:
        # - Current state from system inspection
        # - Usage metrics from logs
        # - Configuration from actual config files

        echo "  ✓ Created review file: $review_file"
    else
        echo "  ⚠  Template not found, creating basic structure"
        cat > "$review_file" <<EOF
# Component Review: $component

**Review Date:** $review_date
**Trigger:** $trigger
**Category:** $CATEGORY

## Manual Review Required

This component review was initiated but requires manual completion.

Complete the review using:
1. ~/dev/infrastructure/dev-env-docs/governance-patterns/13-component-review-system.md
2. ~/dev/infrastructure/dev-env-docs/knowledge/best-practice-sources.yaml

## Next Steps

1. Document current state
2. Research best practices
3. Analyze alternatives
4. Assess ecosystem fit
5. Make decision (KEEP | UPDATE | REPLACE | DEPRECATE)
6. Create action items
7. Update status.yaml and review-history.jsonl
EOF
    fi

    # Log review initiation to history
    review_record=$(cat <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "component_name": "$component",
  "component_category": "$CATEGORY",
  "reviewer": "automated-trigger",
  "trigger": "$TRIGGER",
  "urgent": $URGENT,
  "review_file": "$review_file",
  "status": "initiated"
}
EOF
)

    echo "$review_record" >> "$HISTORY_FILE"
    echo "  ✓ Logged to review history"

    # Send notification if urgent
    if [[ "$URGENT" == true ]]; then
        if [[ -x "$HOME/bin/notify.sh" ]]; then
            "$HOME/bin/notify.sh" \
                "🔍 Urgent Component Review" \
                "Review needed for $component (trigger: $TRIGGER). See: $review_file" \
                --priority high \
                --channel auto
            echo "  ✓ Sent urgent notification"
        fi
    fi

    echo "  📝 Review file ready for completion"
    echo ""
done

# Summary
echo "=== Review Summary ==="
echo "Total components reviewed: ${#REVIEW_TARGETS[@]}"
echo "Review files created in: $COMPLETED_DIR"
echo "History logged to: $HISTORY_FILE"
echo ""
echo "Next steps:"
echo "1. Complete manual review for each component"
echo "2. Update $STATUS_FILE with results"
echo "3. Create IDEA/ISSUE/TASK items as needed"
echo "4. Schedule next review"
