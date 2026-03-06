#!/usr/bin/env python3
"""
scan-revival-triggers.py — Report Parked ideas without revival triggers.

Part of IDEA-589: Structured parking format with mandatory revival triggers.

Scans IDEAS-BACKLOG.md for entries with Status=Parking that lack a
**Revival Trigger:** field. Reports counts and lists missing entries.

Also validates existing revival triggers for well-formedness:
- Time-based: must include a date or duration ("review in 90 days", "by 2026-06")
- Event-based: must reference a concrete event ("when X is implemented")
- Condition-based: must state a measurable condition ("if Y exceeds Z")

Usage:
    python3 scan-revival-triggers.py [--warn-only] [--json]
"""

import re
import sys
import json
from pathlib import Path
from datetime import date

BACKLOG = Path.home() / "dev/infrastructure/dev-env-docs/IDEAS-BACKLOG.md"


def parse_ideas_with_revival(content: str) -> list[dict]:
    """Parse IDEAS-BACKLOG.md and extract revival trigger status."""
    entries = []
    blocks = re.split(r'\n(?=### IDEA-)', content)

    for block in blocks:
        block = block.strip()
        if not block.startswith('### IDEA-'):
            continue

        # Extract ID
        m = re.match(r'^### (IDEA-\d+)', block)
        if not m:
            continue
        idea_id = m.group(1)

        # Extract status
        status = 'Parking'
        status_match = re.search(r'\*\*Status:\*\*\s*(\w+)', block)
        if status_match:
            status = status_match.group(1)

        # Only check Parking items
        if status != 'Parking':
            continue

        # Check for revival trigger (must be at start of line, not in description text)
        revival_match = re.search(r'^\*\*Revival Trigger:\*\*\s*(.+)', block, re.MULTILINE)
        has_trigger = revival_match is not None
        trigger_text = revival_match.group(1).strip() if revival_match else None

        # Extract title
        title_match = re.match(r'^### IDEA-\d+[:\|]\s*(.+)$', block, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else '(unknown)'

        entries.append({
            'id': idea_id,
            'title': title,
            'has_trigger': has_trigger,
            'trigger': trigger_text,
        })

    return entries


def validate_trigger(trigger_text: str) -> tuple[bool, str]:
    """
    Validate a revival trigger for well-formedness.

    Returns (is_valid, trigger_type).
    """
    t = trigger_text.lower()

    # Time-based patterns
    time_patterns = [
        r'review\s+(in|by|after)',
        r'\d{4}-\d{2}',
        r'\d+\s+(day|week|month|quarter)',
        r'(q[1-4]|january|february|march|april|may|june|july|august|september|october|november|december)',
    ]
    for p in time_patterns:
        if re.search(p, t):
            return (True, 'time-based')

    # Event-based patterns
    event_patterns = [
        r'when\s+',
        r'after\s+.+\s+is\s+(implemented|completed|released|merged|deployed)',
        r'once\s+',
        r'if\s+.+\s+(is|are)\s+(implemented|available|released)',
    ]
    for p in event_patterns:
        if re.search(p, t):
            return (True, 'event-based')

    # Condition-based patterns
    condition_patterns = [
        r'if\s+.+\s+(exceeds?|reaches?|drops?|falls?)',
        r'when\s+.+\s+(>|<|>=|<=|exceeds?|reaches?)',
    ]
    for p in condition_patterns:
        if re.search(p, t):
            return (True, 'condition-based')

    # Fallback: any non-empty trigger with actionable language
    if len(trigger_text) > 10:
        return (True, 'unclassified')

    return (False, 'invalid')


def main():
    warn_only = '--warn-only' in sys.argv
    as_json = '--json' in sys.argv

    if not BACKLOG.exists():
        print(f"ERROR: {BACKLOG} not found", file=sys.stderr)
        sys.exit(1)

    content = BACKLOG.read_text()
    entries = parse_ideas_with_revival(content)

    missing = [e for e in entries if not e['has_trigger']]
    with_trigger = [e for e in entries if e['has_trigger']]

    # Validate existing triggers
    valid_triggers = []
    invalid_triggers = []
    for e in with_trigger:
        is_valid, trigger_type = validate_trigger(e['trigger'])
        e['trigger_type'] = trigger_type
        e['trigger_valid'] = is_valid
        if is_valid:
            valid_triggers.append(e)
        else:
            invalid_triggers.append(e)

    if as_json:
        result = {
            'total_parking': len(entries),
            'with_trigger': len(with_trigger),
            'missing_trigger': len(missing),
            'valid_triggers': len(valid_triggers),
            'invalid_triggers': len(invalid_triggers),
            'missing': [{'id': e['id'], 'title': e['title']} for e in missing],
            'invalid': [{'id': e['id'], 'trigger': e['trigger']} for e in invalid_triggers],
        }
        print(json.dumps(result, indent=2))
    else:
        print(f"Revival Trigger Scan — {date.today().isoformat()}")
        print(f"{'=' * 60}")
        print(f"Parked ideas:       {len(entries)}")
        print(f"With trigger:       {len(with_trigger)}")
        print(f"  Valid:            {len(valid_triggers)}")
        print(f"  Invalid/weak:     {len(invalid_triggers)}")
        print(f"Missing trigger:    {len(missing)}")
        print()

        if missing:
            print(f"IDEAS WITHOUT REVIVAL TRIGGER ({len(missing)}):")
            for e in missing:
                print(f"  {e['id']}: {e['title'][:65]}")
            print()

        if invalid_triggers:
            print(f"IDEAS WITH INVALID/WEAK TRIGGER ({len(invalid_triggers)}):")
            for e in invalid_triggers:
                print(f"  {e['id']}: trigger=\"{e['trigger'][:60]}\"")
            print()

        if valid_triggers:
            print(f"VALID TRIGGERS ({len(valid_triggers)}):")
            for e in valid_triggers:
                print(f"  {e['id']}: [{e['trigger_type']}] {e['trigger'][:60]}")

    if not warn_only and missing:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
