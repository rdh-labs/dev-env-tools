#!/usr/bin/env bash
# batch-resolve-issues.sh
#
# Bulk-update ISSUE status: OPEN → RESOLVED in ISSUES-TRACKER.md headers
# AND ISSUES-SUMMARY.yaml atomically.
#
# This closes the 60-RESOLVED-loss root cause (IDEA-639, ISSUE-2256): triage
# was updating YAML-only, causing regeneration to overwrite statuses back to
# OPEN. This script updates both representations atomically so the pre-commit
# consistency check (IDEA-632) always sees a clean state.
#
# Features:
#   - Idempotent: already-RESOLVED issues are skipped (not an error)
#   - Atomic writes: tempfile + os.replace for both TRACKER and YAML
#   - Dry-run: shows what would change without writing
#   - Error handling: not-found issues warn and continue (don't abort batch)
#
# Usage:
#   batch-resolve-issues.sh [--dry-run] ISSUE-NNN [ISSUE-MMM ...]
#
# Examples:
#   batch-resolve-issues.sh --dry-run ISSUE-2100 ISSUE-2101
#   batch-resolve-issues.sh ISSUE-2100 ISSUE-2101 ISSUE-2102
#
# Related:
#   IDEA-639: This script
#   ISSUE-2256: Root cause — no enforcement of tracker header updates
#   ISSUE-2257: archive-resolved-issues.sh --dry-run bug (separate fix)
#   IDEA-632: Pre-commit consistency check that this script supports

set -euo pipefail

TRACKER="$HOME/dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md"
SUMMARY="$HOME/dev/infrastructure/dev-env-docs/ISSUES-SUMMARY.yaml"
DRY_RUN=false
IDS=()

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=true
            ;;
        ISSUE-*)
            IDS+=("$arg")
            ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] ISSUE-NNN [ISSUE-MMM ...]"
            exit 0
            ;;
        *)
            echo "Error: Unknown argument '$arg'. Expected --dry-run or ISSUE-NNN." >&2
            echo "Usage: $0 [--dry-run] ISSUE-NNN [ISSUE-MMM ...]" >&2
            exit 1
            ;;
    esac
done

if [[ ${#IDS[@]} -eq 0 ]]; then
    echo "Error: No ISSUE IDs provided." >&2
    echo "Usage: $0 [--dry-run] ISSUE-NNN [ISSUE-MMM ...]" >&2
    exit 1
fi

if [[ ! -f "$TRACKER" ]]; then
    echo "Error: ISSUES-TRACKER.md not found at $TRACKER" >&2
    exit 1
fi

if [[ ! -f "$SUMMARY" ]]; then
    echo "Error: ISSUES-SUMMARY.yaml not found at $SUMMARY" >&2
    exit 1
fi

# Delegate file updates to Python for atomic dual-file writes and YAML safety
python3 - "$TRACKER" "$SUMMARY" "$DRY_RUN" "${IDS[@]}" <<'PYEOF'
import sys
import re
import os

tracker_path = sys.argv[1]
summary_path = sys.argv[2]
dry_run = sys.argv[3].lower() == "true"
issue_ids = sys.argv[4:]

updated = 0
skipped = 0
not_found = 0

# --- Read tracker ---
with open(tracker_path, 'r', encoding='utf-8') as f:
    tracker_content = f.read()

# --- Read YAML as text (line-based update preserves all formatting/comments) ---
with open(summary_path, 'r', encoding='utf-8') as f:
    summary_lines = f.readlines()

new_tracker = tracker_content
new_summary_lines = list(summary_lines)


def update_yaml_status(lines, issue_id, new_status):
    """Update status field for issue_id in YAML content (line-based, preserves formatting)."""
    result = []
    in_issue_block = False
    status_updated = False
    id_key = f"  {issue_id}:"

    for line in lines:
        stripped = line.rstrip('\n')
        if stripped == id_key:
            in_issue_block = True
            result.append(line)
        elif in_issue_block:
            if stripped.startswith('    status: '):
                result.append(f'    status: {new_status}\n')
                in_issue_block = False
                status_updated = True
            elif stripped and not stripped.startswith('    '):
                # Left the issue block (back to top-level key)
                in_issue_block = False
                result.append(line)
            else:
                result.append(line)
        else:
            result.append(line)

    return result, status_updated


for issue_id in issue_ids:
    # Match tracker header: ### ISSUE-NNN | YYYY-MM-DD | STATUS | severity | title
    header_pattern = re.compile(
        r'^(### ' + re.escape(issue_id) + r' \| \d{4}-\d{2}-\d{2} \| )([A-Z_]+)( \| .+)$',
        re.MULTILINE
    )
    match = header_pattern.search(new_tracker)

    if not match:
        print(f"  WARNING: {issue_id} — header not found in tracker, skipping")
        not_found += 1
        continue

    current_status = match.group(2)
    if current_status == 'RESOLVED':
        print(f"  SKIP:    {issue_id} — already RESOLVED")
        skipped += 1
        continue

    prefix = '[DRY-RUN] ' if dry_run else ''
    print(f"  {prefix}RESOLVE: {issue_id}  ({current_status} → RESOLVED)")

    if not dry_run:
        new_tracker = header_pattern.sub(
            match.group(1) + 'RESOLVED' + match.group(3),
            new_tracker,
            count=1
        )

    # Update YAML (line-based)
    new_summary_lines, yaml_updated = update_yaml_status(new_summary_lines, issue_id, 'RESOLVED')
    if not yaml_updated and not dry_run:
        print(f"  WARNING: {issue_id} — not found in ISSUES-SUMMARY.yaml (tracker updated only)")

    updated += 1

print(f"\nResult: {updated} updated, {skipped} skipped (already resolved), {not_found} not found in tracker")

if dry_run:
    print("[DRY-RUN] No files written.")
    sys.exit(0)

if updated == 0:
    sys.exit(0)

# Atomic write — tracker
tracker_tmp = tracker_path + '.brs.tmp'
try:
    with open(tracker_tmp, 'w', encoding='utf-8') as f:
        f.write(new_tracker)
    os.replace(tracker_tmp, tracker_path)
    print(f"  ✓ ISSUES-TRACKER.md updated")
except Exception as e:
    if os.path.exists(tracker_tmp):
        os.unlink(tracker_tmp)
    print(f"  ERROR: Failed to write ISSUES-TRACKER.md: {e}", file=sys.stderr)
    sys.exit(1)

# Atomic write — YAML
summary_tmp = summary_path + '.brs.tmp'
try:
    with open(summary_tmp, 'w', encoding='utf-8') as f:
        f.writelines(new_summary_lines)
    os.replace(summary_tmp, summary_path)
    print(f"  ✓ ISSUES-SUMMARY.yaml updated")
except Exception as e:
    if os.path.exists(summary_tmp):
        os.unlink(summary_tmp)
    print(f"  ERROR: Failed to write ISSUES-SUMMARY.yaml: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
