#!/usr/bin/env bash
# Quarterly source curation audit (IDEA-401, GOV-014)
# Automates generation of audit checklist for all sources

set -euo pipefail

# Configuration
REGISTRY="$HOME/dev/infrastructure/dev-env-docs/knowledge/best-practice-sources.yaml"
DASHBOARD="$HOME/dev/infrastructure/dev-env-docs/knowledge/source-curation-dashboard.yaml"
AUDIT_DIR="$HOME/dev/infrastructure/dev-env-docs/knowledge/source-audits"
AUDIT_LOG="$AUDIT_DIR/audit-$(date +%Y-%m-%d).md"

# Create audit directory if it doesn't exist
mkdir -p "$AUDIT_DIR"

# Generate audit report
cat > "$AUDIT_LOG" <<'HEADER'
# Quarterly Source Curation Audit

**Audit Date:** DATE_PLACEHOLDER
**Auditor:** [Name/Model]
**Sources Reviewed:** TOTAL_PLACEHOLDER
**Duration:** [Estimated 2-3 hours]

---

## Instructions

For each source below:
1. Complete all 5 review steps (Activity, Quality, Authority, Alternatives, Usage)
2. Make a decision (KEEP, PROMOTE, DEMOTE, DEPRECATE, UPDATE)
3. Document rationale with evidence
4. Create action items if needed

After completing audit:
1. Update best-practice-sources.yaml with curation actions
2. Update source-curation-dashboard.yaml with metrics
3. Create IDEA/ISSUE items for significant changes
4. Schedule next quarterly audit (3 months from today)

---

HEADER

# Replace placeholders
sed -i "s/DATE_PLACEHOLDER/$(date -Iseconds)/" "$AUDIT_LOG"

# Count sources
total_sources=$(grep -c "^  - name:" "$REGISTRY" 2>/dev/null || echo "0")
sed -i "s/TOTAL_PLACEHOLDER/$total_sources/" "$AUDIT_LOG"

# Extract all sources and generate review templates
echo "" >> "$AUDIT_LOG"
echo "## Source Reviews" >> "$AUDIT_LOG"
echo "" >> "$AUDIT_LOG"

# Parse sources from YAML
current_source=""
current_url=""
current_category=""

while IFS= read -r line; do
    # Detect category
    if [[ "$line" =~ ^[[:space:]]+([a-z_]+):$ ]]; then
        current_category="${BASH_REMATCH[1]}"
    fi

    # Detect source name
    if [[ "$line" =~ ^[[:space:]]+-[[:space:]]+name:[[:space:]]+\"(.+)\" ]]; then
        current_source="${BASH_REMATCH[1]}"
    fi

    # Detect source URL
    if [[ "$line" =~ ^[[:space:]]+url:[[:space:]]+\"(.+)\" ]]; then
        current_url="${BASH_REMATCH[1]}"

        # Generate review template for this source
        cat >> "$AUDIT_LOG" <<EOF
### $current_source

**Category:** $current_category
**URL:** $current_url

#### 1. Activity Check

**Last monitored:** [Check source-monitoring-log.md]
**Last update detected:** [Date or "Unknown"]

- [ ] ✅ Active (updates in last 3 months)
- [ ] ⚠️  Inactive (no updates 3-6 months)
- [ ] ❌ Abandoned (no updates 6+ months)

**Evidence:**


#### 2. Quality Check

**Web search validation:**
\`\`\`
"$current_source accuracy review 2026"
"$current_source vs alternatives comparison 2026"
\`\`\`

- [ ] ✅ High quality (accurate, current, well-maintained)
- [ ] ⚠️  Declining (some outdated content, minor errors)
- [ ] ❌ Low quality (significant errors, outdated, misleading)

**Evidence:**


#### 3. Authority Check

**Community recognition:**
\`\`\`
"$current_source cited by 2026"
"$current_source recommended 2026"
\`\`\`

- [ ] ✅ Primary authority (widely cited, industry-recognized)
- [ ] ⚠️  Secondary authority (specialized, niche recognition)
- [ ] ❌ No authority (rarely cited, superseded)

**Evidence:**


#### 4. Alternatives Scan

**Better alternatives exist?**

- [ ] ✅ Still best (no better alternatives)
- [ ] ⚠️  Competitive (alternatives equal but not better)
- [ ] ❌ Superseded (clear better alternative exists)

**Alternatives found:**


#### 5. Usage Analysis

**Ecosystem usage:**
\`\`\`bash
grep -r "$current_source" ~/dev/infrastructure/dev-env-docs/*.md | wc -l
\`\`\`

- [ ] ✅ High usage (3+ citations in decisions/reviews)
- [ ] ⚠️  Low usage (1-2 citations)
- [ ] ❌ Unused (0 citations)

**Citation count:** ___

---

#### Decision

- [ ] **KEEP** - Validated, no changes needed
- [ ] **PROMOTE** - Upgrade to primary authority
- [ ] **DEMOTE** - Downgrade to secondary authority
- [ ] **DEPRECATE** - Mark for removal
- [ ] **UPDATE** - Modify metadata (URL, focus, cadence)

**Rationale:**


**Action Items:**
- [ ] TASK-XXX: [Description]
- [ ] IDEA-XXX: [Description]
- [ ] Update best-practice-sources.yaml
- [ ] Update source-curation-dashboard.yaml

---

EOF
    fi
done < "$REGISTRY"

# Append summary section
cat >> "$AUDIT_LOG" <<'FOOTER'

## Audit Summary

**Completed:** [Date]
**Duration:** [Actual time spent]

### Curation Actions Taken

- **Promoted:** ___ sources
  - [List sources]

- **Demoted:** ___ sources
  - [List sources]

- **Deprecated:** ___ sources
  - [List sources]

- **Added:** ___ sources
  - [List sources]

- **Updated:** ___ sources
  - [List sources]

- **Kept (validated):** ___ sources
  - [List sources]

### Source Health After Audit

- **High quality:** ___ sources
- **Declining quality:** ___ sources
- **Low quality:** ___ sources

- **Active (< 3 months):** ___ sources
- **Inactive (3-6 months):** ___ sources
- **Abandoned (6+ months):** ___ sources

### Coverage Analysis

**Gaps identified:**
- [List topic areas not covered by authoritative sources]

**Overlaps identified:**
- [List sources with significant overlap in coverage]

**Recommendations:**
- [Add sources for gaps]
- [Consolidate overlapping sources]

### Next Steps

1. [ ] Apply all curation actions to best-practice-sources.yaml
2. [ ] Update source-curation-dashboard.yaml metrics
3. [ ] Create IDEA/ISSUE items for significant changes
4. [ ] Schedule next quarterly audit: [Date + 3 months]
5. [ ] Notify stakeholders of changes

---

**Audit file:** audit-YYYY-MM-DD.md
**Next audit due:** [Date + 3 months]
FOOTER

echo "=== Quarterly Source Audit Generated ==="
echo ""
echo "Audit file: $AUDIT_LOG"
echo "Sources to review: $total_sources"
echo ""
echo "Next steps:"
echo "1. Complete the audit (review each source)"
echo "2. Update $REGISTRY with curation actions"
echo "3. Update $DASHBOARD with new metrics"
echo "4. Create action items (IDEA/ISSUE/TASK)"
echo ""

# Send notification
if [[ -x "$HOME/bin/notify.sh" ]]; then
    "$HOME/bin/notify.sh" \
        "📚 Quarterly Source Audit Due" \
        "Complete audit for $total_sources sources at: $AUDIT_LOG" \
        --priority medium \
        --channel auto
fi

echo "✓ Notification sent"
