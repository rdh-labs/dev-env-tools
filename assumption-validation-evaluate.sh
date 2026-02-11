#!/bin/bash
# Assumption Validation Phase 1 - Final Evaluation Report Generator
# Run on review date to generate comprehensive before/after analysis
# Created: 2026-02-10 | Expected Run: 2026-02-17

set -euo pipefail

# Configuration
METRICS_DIR=~/.claude/assumption-registry
REPORT_DIR=~/dev/infrastructure/metrics/assumption-validation
BASELINE_FILE="$REPORT_DIR/baseline-2026-02-10.json"
DAILY_DIR="$REPORT_DIR/daily-reports"
OUTPUT_FILE="$REPORT_DIR/evaluation-report-$(date +%Y-%m-%d).md"
NOTIFY_SCRIPT=~/bin/notify.sh

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Function: Get latest metrics
get_latest_metrics() {
    local latest_file=$(ls -t "$DAILY_DIR"/metrics-*.json 2>/dev/null | head -1)
    if [ -z "$latest_file" ]; then
        echo "ERROR: No daily metrics found" >&2
        exit 1
    fi
    cat "$latest_file"
}

# Function: Calculate override rate
calculate_override_rate() {
    local overrides=$1
    local bypasses=$2
    local total=$((overrides + bypasses))

    if [ "$total" -eq 0 ]; then
        echo "0.0"
        return
    fi

    echo "scale=1; $overrides * 100 / $total" | bc
}

# Function: Generate decision
determine_outcome() {
    local override_rate=$1
    local validation_rate=$2

    local override_met=$(echo "$override_rate <= 80" | bc)
    local validation_met=$(echo "$validation_rate >= 35" | bc)

    if [ "$override_met" -eq 1 ] && [ "$validation_met" -eq 1 ]; then
        echo "SUCCESS"
    elif [ "$override_met" -eq 1 ] || [ "$validation_met" -eq 1 ]; then
        echo "PARTIAL"
    else
        echo "FAILURE"
    fi
}

# Function: Get recommendation
get_recommendation() {
    local outcome=$1

    case "$outcome" in
        SUCCESS)
            echo "Proceed to Phase 2 (scope assumptions + override friction)"
            ;;
        PARTIAL)
            echo "Investigate underperforming component, consider Phase 1.5 polish, re-evaluate in 1 week"
            ;;
        FAILURE)
            echo "Root cause analysis required, consider rollback or alternative approach"
            ;;
    esac
}

# Main evaluation
main() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}ASSUMPTION VALIDATION PHASE 1 - FINAL EVALUATION${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    # Check for baseline
    if [ ! -f "$BASELINE_FILE" ]; then
        echo -e "${RED}ERROR: Baseline file not found: $BASELINE_FILE${NC}"
        exit 1
    fi

    # Get metrics
    echo -e "${YELLOW}📊 Loading metrics...${NC}"
    baseline=$(cat "$BASELINE_FILE")
    latest=$(get_latest_metrics)

    # Parse baseline
    base_overrides=$(echo "$baseline" | jq -r '.override_count')
    base_bypasses=$(echo "$baseline" | jq -r '.test_bypass_count')
    base_val_rate=$(echo "$baseline" | jq -r '.assumptions.validation_rate_pct')

    # Parse latest
    curr_overrides=$(echo "$latest" | jq -r '.override_count')
    curr_bypasses=$(echo "$latest" | jq -r '.test_bypass_count')
    curr_val_rate=$(echo "$latest" | jq -r '.assumptions.validation_rate_pct')
    curr_timestamp=$(echo "$latest" | jq -r '.timestamp')

    # Calculate override rates
    base_override_rate=$(calculate_override_rate "$base_overrides" "$base_bypasses")
    curr_override_rate=$(calculate_override_rate "$curr_overrides" "$curr_bypasses")

    # Calculate deltas
    override_rate_delta=$(echo "scale=1; $curr_override_rate - $base_override_rate" | bc)
    val_rate_delta=$(echo "scale=1; $curr_val_rate - $base_val_rate" | bc)

    # Determine outcome
    outcome=$(determine_outcome "$curr_override_rate" "$curr_val_rate")
    recommendation=$(get_recommendation "$outcome")

    # Generate report
    cat > "$OUTPUT_FILE" << EOF
# Assumption Validation Phase 1 - Evaluation Report

**Review Date:** $(date +%Y-%m-%d)
**Monitoring Period:** 2026-02-10 to $(date +%Y-%m-%d)
**Evaluation Status:** ${outcome}

---

## Executive Summary

Phase 1 implementation (test mode detection + validation reminders) deployed on 2026-02-10. This report evaluates impact after 1 week of monitoring.

**Outcome:** **${outcome}**

**Recommendation:** ${recommendation}

---

## Metrics Comparison

### Override Rate

| Metric | Baseline | Current | Change | Target | Met? |
|--------|----------|---------|--------|--------|------|
| Override Rate | ${base_override_rate}% | ${curr_override_rate}% | ${override_rate_delta}% | <80% | $([ $(echo "$curr_override_rate <= 80" | bc) -eq 1 ] && echo "✅ YES" || echo "❌ NO") |

### Validation Rate

| Metric | Baseline | Current | Change | Target | Met? |
|--------|----------|---------|--------|--------|------|
| Validation Rate | ${base_val_rate}% | ${curr_val_rate}% | ${val_rate_delta}% | >35% | $([ $(echo "$curr_val_rate >= 35" | bc) -eq 1 ] && echo "✅ YES" || echo "❌ NO") |

### Test Bypasses

| Metric | Baseline | Current | Status |
|--------|----------|---------|--------|
| Test Bypasses Logged | ${base_bypasses} | ${curr_bypasses} | $([ "$curr_bypasses" -gt "$base_bypasses" ] && echo "✅ Working" || echo "⚠️ Check") |

---

## Detailed Analysis

### What Worked

EOF

    # Conditional analysis based on metrics
    if [ "$curr_bypasses" -gt "$base_bypasses" ]; then
        echo "- ✅ **Test mode detection functioning**: ${curr_bypasses} test queries auto-bypassed" >> "$OUTPUT_FILE"
    fi

    if (( $(echo "$override_rate_delta < 0" | bc -l) )); then
        echo "- ✅ **Override rate improved**: Decreased by ${override_rate_delta#-}%" >> "$OUTPUT_FILE"
    fi

    if (( $(echo "$val_rate_delta > 0" | bc -l) )); then
        echo "- ✅ **Validation rate improved**: Increased by ${val_rate_delta}%" >> "$OUTPUT_FILE"
    fi

    cat >> "$OUTPUT_FILE" << EOF

### Concerns

EOF

    if (( $(echo "$override_rate_delta > 0" | bc -l) )); then
        echo "- ⚠️ **Override rate increased**: Up by ${override_rate_delta}%" >> "$OUTPUT_FILE"
    fi

    if (( $(echo "$val_rate_delta < 0" | bc -l) )); then
        echo "- ⚠️ **Validation rate decreased**: Down by ${val_rate_delta#-}%" >> "$OUTPUT_FILE"
    fi

    if [ "$curr_bypasses" -eq "$base_bypasses" ]; then
        echo "- ⚠️ **Test bypasses not incrementing**: Functionality may be broken" >> "$OUTPUT_FILE"
    fi

    cat >> "$OUTPUT_FILE" << EOF

---

## Decision Matrix Application

**Criteria Evaluated:**
- Override rate ≤80%: $([ $(echo "$curr_override_rate <= 80" | bc) -eq 1 ] && echo "✅ Met" || echo "❌ Not met")
- Validation rate ≥35%: $([ $(echo "$curr_val_rate >= 35" | bc) -eq 1 ] && echo "✅ Met" || echo "❌ Not met")

**Outcome Category:** ${outcome}

**Recommended Action:** ${recommendation}

---

## Next Steps

EOF

    case "$outcome" in
        SUCCESS)
            cat >> "$OUTPUT_FILE" << 'EOF'
### Phase 2 Planning

1. **Scope Assumptions** (addresses 40% of problem - gate contamination)
   - Allow assumptions to specify domain/scope
   - Only block queries matching assumption domain
   - Prevents one bad assumption from blocking all queries

2. **Override Friction** (addresses 10% of problem)
   - Require justification for overrides
   - Log override reasons
   - Make bypassing gates slightly more deliberate

3. **Consider Advanced Features**
   - IDEA-346: Proactive validation guidance
   - IDEA-347: Assumption auto-validation

### Timeline
- [ ] Draft Phase 2 plan (1-2 hours)
- [ ] Review with user
- [ ] Implement Phase 2 (est. 3-4 hours)
- [ ] Deploy and monitor (2 weeks)
EOF
            ;;
        PARTIAL)
            cat >> "$OUTPUT_FILE" << 'EOF'
### Investigation Required

1. **Identify Underperforming Component**
   - If override rate not met: Analyze why test mode not catching queries
   - If validation rate not met: Analyze reminder effectiveness

2. **Consider Phase 1.5 Polish**
   - M-1: Age display enhancement
   - M-2: Empty query guards
   - M-3: Reminder frequency control
   - M-4: Pattern match logging

3. **Extended Monitoring**
   - Continue monitoring for 1 additional week
   - Re-evaluate on $(date -d "+7 days" +%Y-%m-%d)

### Timeline
- [ ] Root cause analysis (1 hour)
- [ ] Implement targeted fixes (1-2 hours)
- [ ] Monitor for 1 week
- [ ] Re-evaluate
EOF
            ;;
        FAILURE)
            cat >> "$OUTPUT_FILE" << 'EOF'
### Root Cause Analysis Required

1. **Investigate Test Pattern Coverage**
   - Sample recent overrides: Are they test queries?
   - Check for false negatives in pattern matching
   - Consider expanding test patterns

2. **Check Reminder Effectiveness**
   - Verify reminder displaying in sessions
   - Check state file for reminder frequency
   - Consider alternative reminder mechanisms

3. **Review Implementation**
   - Verify shared module imports working
   - Check metrics logging functioning
   - Test end-to-end flow

### Timeline
- [ ] Comprehensive investigation (2-3 hours)
- [ ] Document findings
- [ ] Decide: Fix vs Alternative Approach vs Rollback
EOF
            ;;
    esac

    cat >> "$OUTPUT_FILE" << EOF

---

## Governance Tracking

### Issues to Update
- ISSUE-189: Assumption Validation System Degraded (override rate)
- ISSUE-190: Validation Rate Critically Low

### Decisions to Record
- DEC-XXX: Phase 1 evaluation outcome and next steps

### Lessons Learned
- (To be captured after review)

---

## Appendix: Raw Data

### Baseline (2026-02-10)
\`\`\`json
$(cat "$BASELINE_FILE")
\`\`\`

### Latest (${curr_timestamp})
\`\`\`json
$(echo "$latest")
\`\`\`

### Daily Reports
Location: \`$DAILY_DIR\`
Count: $(ls -1 "$DAILY_DIR"/metrics-*.json 2>/dev/null | wc -l) days

---

**Report Generated:** $(date -Iseconds)
**Script:** assumption-validation-evaluate.sh
**Status:** ${outcome}
EOF

    echo -e "${GREEN}✓ Evaluation report generated: $OUTPUT_FILE${NC}"

    # Display summary
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}EVALUATION SUMMARY${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "Outcome: ${YELLOW}${outcome}${NC}"
    echo -e "Override Rate: ${base_override_rate}% → ${curr_override_rate}% (${override_rate_delta}%)"
    echo -e "Validation Rate: ${base_val_rate}% → ${curr_val_rate}% (${val_rate_delta}%)"
    echo -e "Test Bypasses: ${base_bypasses} → ${curr_bypasses}"
    echo ""
    echo -e "Recommendation: ${recommendation}"
    echo ""
    echo -e "Full report: ${OUTPUT_FILE}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    # Send notification
    if [ -x "$NOTIFY_SCRIPT" ]; then
        "$NOTIFY_SCRIPT" "📊 Phase 1 Evaluation Complete: ${outcome}" \
            "Override: ${curr_override_rate}%, Validation: ${curr_val_rate}%. ${recommendation}" \
            --priority high --channel both
    fi
}

# Run main
main "$@"
