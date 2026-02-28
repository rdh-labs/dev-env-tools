#!/usr/bin/env bash
# source-freshness-scan.sh — Find stale source references in governance artifacts
# Pattern: GOV-019 (Source Freshness and Re-Evaluation)
# Runs: monthly via cron, or on-demand
#
# Scans governance patterns, research docs, and decisions for:
# 1. External URL references without freshness metadata
# 2. Freshness metadata past its review date
# 3. Freshness metadata older than threshold

set -euo pipefail

PATTERNS_DIR="$HOME/dev/infrastructure/dev-env-docs/governance-patterns"
RESEARCH_DIR="$HOME/dev/infrastructure/dev-env-docs/research"
DECISIONS="$HOME/dev/infrastructure/dev-env-docs/DECISIONS-LOG.md"
TODAY=$(date +%Y-%m-%d)
STALE_THRESHOLD_DAYS=${1:-180}  # Default: 6 months, override via arg

echo "=== Source Freshness Scan (GOV-019) ==="
echo "Date: $TODAY"
echo "Threshold: ${STALE_THRESHOLD_DAYS} days"
echo ""

stale_count=0
tracked_count=0
fresh_count=0

scan_file() {
  local f="$1"
  local section="$2"
  local basename
  basename=$(basename "$f")

  # Count external URLs (skip internal file paths)
  local url_count
  url_count=$(grep -cP 'https?://' "$f" 2>/dev/null || true)
  url_count=${url_count:-0}
  url_count=$(echo "$url_count" | tr -d '[:space:]')

  [[ "$url_count" -eq 0 ]] && return

  # Check for source_freshness validated date
  local validated
  validated=$(grep -oP '(?:validated|Validated)[:\s]*"?\K\d{4}-\d{2}-\d{2}' "$f" 2>/dev/null | tail -1 || true)

  # Check for next_review / review by date
  local next_review
  next_review=$(grep -oP '(?:next_review|Review by|review.by)[:\s]*"?\K\d{4}-\d{2}-\d{2}' "$f" 2>/dev/null | tail -1 || true)

  if [[ -z "$validated" ]]; then
    echo "  ⚠️  $basename: $url_count external URLs, NO freshness metadata"
    stale_count=$((stale_count + 1))
  elif [[ -n "$next_review" && "$next_review" < "$TODAY" ]]; then
    echo "  🔴 $basename: Review OVERDUE (was due $next_review, validated $validated)"
    stale_count=$((stale_count + 1))
  else
    local days_since
    days_since=$(( ($(date -d "$TODAY" +%s) - $(date -d "$validated" +%s)) / 86400 ))
    if [[ $days_since -gt $STALE_THRESHOLD_DAYS ]]; then
      echo "  🟡 $basename: Last validated $days_since days ago ($validated)"
      stale_count=$((stale_count + 1))
    else
      echo "  ✅ $basename: Fresh (validated $validated, ${days_since}d ago)"
      fresh_count=$((fresh_count + 1))
    fi
    tracked_count=$((tracked_count + 1))
  fi
}

# Scan governance patterns
echo "## Governance Patterns"
for f in "$PATTERNS_DIR"/*.md; do
  [[ -f "$f" ]] || continue
  scan_file "$f" "patterns"
done

# Scan research documents
echo ""
echo "## Research Documents"
if [[ -d "$RESEARCH_DIR" ]]; then
  for f in "$RESEARCH_DIR"/*.md; do
    [[ -f "$f" ]] || continue
    scan_file "$f" "research"
  done
else
  echo "  (no research directory)"
fi

echo ""
echo "=== Summary ==="
echo "Fresh (with metadata, within threshold): $fresh_count"
echo "Tracked (have metadata): $tracked_count"
echo "Stale or untracked: $stale_count"

if [[ $stale_count -gt 0 ]]; then
  echo ""
  echo "Action: Review stale sources and update freshness metadata"
  echo "Pattern: ~/dev/infrastructure/dev-env-docs/governance-patterns/19-source-freshness-and-re-evaluation.md"

  # Notify if stale count is concerning
  if [[ -x "$HOME/bin/notify.sh" && $stale_count -gt 3 ]]; then
    "$HOME/bin/notify.sh" \
      "Source Freshness Alert" \
      "$stale_count governance artifacts have stale or untracked source references" \
      --priority medium --channel auto
  fi
fi

exit $stale_count  # Non-zero exit if stale items found (useful for CI/cron)
