#!/usr/bin/env python3
"""
Generate governance summary YAML files from full governance documents.
Part of Summary-First Hybrid approach for governance token reduction.

Creates:
- DECISIONS-SUMMARY.yaml
- ISSUES-SUMMARY.yaml
- IDEAS-SUMMARY.yaml

Usage: python3 generate-governance-summaries.py
"""

import re
import yaml
from pathlib import Path
from datetime import datetime
import hashlib

DEV_ENV_DOCS = Path.home() / "dev/infrastructure/dev-env-docs"
OUTPUT_DIR = DEV_ENV_DOCS

def get_file_hash(filepath: Path) -> str:
    """Get short hash of file for change detection."""
    content = filepath.read_text()
    return hashlib.md5(content.encode()).hexdigest()[:12]

def parse_decisions(filepath: Path) -> dict:
    """Parse DECISIONS-LOG.md into summary structure."""
    content = filepath.read_text()

    # Pattern: ### DEC-NNN | DATE | CATEGORY | STATUS | Title
    pattern = r'^### (DEC-\d+) \| ([\d-]+) \| (\w+) \| (\w+) \| (.+)$'

    decisions = {}
    for match in re.finditer(pattern, content, re.MULTILINE):
        dec_id, date, category, status, title = match.groups()
        decisions[dec_id] = {
            'date': date,
            'category': category,
            'status': status,
            'title': title.strip()
        }

    return decisions

def parse_issues(filepath: Path) -> dict:
    """Parse ISSUES-TRACKER.md into summary structure."""
    content = filepath.read_text()

    # Pattern: ### ISSUE-NNN: Title or ### ISSUE-NNN | ... | Title
    pattern1 = r'^### (ISSUE-\d+): (.+)$'
    pattern2 = r'^### (ISSUE-\d+) \| .+ \| (\w+) \| (.+)$'

    issues = {}

    # Try format 1
    for match in re.finditer(pattern1, content, re.MULTILINE):
        issue_id, title = match.groups()
        # Look for status in nearby lines
        issues[issue_id] = {
            'title': title.strip(),
            'status': 'OPEN'  # Default, will be updated if found
        }

    # Try format 2 (with status)
    for match in re.finditer(pattern2, content, re.MULTILINE):
        issue_id, status, title = match.groups()
        issues[issue_id] = {
            'title': title.strip(),
            'status': status
        }

    # Also look for status markers
    status_pattern = r'(ISSUE-\d+).*\*\*Status:\*\* (\w+)'
    for match in re.finditer(status_pattern, content):
        issue_id, status = match.groups()
        if issue_id in issues:
            issues[issue_id]['status'] = status

    return issues

def parse_ideas(filepath: Path) -> dict:
    """Parse IDEAS-BACKLOG.md into summary structure."""
    content = filepath.read_text()

    # Pattern: ### IDEA-NNN: Title
    pattern = r'^### (IDEA-\d+): (.+)$'

    ideas = {}
    current_idea = None

    for match in re.finditer(pattern, content, re.MULTILINE):
        idea_id, title = match.groups()
        ideas[idea_id] = {
            'title': title.strip(),
            'status': 'Parking'  # Default
        }

    # Look for status markers
    status_pattern = r'(IDEA-\d+).*\*\*Status:\*\* (\w+)'
    for match in re.finditer(status_pattern, content):
        idea_id, status = match.groups()
        if idea_id in ideas:
            ideas[idea_id]['status'] = status

    return ideas

def write_summary(data: dict, output_path: Path, source_path: Path, item_type: str):
    """Write summary YAML file."""

    header = f"""# {item_type.upper()}-SUMMARY.yaml
# Generated lightweight index for fast discovery
# Created: {datetime.now().isoformat()}
# Source hash: {get_file_hash(source_path)}
# Full file: {source_path}
# Use summary for quick lookups; query MCP or read full file for details
#
"""

    summary = {
        'version': '1.0',
        'generated_at': datetime.now().isoformat(),
        'source_file': str(source_path),
        'source_hash': get_file_hash(source_path),
        'total_count': len(data),
        'note': f'Quick lookup index. For full details, use governance MCP query_{item_type.lower()}(id) or read {source_path.name}',
        f'{item_type.lower()}s': data
    }

    with open(output_path, 'w') as f:
        f.write(header)
        yaml.dump(summary, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return len(data)

def main():
    print("Generating governance summary files...")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    # Decisions
    decisions_path = DEV_ENV_DOCS / "DECISIONS-LOG.md"
    if decisions_path.exists():
        decisions = parse_decisions(decisions_path)
        count = write_summary(
            decisions,
            OUTPUT_DIR / "DECISIONS-SUMMARY.yaml",
            decisions_path,
            "decision"
        )
        print(f"✓ DECISIONS-SUMMARY.yaml: {count} decisions indexed")
    else:
        print(f"✗ DECISIONS-LOG.md not found at {decisions_path}")

    # Issues
    issues_path = DEV_ENV_DOCS / "ISSUES-TRACKER.md"
    if issues_path.exists():
        issues = parse_issues(issues_path)
        count = write_summary(
            issues,
            OUTPUT_DIR / "ISSUES-SUMMARY.yaml",
            issues_path,
            "issue"
        )
        print(f"✓ ISSUES-SUMMARY.yaml: {count} issues indexed")
    else:
        print(f"✗ ISSUES-TRACKER.md not found at {issues_path}")

    # Ideas
    ideas_path = DEV_ENV_DOCS / "IDEAS-BACKLOG.md"
    if ideas_path.exists():
        ideas = parse_ideas(ideas_path)
        count = write_summary(
            ideas,
            OUTPUT_DIR / "IDEAS-SUMMARY.yaml",
            ideas_path,
            "idea"
        )
        print(f"✓ IDEAS-SUMMARY.yaml: {count} ideas indexed")
    else:
        print(f"✗ IDEAS-BACKLOG.md not found at {ideas_path}")

    print()
    print("Summary generation complete.")
    print("Next: Update CLAUDE.md to reference these summaries instead of full files.")

if __name__ == "__main__":
    main()
