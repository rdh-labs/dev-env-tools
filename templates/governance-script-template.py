#!/usr/bin/env python3
"""
Governance File Script Template — IDEA-862 / ISSUE-2704
==========================================================
MANDATORY: All scripts that modify governance files (ISSUES-TRACKER.md,
IDEAS-BACKLOG.md, DECISIONS-LOG.md, LESSONS-LOG.md, settings.json, CLAUDE.md)
MUST implement --dry-run / --execute separation per the Governance File Script
Safety Protocol (IDEA-862, L-415).

PROTOCOL:
  1. Run with --dry-run first: shows what will be changed, no writes
  2. Review output: confirm ALL targets are the intended type/status
  3. Run with --execute: only after dry-run output is verified

Usage:
  python3 this_script.py --dry-run    # Preview — no changes written
  python3 this_script.py --execute    # Execute — writes changes after dry-run
  python3 this_script.py              # Defaults to --dry-run (safety default)

Related: ISSUE-2704, ISSUE-2705, ISSUE-2706, L-415, IDEA-862, IDEA-863
"""
import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration — edit these for your script
# ---------------------------------------------------------------------------
GOVERNANCE_FILE = Path.home() / "dev/infrastructure/dev-env-docs/ISSUES-TRACKER.md"
SCRIPT_DESCRIPTION = "TODO: describe what this script does to the governance file"


def parse_args():
    parser = argparse.ArgumentParser(description=SCRIPT_DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview changes without writing (DEFAULT — always run this first)",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Apply changes (only after --dry-run output has been verified)",
    )
    return parser.parse_args()


def find_targets(content: str) -> list[dict]:
    """
    TODO: implement target selection logic.
    Return a list of dicts describing what will be changed.
    Each dict should have at minimum: {'id': str, 'description': str, 'action': str}
    """
    targets = []
    # Example:
    # for match in re.finditer(r'^### ISSUE-\d+.*\| RESOLVED \|', content, re.MULTILINE):
    #     targets.append({'id': match.group(0)[:20], 'description': match.group(0), 'action': 'archive'})
    return targets


def preview(targets: list[dict]) -> None:
    """Print dry-run output — what WOULD be changed."""
    print(f"\n[DRY RUN] {SCRIPT_DESCRIPTION}")
    print(f"Governance file: {GOVERNANCE_FILE}")
    print(f"{'='*60}")
    if not targets:
        print("  No targets found matching criteria.")
        return
    print(f"  {len(targets)} item(s) would be affected:\n")
    for t in targets:
        print(f"  [{t['action'].upper()}] {t['id']}")
        print(f"    {t['description'][:100]}")
    print(f"\n{'='*60}")
    print(f"Run with --execute to apply these {len(targets)} change(s).")
    print("VERIFY: Are all targets above the correct type/status?")


def execute(content: str, targets: list[dict]) -> str:
    """
    TODO: implement the actual transformation.
    Return the modified content string.
    """
    new_content = content
    # Apply changes here
    return new_content


def main():
    args = parse_args()
    dry_run = not args.execute  # Default is dry-run unless --execute explicitly passed

    if not GOVERNANCE_FILE.exists():
        print(f"ERROR: Governance file not found: {GOVERNANCE_FILE}", file=sys.stderr)
        sys.exit(1)

    content = GOVERNANCE_FILE.read_text()
    targets = find_targets(content)

    if dry_run:
        preview(targets)
        sys.exit(0)

    # --- EXECUTE MODE ---
    print(f"\n[EXECUTE] {SCRIPT_DESCRIPTION}")
    print(f"Targets: {len(targets)} item(s)")

    if not targets:
        print("Nothing to do.")
        sys.exit(0)

    # Show compact target list before writing
    for t in targets:
        print(f"  [{t['action'].upper()}] {t['id']}")

    new_content = execute(content, targets)

    if new_content == content:
        print("WARNING: execute() returned unchanged content. Check implementation.")
        sys.exit(1)

    GOVERNANCE_FILE.write_text(new_content)
    print(f"\n✅ Done. {len(targets)} change(s) applied to {GOVERNANCE_FILE.name}")
    print("Run: git diff to verify, then git add + git commit")


if __name__ == "__main__":
    main()
