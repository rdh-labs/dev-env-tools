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
import argparse
from pathlib import Path
from datetime import datetime
from collections import Counter
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
    decisions = {}

    # Pattern 1: ### DEC-NNN | DATE | CATEGORY | STATUS | Title (standard 5-field)
    # Status can be multi-word like "PARTIALLY SUPERSEDED"
    pattern1 = r'^### (DEC-\d+) \| (\d{4}-\d{2}-\d{2}) \| (\w+) \| ([\w\s]+?) \| (.+)$'
    for match in re.finditer(pattern1, content, re.MULTILINE):
        dec_id, date, category, status, title = match.groups()
        decisions[dec_id] = {
            'date': date,
            'category': category,
            'status': status,
            'title': title.strip()
        }

    # Pattern 2: ### DEC-NNN | Title | Status (legacy 3-field, no date/category)
    pattern2 = r'^### (DEC-\d+) \| ([^|]+) \| (\w+)$'
    for match in re.finditer(pattern2, content, re.MULTILINE):
        dec_id, title, status = match.groups()
        if dec_id not in decisions:
            decisions[dec_id] = {
                'date': 'unknown',
                'category': 'UNKNOWN',
                'status': status,
                'title': title.strip()
            }

    # Pattern 3: ### DEC-NNN | Title (minimal 2-field)
    pattern3 = r'^### (DEC-\d+) \| ([^|]+)$'
    for match in re.finditer(pattern3, content, re.MULTILINE):
        dec_id, title = match.groups()
        if dec_id not in decisions:
            decisions[dec_id] = {
                'date': 'unknown',
                'category': 'UNKNOWN',
                'status': 'UNKNOWN',
                'title': title.strip()
            }

    return decisions

def parse_issues(filepath: Path) -> dict:
    """Parse ISSUES-TRACKER.md into summary structure."""
    content = filepath.read_text()
    issues = {}

    # Pattern 1: ### ISSUE-NNN | STATUS | CATEGORY | Title (current 4-field format)
    # Status is OPEN, RESOLVED, PARTIAL, etc. Category can have hyphens (e.g., DATA-QUALITY)
    pattern1 = r'^### (ISSUE-\d+) \| (OPEN|RESOLVED|PARTIAL|CLOSED|DEFERRED) \| ([\w-]+) \| (.+)$'
    for match in re.finditer(pattern1, content, re.MULTILINE):
        issue_id, status, category, title = match.groups()
        issues[issue_id] = {
            'title': title.strip(),
            'status': status,
            'category': category
        }

    # Pattern 2: ### ISSUE-NNN | SEVERITY | Title (legacy 3-field format)
    # Severity is Critical, High, Medium, Low
    pattern2 = r'^### (ISSUE-\d+) \| (Critical|High|Medium|Low|HIGH) \| (.+)$'
    for match in re.finditer(pattern2, content, re.MULTILINE):
        issue_id, severity, title = match.groups()
        if issue_id not in issues:
            issues[issue_id] = {
                'title': title.strip(),
                'status': 'OPEN',
                'severity': severity
            }

    # Pattern 3: ### ISSUE-NNN | DATE | STATUS | Title (date-prefixed format)
    pattern3 = r'^### (ISSUE-\d+) \| (\d{4}-\d{2}-\d{2}) \| (OPEN|RESOLVED|PARTIAL) \| (.+)$'
    for match in re.finditer(pattern3, content, re.MULTILINE):
        issue_id, date, status, title = match.groups()
        if issue_id not in issues:
            issues[issue_id] = {
                'title': title.strip(),
                'status': status,
                'date': date
            }

    # Pattern 4: ### ISSUE-NNN: Title (colon format)
    pattern4 = r'^### (ISSUE-\d+): (.+)$'
    for match in re.finditer(pattern4, content, re.MULTILINE):
        issue_id, title = match.groups()
        if issue_id not in issues:
            issues[issue_id] = {
                'title': title.strip(),
                'status': 'OPEN'
            }

    # Pattern 5: ### ISSUE-NNN | Title | Severity (title-first 3-field format)
    pattern5 = r'^### (ISSUE-\d+) \| ([^|]+) \| (Critical|High|Medium|Low)$'
    for match in re.finditer(pattern5, content, re.MULTILINE):
        issue_id, title, severity = match.groups()
        if issue_id not in issues:
            issues[issue_id] = {
                'title': title.strip(),
                'status': 'OPEN',
                'severity': severity
            }

    # Pattern 6: ### ISSUE-NNN | DATE | Title | STATUS (date-title-status 4-field)
    pattern6 = r'^### (ISSUE-\d+) \| (\d{4}-\d{2}-\d{2}) \| ([^|]+) \| (OPEN|RESOLVED|PARTIAL)$'
    for match in re.finditer(pattern6, content, re.MULTILINE):
        issue_id, date, title, status = match.groups()
        if issue_id not in issues:
            issues[issue_id] = {
                'title': title.strip(),
                'status': status,
                'date': date
            }

    # Also look for status markers in body text
    status_pattern = r'(ISSUE-\d+).*\*\*Status:\*\* (\w+)'
    for match in re.finditer(status_pattern, content):
        issue_id, status = match.groups()
        if issue_id in issues:
            issues[issue_id]['status'] = status

    return issues

def parse_ideas(filepath: Path) -> dict:
    """Parse IDEAS-BACKLOG.md into summary structure."""
    content = filepath.read_text()
    ideas = {}

    # Pattern 1: ### IDEA-NNN: Title (colon format - most common)
    pattern1 = r'^### (IDEA-\d+): (.+)$'
    for match in re.finditer(pattern1, content, re.MULTILINE):
        idea_id, title = match.groups()
        ideas[idea_id] = {
            'title': title.strip(),
            'status': 'Parking'
        }

    # Pattern 2: ### IDEA-NNN | DATE | Title (date-prefixed pipe format)
    pattern2 = r'^### (IDEA-\d+) \| (\d{4}-\d{2}-\d{2}) \| (.+)$'
    for match in re.finditer(pattern2, content, re.MULTILINE):
        idea_id, date, title = match.groups()
        if idea_id not in ideas:
            ideas[idea_id] = {
                'title': title.strip(),
                'status': 'Parking',
                'date': date
            }

    # Pattern 3: ### IDEA-NNN | CATEGORY | Title (category-prefixed pipe format)
    pattern3 = r'^### (IDEA-\d+) \| ([A-Za-z]+) \| (.+)$'
    for match in re.finditer(pattern3, content, re.MULTILINE):
        idea_id, category, title = match.groups()
        if idea_id not in ideas:
            ideas[idea_id] = {
                'title': title.strip(),
                'status': 'Parking',
                'category': category
            }

    # Pattern 4: ### IDEA-NNN | Title (simple pipe format)
    pattern4 = r'^### (IDEA-\d+) \| ([^|]+)$'
    for match in re.finditer(pattern4, content, re.MULTILINE):
        idea_id, title = match.groups()
        if idea_id not in ideas:
            ideas[idea_id] = {
                'title': title.strip(),
                'status': 'Parking'
            }

    # Look for status markers in body text
    status_pattern = r'(IDEA-\d+).*\*\*Status:\*\* (\w+)'
    for match in re.finditer(status_pattern, content):
        idea_id, status = match.groups()
        if idea_id in ideas:
            ideas[idea_id]['status'] = status

    return ideas

def find_duplicates(filepath: Path, pattern: str) -> dict:
    """Find duplicate IDs in a governance file.

    Returns dict mapping duplicate IDs to list of (line_num, full_line) tuples.
    """
    content = filepath.read_text()
    lines = content.split('\n')

    # Find all IDs with their line numbers
    id_locations = {}
    for i, line in enumerate(lines, 1):
        match = re.match(pattern, line)
        if match:
            item_id = match.group(1)
            if item_id not in id_locations:
                id_locations[item_id] = []
            id_locations[item_id].append((i, line.strip()))

    # Filter to only duplicates
    duplicates = {k: v for k, v in id_locations.items() if len(v) > 1}
    return duplicates


def validate_governance_files() -> bool:
    """Validate governance files for duplicates and format issues.

    Returns True if all files pass validation, False otherwise.
    """
    all_valid = True
    total_duplicates = 0

    print("Validating governance files...")
    print()

    # Check DECISIONS-LOG.md
    decisions_path = DEV_ENV_DOCS / "DECISIONS-LOG.md"
    if decisions_path.exists():
        duplicates = find_duplicates(decisions_path, r'^### (DEC-\d+)')
        if duplicates:
            all_valid = False
            total_duplicates += len(duplicates)
            print(f"✗ DECISIONS-LOG.md: {len(duplicates)} duplicate IDs found")
            for dec_id, locations in sorted(duplicates.items()):
                print(f"  {dec_id}: lines {', '.join(str(loc[0]) for loc in locations)}")
        else:
            print(f"✓ DECISIONS-LOG.md: No duplicates")

    # Check ISSUES-TRACKER.md
    issues_path = DEV_ENV_DOCS / "ISSUES-TRACKER.md"
    if issues_path.exists():
        duplicates = find_duplicates(issues_path, r'^### (ISSUE-\d+)')
        if duplicates:
            all_valid = False
            total_duplicates += len(duplicates)
            print(f"✗ ISSUES-TRACKER.md: {len(duplicates)} duplicate IDs found")
            for issue_id, locations in sorted(duplicates.items()):
                print(f"  {issue_id}: lines {', '.join(str(loc[0]) for loc in locations)}")
        else:
            print(f"✓ ISSUES-TRACKER.md: No duplicates")

    # Check IDEAS-BACKLOG.md
    ideas_path = DEV_ENV_DOCS / "IDEAS-BACKLOG.md"
    if ideas_path.exists():
        duplicates = find_duplicates(ideas_path, r'^### (IDEA-\d+)')
        if duplicates:
            all_valid = False
            total_duplicates += len(duplicates)
            print(f"✗ IDEAS-BACKLOG.md: {len(duplicates)} duplicate IDs found")
            for idea_id, locations in sorted(duplicates.items()):
                print(f"  {idea_id}: lines {', '.join(str(loc[0]) for loc in locations)}")
        else:
            print(f"✓ IDEAS-BACKLOG.md: No duplicates")

    print()
    if all_valid:
        print("All governance files pass validation.")
    else:
        print(f"Validation failed: {total_duplicates} duplicate IDs found across governance files.")
        print("See ISSUE-130 for remediation guidance.")

    return all_valid


def path_to_tilde(filepath: Path) -> str:
    """Convert absolute path to ~/... format to avoid leaking usernames."""
    home = Path.home()
    try:
        rel = filepath.relative_to(home)
        return f"~/{rel}"
    except ValueError:
        return str(filepath)


# MCP tool name mapping (actual tool names, not generated from item_type)
MCP_TOOL_NAMES = {
    'decision': 'query_decisions',  # Actual MCP tool name
    'issue': 'query_issues',        # Actual MCP tool name
    'idea': None,                   # No MCP tool yet - read file directly
}


def write_summary(data: dict, output_path: Path, source_path: Path, item_type: str):
    """Write summary YAML file."""
    source_tilde = path_to_tilde(source_path)
    mcp_tool = MCP_TOOL_NAMES.get(item_type.lower())

    # Build note based on whether MCP tool exists
    if mcp_tool:
        note = f'Quick lookup index. For full details, use governance MCP {mcp_tool}(id) or read {source_path.name}'
    else:
        note = f'Quick lookup index. For full details, read {source_path.name} (no MCP query tool yet)'

    header = f"""# {item_type.upper()}-SUMMARY.yaml
# Generated lightweight index for fast discovery
# Created: {datetime.now().isoformat()}
# Source hash: {get_file_hash(source_path)}
# Full file: {source_tilde}
# Use summary for quick lookups; query MCP or read full file for details
#
"""

    summary = {
        'version': '1.0',
        'generated_at': datetime.now().isoformat(),
        'source_file': source_tilde,
        'source_hash': get_file_hash(source_path),
        'total_count': len(data),
        'note': note,
        f'{item_type.lower()}s': data
    }

    with open(output_path, 'w') as f:
        f.write(header)
        yaml.dump(summary, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return len(data)

def generate_summaries():
    """Generate all governance summary files."""
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


def main():
    parser = argparse.ArgumentParser(
        description="Generate governance summary YAML files from full governance documents."
    )
    parser.add_argument(
        '--validate',
        action='store_true',
        help="Validate governance files for duplicates without generating summaries"
    )
    parser.add_argument(
        '--validate-and-generate',
        action='store_true',
        help="Validate governance files, then generate summaries regardless of result"
    )
    args = parser.parse_args()

    if args.validate:
        valid = validate_governance_files()
        exit(0 if valid else 1)
    elif args.validate_and_generate:
        validate_governance_files()
        print()
        print("-" * 60)
        print()
        generate_summaries()
    else:
        generate_summaries()


if __name__ == "__main__":
    main()
