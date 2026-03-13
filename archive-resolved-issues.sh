#!/usr/bin/env bash
# archive-resolved-issues.sh
#
# Archives RESOLVED and ISSUE-R entries from ISSUES-TRACKER.md to
# archive/governance/ISSUES-TRACKER-archive.md, then commits + pushes.
#
# Run monthly via cron to keep ISSUES-TRACKER.md under 470KB threshold.
# See: ISSUE-2060, ISSUE-TRACKER archival 2026-03-09
#
# Usage:
#   ./archive-resolved-issues.sh [--dry-run]
#
# Cron (monthly, 2 AM on the 1st):
#   0 2 1 * * /home/ichardart/dev/infrastructure/tools/archive-resolved-issues.sh

set -euo pipefail

TRACKER="$HOME/dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md"
ARCHIVE="$HOME/dev/infrastructure/dev-env-docs/archive/governance/ISSUES-TRACKER-archive.md"
THRESHOLD=470000
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

current_size=$(wc -c < "$TRACKER")

if (( current_size < THRESHOLD )); then
    echo "ISSUES-TRACKER.md is ${current_size} bytes (under ${THRESHOLD} threshold) — no archival needed."
    exit 0
fi

echo "ISSUES-TRACKER.md is ${current_size} bytes (threshold: ${THRESHOLD}) — archiving resolved issues..."

python3 - "$TRACKER" "$ARCHIVE" "$DRY_RUN" <<'PYEOF'
import sys
import re
from datetime import datetime

tracker_path, archive_path, dry_run_str = sys.argv[1], sys.argv[2], sys.argv[3]
dry_run = dry_run_str.lower() == "true"

with open(tracker_path, 'r') as f:
    content = f.read()

all_matches = list(re.finditer(r'^### ISSUE-', content, re.MULTILINE))
if not all_matches:
    print("No issues found — nothing to archive.")
    sys.exit(0)

preamble = content[:all_matches[0].start()]
blocks = []

for i, m in enumerate(all_matches):
    start = m.start()
    end = all_matches[i+1].start() if i+1 < len(all_matches) else len(content)
    heading_end = content.index('\n', start)
    heading = content[start:heading_end]

    is_resolved = bool(re.search(r'\|\s*(RESOLVED|Resolved|WONTFIX|CLOSED|DECOMMISSION)', heading))
    is_r_entry = bool(re.match(r'^### ISSUE-R', heading))
    should_archive = is_resolved or is_r_entry

    blocks.append({
        'heading': heading.strip(),
        'archive': should_archive,
        'text': content[start:end]
    })

keep_blocks = [b for b in blocks if not b['archive']]
archive_blocks = [b for b in blocks if b['archive']]

if not archive_blocks:
    print("No RESOLVED or ISSUE-R entries found — nothing to archive.")
    sys.exit(0)

new_size = len(preamble.encode('utf-8')) + sum(len(b['text'].encode('utf-8')) for b in keep_blocks)

print(f"Archiving {len(archive_blocks)} entries (keep {len(keep_blocks)}).")
print(f"Projected size: {new_size} bytes (current: {len(content.encode('utf-8'))})")

for b in archive_blocks:
    print(f"  → {b['heading'][:80]}")

if dry_run:
    print("[DRY RUN] No files modified.")
    sys.exit(0)

# Write updated tracker
new_tracker = preamble + ''.join(b['text'] for b in keep_blocks)
with open(tracker_path, 'w') as f:
    f.write(new_tracker)

# Append to archive
today = datetime.now().strftime('%Y-%m-%d')
archive_header = f"\n\n---\n\n## Archived {today} — Resolved/Completed Issues\n\n"
with open(archive_path, 'a') as f:
    f.write(archive_header + ''.join(b['text'] for b in archive_blocks))

print("Done.")
PYEOF

if [[ "$DRY_RUN" == "true" ]]; then
    exit 0
fi

# Commit and push
cd "$HOME/dev/infrastructure/dev-env-docs"

if ! git diff --quiet ISSUES-TRACKER.md archive/governance/ISSUES-TRACKER-archive.md 2>/dev/null; then
    count=$(git diff --stat HEAD -- ISSUES-TRACKER.md | grep -oP '\d+(?= deletion)' || echo "?")
    git add ISSUES-TRACKER.md archive/governance/ISSUES-TRACKER-archive.md
    git commit -m "chore: archive resolved issues from ISSUES-TRACKER.md (monthly maintenance)

Automated archival of RESOLVED and ISSUE-R entries. Keeps tracker under
the 470KB pre-commit warning threshold. See ISSUE-2060.

Co-Authored-By: archive-resolved-issues.sh (automated)"
    git push
    echo "Committed and pushed."
    ~/bin/notify.sh "ISSUES-TRACKER Archival" "Archived resolved issues. Tracker now $(wc -c < "$TRACKER") bytes." --priority low --channel auto 2>/dev/null || true
else
    echo "No changes to commit."
fi
