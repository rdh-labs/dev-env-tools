#!/usr/bin/env python3
"""
migrate_governance_headers.py -- Normalize governance file entry header formats.

Fixes ISSUE-3017: mixed header formats cause silent false negatives in grep tooling.

Canonical formats:
  IDEAS-BACKLOG.md  -> colon: ### IDEA-NNN: title
  ISSUES-TRACKER.md -> pipe:  ### ISSUE-NNN | date | status | severity | title

Migrations performed:
  IDEAS-BACKLOG.md:  pipe "### IDEA-NNN | date | status | title" -> colon "### IDEA-NNN: title"
  ISSUES-TRACKER.md: colon "### ISSUE-NNN: title" -> pipe using date/status/severity from body

Usage:
  python3 migrate_governance_headers.py --dry-run   # default: preview only
  python3 migrate_governance_headers.py --apply      # apply in-place (atomic write)
"""

import re
import sys
import os
import tempfile
from datetime import date
from pathlib import Path


GOVERNANCE_DIR = Path.home() / "dev/infrastructure/dev-env-docs"
IDEAS_FILE = GOVERNANCE_DIR / "IDEAS-BACKLOG.md"
ISSUES_FILE = GOVERNANCE_DIR / "ISSUES-TRACKER.md"

TODAY = date.today().strftime("%Y-%m-%d")


def atomic_write(path: Path, content: str) -> None:
    """Write file atomically using tempfile + os.replace."""
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".migrate_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def migrate_ideas(content: str) -> tuple[str, int]:
    """
    Migrate pipe-format headers in IDEAS-BACKLOG.md to colon format.

    ### IDEA-NNN | date | status | title  ->  ### IDEA-NNN: title
    Body is unchanged (already has **Added:** and **Status:** fields).

    Returns (new_content, count_changed).
    """
    pattern = re.compile(
        r'^(### IDEA-\d+) \| [^|\n]+ \| [^|\n]+ \| (.+)$',
        re.MULTILINE
    )
    matches = pattern.findall(content)
    count = len(matches)
    new_content = pattern.sub(r'\1: \2', content)
    return new_content, count


def _extract_issue_fields(body_block: str) -> tuple[str, str, str]:
    """
    Extract date, status, severity from an ISSUE entry body block.

    Returns (date_str, status_str, severity_str) with fallbacks.
    """
    # Date: **Reported:** or **Discovered:**
    date_match = re.search(
        r'\*\*(?:Reported|Discovered):\*\*\s*(\d{4}-\d{2}-\d{2})',
        body_block
    )
    date_str = date_match.group(1) if date_match else TODAY

    # Status: first word after **Status:** (handles "RESOLVED | 2026-04-03 | ..." too)
    status_match = re.search(r'\*\*Status:\*\*\s*(\w+)', body_block)
    status_str = status_match.group(1).upper() if status_match else "OPEN"

    # Severity
    severity_match = re.search(r'\*\*Severity:\*\*\s*(\w+)', body_block)
    severity_str = severity_match.group(1).upper() if severity_match else "MEDIUM"

    return date_str, status_str, severity_str


def migrate_issues(content: str) -> tuple[str, int]:
    """
    Migrate colon-format headers in ISSUES-TRACKER.md to pipe format.

    ### ISSUE-NNN: title  ->  ### ISSUE-NNN | date | status | severity | title

    Date/status/severity are extracted from the body block below each header.
    Returns (new_content, count_changed).
    """
    colon_pattern = re.compile(
        r'^(### (ISSUE-\d+): (.+))$',
        re.MULTILINE
    )

    def replace_header(m: re.Match) -> str:
        full_match_start = m.start()
        issue_id = m.group(2)
        title = m.group(3)

        # Find body block: from end of this line to next "---" separator
        block_start = m.end()
        sep_match = re.search(r'\n---\n', content[block_start:])
        if sep_match:
            body_block = content[block_start: block_start + sep_match.start()]
        else:
            body_block = content[block_start:]

        date_str, status_str, severity_str = _extract_issue_fields(body_block)
        return f"### {issue_id} | {date_str} | {status_str} | {severity_str} | {title}"

    count = len(colon_pattern.findall(content))
    new_content = colon_pattern.sub(replace_header, content)
    return new_content, count


def run(dry_run: bool) -> int:
    """
    Run migration. Returns exit code (0=ok, 1=error).
    """
    mode_label = "DRY-RUN" if dry_run else "APPLY"
    print(f"migrate_governance_headers.py [{mode_label}]")
    print()

    errors = []

    # --- IDEAS-BACKLOG ---
    if not IDEAS_FILE.exists():
        print(f"ERROR: {IDEAS_FILE} not found")
        errors.append("IDEAS file missing")
    else:
        content = IDEAS_FILE.read_text(encoding="utf-8")
        new_content, count = migrate_ideas(content)
        if count == 0:
            print(f"IDEAS-BACKLOG.md: already clean (no pipe-format headers)")
        else:
            print(f"IDEAS-BACKLOG.md: {count} pipe-format headers to migrate -> colon")
            # Show first 3 examples
            pipe_pattern = re.compile(r'^### IDEA-\d+ \| .+$', re.MULTILINE)
            for i, m in enumerate(pipe_pattern.finditer(content)):
                if i >= 3:
                    print(f"  ... ({count - 3} more)")
                    break
                # Show what it becomes
                idea_pat = re.compile(r'^(### IDEA-\d+) \| [^|\n]+ \| [^|\n]+ \| (.+)$')
                replaced = idea_pat.sub(r'\1: \2', m.group(0))
                print(f"  BEFORE: {m.group(0)}")
                print(f"  AFTER:  {replaced}")
            if not dry_run:
                atomic_write(IDEAS_FILE, new_content)
                print(f"  -> Written.")

    print()

    # --- ISSUES-TRACKER ---
    if not ISSUES_FILE.exists():
        print(f"ERROR: {ISSUES_FILE} not found")
        errors.append("ISSUES file missing")
    else:
        content = ISSUES_FILE.read_text(encoding="utf-8")
        new_content, count = migrate_issues(content)
        if count == 0:
            print(f"ISSUES-TRACKER.md: already clean (no colon-format headers)")
        else:
            print(f"ISSUES-TRACKER.md: {count} colon-format headers to migrate -> pipe")
            # Show all (only 11)
            colon_pattern = re.compile(r'^### (ISSUE-\d+): (.+)$', re.MULTILINE)
            for m in colon_pattern.finditer(content):
                issue_id = m.group(1)
                title = m.group(2)
                block_start = m.end()
                sep_match = re.search(r'\n---\n', content[block_start:])
                body = content[block_start: block_start + sep_match.start()] if sep_match else content[block_start:]
                d, s, sev = _extract_issue_fields(body)
                print(f"  BEFORE: {m.group(0)}")
                print(f"  AFTER:  ### {issue_id} | {d} | {s} | {sev} | {title}")
            if not dry_run:
                atomic_write(ISSUES_FILE, new_content)
                print(f"  -> Written.")

    print()

    if errors:
        print(f"ERRORS: {errors}")
        return 1

    if dry_run:
        print("Dry-run complete. Re-run with --apply to apply changes.")
    else:
        print("Migration complete.")

    return 0


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    sys.exit(run(dry_run))
