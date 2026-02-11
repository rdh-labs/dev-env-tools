#!/bin/bash
# Assumption Validation Phase 1 Metrics Monitor
# Automated daily collection and analysis of Phase 1 impact
# Created: 2026-02-10 | Review Date: 2026-02-17

set -euo pipefail

# Configuration
METRICS_DIR=~/.claude/assumption-registry
REPORT_DIR=~/dev/infrastructure/metrics/assumption-validation
STATE_FILE="$REPORT_DIR/monitoring-state.json"
BASELINE_FILE="$REPORT_DIR/baseline-2026-02-10.json"

# Notification settings
NOTIFY_SCRIPT=~/bin/notify.sh
REVIEW_DATE="2026-02-17"

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Create report directory
mkdir -p "$REPORT_DIR"

# Initialize state file if not exists
if [ ! -f "$STATE_FILE" ]; then
    cat > "$STATE_FILE" << 'EOF'
{
  "monitoring_start": "2026-02-10T06:20:00-08:00",
  "review_date": "2026-02-17",
  "last_check": null,
  "checks_run": 0,
  "anomalies_detected": 0,
  "notifications_sent": 0
}
EOF
fi

# Function: Collect current metrics
collect_metrics() {
    local timestamp=$(date -Iseconds)

    # Handle missing files gracefully
    local override_count=0
    if [ -f "$METRICS_DIR/gate-overrides.jsonl" ]; then
        override_count=$(wc -l < "$METRICS_DIR/gate-overrides.jsonl" 2>/dev/null || echo "0")
    fi

    local test_bypass_count=0
    if [ -f "$METRICS_DIR/test-bypasses.jsonl" ]; then
        test_bypass_count=$(wc -l < "$METRICS_DIR/test-bypasses.jsonl" 2>/dev/null || echo "0")
    fi

    local registry_file="$METRICS_DIR/assumption-registry.json"

    # Count assumptions by validation status
    local total_assumptions=0
    local validated_count=0
    local invalid_count=0
    local unvalidated_count=0

    if [ -f "$registry_file" ]; then
        total_assumptions=$(jq '[.assumptions[]] | length' "$registry_file" 2>/dev/null || echo "0")
        validated_count=$(jq '[.assumptions[] | select(.validated == true and .is_valid == true)] | length' "$registry_file" 2>/dev/null || echo "0")
        invalid_count=$(jq '[.assumptions[] | select(.validated == true and .is_valid == false)] | length' "$registry_file" 2>/dev/null || echo "0")
        unvalidated_count=$(jq '[.assumptions[] | select(.validated == false)] | length' "$registry_file" 2>/dev/null || echo "0")
    fi

    # Calculate validation rate
    local validation_rate=0
    if [ "$total_assumptions" -gt 0 ]; then
        validation_rate=$(echo "scale=1; ($validated_count + $invalid_count) * 100 / $total_assumptions" | bc)
    fi

    cat << EOF
{
  "timestamp": "$timestamp",
  "override_count": $override_count,
  "test_bypass_count": $test_bypass_count,
  "assumptions": {
    "total": $total_assumptions,
    "validated": $validated_count,
    "invalid": $invalid_count,
    "unvalidated": $unvalidated_count,
    "validation_rate_pct": $validation_rate
  }
}
EOF
}

# Function: Save baseline (first run)
save_baseline() {
    if [ ! -f "$BASELINE_FILE" ]; then
        echo -e "${YELLOW}📊 Saving baseline metrics...${NC}"
        collect_metrics > "$BASELINE_FILE"
        echo -e "${GREEN}✓ Baseline saved: $BASELINE_FILE${NC}"

        # Send notification
        if [ -x "$NOTIFY_SCRIPT" ]; then
            "$NOTIFY_SCRIPT" "📊 Phase 1 Monitoring Started" \
                "Assumption Validation metrics collection started. Review scheduled for $REVIEW_DATE." \
                --priority low --channel auto
        fi
    fi
}

# Function: Calculate override rate
calculate_override_rate() {
    local current_overrides=$1
    local current_bypasses=$2
    local total_queries=$((current_overrides + current_bypasses))

    if [ "$total_queries" -eq 0 ]; then
        echo "0.0"
        return
    fi

    echo "scale=1; $current_overrides * 100 / $total_queries" | bc
}

# Function: Analyze trends and detect anomalies
analyze_metrics() {
    local current_metrics="$1"

    if [ ! -f "$BASELINE_FILE" ]; then
        echo -e "${YELLOW}⚠️  No baseline found, cannot analyze trends${NC}"
        return 0
    fi

    # Parse current metrics
    local curr_overrides=$(echo "$current_metrics" | jq -r '.override_count')
    local curr_bypasses=$(echo "$current_metrics" | jq -r '.test_bypass_count')
    local curr_val_rate=$(echo "$current_metrics" | jq -r '.assumptions.validation_rate_pct')

    # Parse baseline metrics
    local base_overrides=$(jq -r '.override_count' "$BASELINE_FILE")
    local base_bypasses=$(jq -r '.test_bypass_count' "$BASELINE_FILE")
    local base_val_rate=$(jq -r '.assumptions.validation_rate_pct' "$BASELINE_FILE")

    # Calculate override rate
    local curr_override_rate=$(calculate_override_rate "$curr_overrides" "$curr_bypasses")
    local base_override_rate=$(calculate_override_rate "$base_overrides" "$base_bypasses")

    # Calculate changes
    local override_delta=$(echo "scale=1; $curr_overrides - $base_overrides" | bc)
    local bypass_delta=$(echo "scale=1; $curr_bypasses - $base_bypasses" | bc)
    local val_rate_delta=$(echo "scale=1; $curr_val_rate - $base_val_rate" | bc)

    echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}METRICS ANALYSIS${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    echo -e "\n📊 Override Rate:"
    echo "  Baseline: ${base_override_rate}%"
    echo "  Current:  ${curr_override_rate}%"
    echo "  Target:   <80% (Phase 1), <25% (final)"

    echo -e "\n📈 Validation Rate:"
    echo "  Baseline: ${base_val_rate}%"
    echo "  Current:  ${curr_val_rate}%"
    echo "  Change:   ${val_rate_delta}%"
    echo "  Target:   >35% (Phase 1), >75% (final)"

    echo -e "\n🧪 Test Bypasses:"
    echo "  Count: $curr_bypasses (Δ${bypass_delta})"
    echo "  Status: Working as designed (test pollution eliminated)"

    # Anomaly detection
    local anomaly_detected=0

    # Alert if override rate INCREASED
    if (( $(echo "$curr_override_rate > $base_override_rate + 5" | bc -l) )); then
        echo -e "\n${RED}⚠️  ANOMALY: Override rate INCREASED by >5%${NC}"
        anomaly_detected=1
    fi

    # Alert if test bypasses stopped working (no new bypasses in 3+ days)
    local days_running=$(( ($(date +%s) - $(date -d "2026-02-10" +%s)) / 86400 ))
    if [ "$days_running" -ge 3 ] && [ "$bypass_delta" -eq 0 ]; then
        echo -e "\n${RED}⚠️  ANOMALY: No test bypasses logged in 3+ days${NC}"
        anomaly_detected=1
    fi

    # Alert if validation rate DECREASED
    if (( $(echo "$val_rate_delta < -5" | bc -l) )); then
        echo -e "\n${RED}⚠️  ANOMALY: Validation rate DECREASED by >5%${NC}"
        anomaly_detected=1
    fi

    if [ "$anomaly_detected" -eq 1 ]; then
        # Send notification
        if [ -x "$NOTIFY_SCRIPT" ]; then
            "$NOTIFY_SCRIPT" "⚠️ Phase 1 Monitoring: Anomaly Detected" \
                "Assumption Validation metrics showing unexpected trends. Check report: $REPORT_DIR" \
                --priority high --channel auto
        fi

        # Update state
        jq ".anomalies_detected += 1 | .notifications_sent += 1" "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"
    fi

    echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    return "$anomaly_detected"
}

# Function: Check if review date reached
check_review_date() {
    local today=$(date +%Y-%m-%d)

    if [ "$today" = "$REVIEW_DATE" ]; then
        echo -e "${YELLOW}📅 Review date reached!${NC}"

        # Send notification
        if [ -x "$NOTIFY_SCRIPT" ]; then
            "$NOTIFY_SCRIPT" "📅 Phase 1 Review Due Today" \
                "Time to evaluate Assumption Validation Phase 1 impact. Report: $REPORT_DIR/daily-reports/" \
                --priority high --channel auto
        fi

        return 0
    elif [[ "$today" > "$REVIEW_DATE" ]]; then
        echo -e "${RED}⚠️  Review date OVERDUE (was $REVIEW_DATE)${NC}"
        return 1
    else
        local days_until=$((( $(date -d "$REVIEW_DATE" +%s) - $(date +%s) ) / 86400))
        echo -e "${GREEN}📅 Review in $days_until days ($REVIEW_DATE)${NC}"
        return 2
    fi
}

# Main execution
main() {
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}ASSUMPTION VALIDATION MONITORING${NC}"
    echo -e "${GREEN}Phase 1 Impact Assessment${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "Run: $(date)"
    echo -e "Review Date: $REVIEW_DATE"

    # Save baseline on first run
    save_baseline

    # Collect current metrics
    echo -e "\n${YELLOW}📊 Collecting metrics...${NC}"
    current_metrics=$(collect_metrics)

    # Save daily snapshot
    daily_report_dir="$REPORT_DIR/daily-reports"
    mkdir -p "$daily_report_dir"
    daily_file="$daily_report_dir/metrics-$(date +%Y-%m-%d).json"
    echo "$current_metrics" > "$daily_file"
    echo -e "${GREEN}✓ Saved: $daily_file${NC}"

    # Write to InfluxDB (if available)
    influxdb_writer="$(dirname "$0")/influxdb-writer.py"
    if [ -x "$influxdb_writer" ]; then
        echo "$current_metrics" | "$influxdb_writer" 2>&1 | grep -E "^(✓|✗)" || true
    fi

    # Analyze metrics
    analyze_metrics "$current_metrics"

    # Check review date (capture return but don't propagate as error)
    check_review_date || true

    # Update state
    jq ".last_check = \"$(date -Iseconds)\" | .checks_run += 1" "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"

    echo -e "\n${GREEN}✓ Monitoring check complete${NC}"
}

# Run main
main "$@"
