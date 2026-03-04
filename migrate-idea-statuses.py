#!/usr/bin/env python3
"""
migrate-idea-statuses.py — One-time status normalization for IDEAS-BACKLOG.md
Part of IDEA-568: Idea Management Pipeline (PATT-020)

Normalizes 89 free-text status variants into pipeline status model.
Moves Accepted items to IDEAS-ACTIVE.md, Implemented items to ARCHIVE.md.

Usage:
    python3 migrate-idea-statuses.py [--dry-run|--apply] [--backlog PATH]

Flags:
    --dry-run   Preview changes without writing (DEFAULT)
    --apply     Execute migration (writes files, creates .bak backups)
    --backlog   Override path to IDEAS-BACKLOG.md

Output (always):
    stdout: migration report
    migration-report.txt: same report for git commit message
"""

import re
import sys
import shutil
import pathlib
from datetime import date

# ── Configuration ──────────────────────────────────────────────────────────────

BASE_DIR = pathlib.Path.home() / "dev/infrastructure/dev-env-docs"
DEFAULT_BACKLOG = BASE_DIR / "IDEAS-BACKLOG.md"
IDEAS_ACTIVE = BASE_DIR / "IDEAS-ACTIVE.md"
ARCHIVE_MD = BASE_DIR / "ARCHIVE.md"
REPORT_FILE = BASE_DIR / "migration-report.txt"

TODAY = date.today().isoformat()

# ── Status Mapping Rules ───────────────────────────────────────────────────────
# Each rule: (regex_pattern, target_status, forced_note_or_None)
# First match wins. Case-insensitive via re.IGNORECASE in apply_mapping().
# If forced_note is None, suffix extraction logic runs instead.

MAPPING_RULES = [
    # Data errors — must come first
    (r"^\s*\[.*Not Started",            "Parked",          "[DATA-ERROR] Status was template checkbox, not actual status"),
    (r"^✅",                             "Parked",          "[DATA-ERROR] Status was test output (✅ ALL TESTS PASSED)"),

    # Implemented → ARCHIVE.md (anchor to start or "Phase N IMPLEMENTED")
    (r"^(?:Phase \d+ )?IMPLEMENTED",     "Implemented",     None),

    # Active variants → Accepted (→ IDEAS-ACTIVE.md)
    (r"^ACTIVATED",                      "Accepted",        None),
    (r"^ACTIVE",                         "Accepted",        None),
    (r"^Active",                         "Accepted",        None),

    # Explicitly deferred
    (r"^Deferred",                       "Parked:Deferred", None),
    (r"→\s*\*\*FUTURE",                  "Parked:Deferred", "Post-Phase 2"),

    # User escalations → Evaluating
    (r"→\s*\*\*(CRITICAL|HIGH)",         "Evaluating",      None),

    # Evaluating / Under Review
    (r"^Evaluating",                     "Evaluating",      None),
    (r"^Under Review",                   "Evaluating",      None),

    # Scheduled / Unblocked → Accepted
    (r"^Scheduled",                      "Accepted",        None),
    (r"^Unblocked",                      "Accepted",        None),

    # Blocked → Parked with suffix as note
    (r"^Blocked",                        "Parked",          None),

    # Parking variants → Parked
    (r"^PARKING",                        "Parked",          None),
    (r"^Parking",                        "Parked",          None),
    (r"^Parking Lot",                    "Parked",          None),

    # Proposed → Parked
    (r"^PROPOSED",                       "Parked",          None),
    (r"^Proposed",                       "Parked",          None),

    # BACKLOG → Parked
    (r"^BACKLOG",                        "Parked",          None),
]

# Valid pipeline statuses (for validation pass)
VALID_STATUSES = {"Parked", "Parked:Deferred", "Parked:Stale", "Evaluating", "Accepted", "Implemented"}


# ── Entry Parsing ──────────────────────────────────────────────────────────────

def parse_entries(text: str) -> list[dict]:
    """
    Split IDEAS-BACKLOG.md into structured entries.
    Each entry: {header, raw, idea_id, status_line_idx, status_value, lines}
    Returns entries in document order.
    """
    lines = text.splitlines(keepends=True)
    entries = []
    preamble_lines = []
    i = 0

    # Collect preamble (everything before first ### IDEA-
    while i < len(lines):
        if re.match(r"^### IDEA-", lines[i]):
            break
        preamble_lines.append(lines[i])
        i += 1

    # Parse each IDEA entry
    current_entry_start = None
    current_lines = []

    while i < len(lines):
        line = lines[i]
        if re.match(r"^### IDEA-", line):
            if current_lines and current_entry_start is not None:
                entry = build_entry(current_entry_start, current_lines)
                if entry:
                    entries.append(entry)
            current_entry_start = i
            current_lines = [line]
        else:
            if current_entry_start is not None:
                current_lines.append(line)
        i += 1

    # Last entry
    if current_lines and current_entry_start is not None:
        entry = build_entry(current_entry_start, current_lines)
        if entry:
            entries.append(entry)

    return preamble_lines, entries


def build_entry(start_line: int, lines: list[str]) -> dict | None:
    """Build a structured entry dict from raw lines."""
    header = lines[0].strip()
    m = re.match(r"^### (IDEA-\d+)[:\s]", header)
    idea_id = m.group(1) if m else None

    status_line_idx = None
    status_value = None
    for idx, line in enumerate(lines):
        sm = re.match(r"^\*\*Status:\*\*\s*(.*)", line)
        if sm:
            status_line_idx = idx
            status_value = sm.group(1).strip()
            break

    return {
        "idea_id": idea_id,
        "header": header,
        "lines": lines,
        "start_line": start_line,
        "status_line_idx": status_line_idx,
        "status_value": status_value,
    }


# ── Status Mapping ─────────────────────────────────────────────────────────────

def apply_mapping(status_value: str) -> tuple[str, str | None]:
    """
    Apply mapping rules to raw status value.
    Returns (target_status, parking_note_or_None).
    """
    if status_value is None:
        return "Parked", "[DATA-ERROR] No Status field found in entry"

    for pattern, target, forced_note in MAPPING_RULES:
        if re.search(pattern, status_value, re.IGNORECASE):
            if forced_note is not None:
                return target, forced_note
            # Extract suffix note if present
            note = extract_suffix_note(status_value)
            return target, note

    # No match
    return None, None  # caller treats None target as unmapped


def extract_suffix_note(status_value: str) -> str | None:
    """
    Extract meaningful suffix from compound status values.
    Examples:
        "Parking - reason here" → "reason here"
        "Active - implementing as part of DEC-076" → "implementing as part of DEC-076"
        "Blocked (Linux) - web session capture requires macOS" → "Blocked (Linux): web session capture requires macOS"
        "Parking (blocked by ISSUE-085)" → "blocked by ISSUE-085"
        "Deferred - extend multi-check.py per DEC-024; ..." → "extend multi-check.py per DEC-024; ..."
        "ACTIVATED (2026-02-11)" → "Activated: 2026-02-11"
    """
    # "Keyword - suffix" pattern
    m = re.match(r"^[^-]+ - (.+)$", status_value, re.IGNORECASE)
    if m:
        suffix = m.group(1).strip()
        if suffix:
            return suffix

    # "Keyword (parenthetical)" pattern — extract content of first parens
    m = re.match(r"^[^(]+\(([^)]+)\)", status_value)
    if m:
        inner = m.group(1).strip()
        # Don't emit a note if the paren content is a case modifier like "(design phase)"
        # that has already been handled by the general rule
        return inner if inner else None

    # "→ **CRITICAL PRIORITY**" — extract escalation note
    m = re.search(r"→\s*\*\*(.+?)\*\*", status_value)
    if m:
        return f"Escalated: {m.group(1)}"

    return None


# ── Entry Reconstruction ───────────────────────────────────────────────────────

def reconstruct_entry(entry: dict, new_status: str, parking_note: str | None) -> list[str]:
    """
    Rebuild entry lines with updated status and optional parking note.
    Returns new lines list.
    """
    lines = list(entry["lines"])
    si = entry["status_line_idx"]

    if si is None:
        # No status field — add one after the header
        lines.insert(1, f"**Status:** {new_status}\n")
        if parking_note:
            lines.insert(2, f"**Parking Note:** {parking_note}\n")
        return lines

    # Replace status line
    lines[si] = f"**Status:** {new_status}\n"

    # Add parking note immediately after status if we have one and it doesn't already exist
    has_parking_note = any(l.startswith("**Parking Note:**") for l in lines)
    if parking_note and not has_parking_note:
        lines.insert(si + 1, f"**Parking Note:** {parking_note}\n")

    return lines


# ── IDEAS-ACTIVE.md Population ─────────────────────────────────────────────────

def format_active_entry(entry: dict, rank: int) -> str:
    """Format an entry for IDEAS-ACTIVE.md queue."""
    idea_id = entry["idea_id"] or "IDEA-???"
    # Extract title from header: "### IDEA-###: Title" or "### IDEA-### — Title"
    m = re.match(r"^### IDEA-\d+[:\s—–-]+(.+)$", entry["header"])
    title = m.group(1).strip() if m else entry["header"]

    # Extract fields from entry lines
    fields = {}
    for line in entry["lines"]:
        for field in ["Priority", "Added", "Effort", "Related", "Blocker"]:
            fm = re.match(rf"^\*\*{field}:\*\*\s*(.*)", line)
            if fm:
                fields[field] = fm.group(1).strip()

    priority = fields.get("Priority", "unassigned")
    added = fields.get("Added", "unknown")
    effort = fields.get("Effort", "unknown")
    parking_note = None
    for line in entry["lines"]:
        pm = re.match(r"^\*\*Parking Note:\*\*\s*(.*)", line)
        if pm:
            parking_note = pm.group(1).strip()

    status = entry.get("_new_status", "Accepted")
    dart_substatus = "In Progress" if status == "Accepted" else "Queued"

    lines = [f"### {rank}. {idea_id} — {title}\n"]
    lines.append(f"- **Priority:** {priority}\n")
    lines.append(f"- **Status:** {dart_substatus}\n")
    lines.append(f"- **Accepted:** {added} (migrated {TODAY})\n")
    lines.append(f"- **Effort:** {effort}\n")
    lines.append(f"- **Dependencies:** none\n")
    lines.append(f"- **Target:** next-available\n")
    lines.append(f"- **Assignee:** unassigned\n")
    if parking_note:
        lines.append(f"- **Notes:** {parking_note}\n")
    lines.append("\n")
    return "".join(lines)


# ── ARCHIVE.md Population ──────────────────────────────────────────────────────

def format_archive_implemented(entry: dict) -> str:
    """Format an Implemented entry for ARCHIVE.md §Implemented."""
    idea_id = entry["idea_id"] or "IDEA-???"
    m = re.match(r"^### IDEA-\d+[:\s—–-]+(.+)$", entry["header"])
    title = m.group(1).strip() if m else entry["header"]

    fields = {}
    for line in entry["lines"]:
        for field in ["Added", "Status"]:
            fm = re.match(rf"^\*\*{field}:\*\*\s*(.*)", line)
            if fm:
                fields[field] = fm.group(1).strip()

    added = fields.get("Added", "unknown")
    orig_status = fields.get("Status", "unknown")

    lines = [f"### {idea_id} — {title}\n"]
    lines.append(f"- **Added:** {added}\n")
    lines.append(f"- **Accepted:** unknown (pre-pipeline)\n")
    lines.append(f"- **Implemented:** {TODAY} (migrated from IDEAS-BACKLOG.md)\n")
    lines.append(f"- **Original Status:** {orig_status}\n")
    lines.append(f"- **Outcome Review:** pending\n")
    lines.append(f"- **Result:** Migrated from IDEAS-BACKLOG.md during IDEA-568 pipeline setup.\n")
    lines.append("\n")
    return "".join(lines)


# ── Main Migration Logic ───────────────────────────────────────────────────────

def migrate(backlog_path: pathlib.Path, dry_run: bool) -> int:
    """
    Main migration function.
    Returns exit code (0 = success, 1 = error).
    """
    print(f"{'DRY RUN — ' if dry_run else ''}Migration starting: {backlog_path}")
    print(f"Date: {TODAY}")
    print()

    # Read source
    if not backlog_path.exists():
        print(f"ERROR: {backlog_path} not found", file=sys.stderr)
        return 1

    text = backlog_path.read_text(encoding="utf-8")
    preamble_lines, entries = parse_entries(text)

    print(f"Parsed {len(entries)} entries from {backlog_path.name}")
    print()

    # --- Pass 1: Apply mapping rules ---
    stats = {
        "total": len(entries),
        "changed": 0,
        "already_valid": 0,
        "unmapped": 0,
        "no_status": 0,
        "to_active": 0,
        "to_archive": 0,
        "parking_notes_added": 0,
        "data_errors": 0,
        "errors": [],
    }

    unmapped_list = []
    to_active = []   # Accepted items
    to_archive = []  # Implemented items
    stay_in_backlog = []  # Everything else

    for entry in entries:
        raw_status = entry["status_value"]

        if raw_status is None:
            stats["no_status"] += 1
            stats["errors"].append(f"  {entry['idea_id']}: no **Status:** field found")
            entry["_new_status"] = "Parked"
            entry["_parking_note"] = "[DATA-ERROR] No Status field found"
            entry["_changed"] = True
            stay_in_backlog.append(entry)
            continue

        new_status, parking_note = apply_mapping(raw_status)

        if new_status is None:
            stats["unmapped"] += 1
            unmapped_list.append(f"  {entry['idea_id']}: '{raw_status}'")
            entry["_new_status"] = raw_status  # unchanged
            entry["_parking_note"] = None
            entry["_changed"] = False
            stay_in_backlog.append(entry)
            continue

        # Check if already valid (no change needed)
        if raw_status in VALID_STATUSES and new_status == raw_status and not parking_note:
            stats["already_valid"] += 1
            entry["_new_status"] = raw_status
            entry["_parking_note"] = None
            entry["_changed"] = False
            # Still route correctly
            if new_status == "Accepted":
                to_active.append(entry)
            elif new_status == "Implemented":
                to_archive.append(entry)
            else:
                stay_in_backlog.append(entry)
            continue

        entry["_new_status"] = new_status
        entry["_parking_note"] = parking_note
        entry["_changed"] = True
        stats["changed"] += 1

        if "[DATA-ERROR]" in (parking_note or ""):
            stats["data_errors"] += 1

        if parking_note and not any(l.startswith("**Parking Note:**") for l in entry["lines"]):
            stats["parking_notes_added"] += 1

        if new_status == "Accepted":
            stats["to_active"] += 1
            to_active.append(entry)
        elif new_status == "Implemented":
            stats["to_archive"] += 1
            to_archive.append(entry)
        else:
            stay_in_backlog.append(entry)

    # --- HARD STOP if unmapped statuses exist ---
    if stats["unmapped"] > 0:
        print("=" * 60)
        print("HARD STOP: Unmapped statuses found. Do NOT apply.")
        print("Add mapping rules for these values:")
        for u in unmapped_list:
            print(u)
        print("=" * 60)
        return 1

    # --- Report ---
    report_lines = [
        f"Migration Report — {TODAY}",
        "=" * 50,
        f"Source:                  {backlog_path}",
        f"Mode:                    {'DRY RUN (no files written)' if dry_run else 'APPLY (files modified)'}",
        "",
        f"Total entries parsed:    {stats['total']}",
        f"Status changes:          {stats['changed']}",
        f"Already valid (no-op):   {stats['already_valid']}",
        f"No Status field:         {stats['no_status']}",
        f"Unmapped statuses:       {stats['unmapped']}",
        "",
        f"Items → IDEAS-ACTIVE.md: {stats['to_active']}",
        f"Items → ARCHIVE.md:      {stats['to_archive']}",
        f"Items stay in backlog:   {len(stay_in_backlog)}",
        f"Parking notes added:     {stats['parking_notes_added']}",
        f"Data errors flagged:     {stats['data_errors']}",
    ]

    if stats["errors"]:
        report_lines.append("")
        report_lines.append("Parse warnings:")
        report_lines.extend(stats["errors"])

    # Show to_active details
    if to_active:
        report_lines.append("")
        report_lines.append(f"Items moving to IDEAS-ACTIVE.md ({len(to_active)}):")
        for e in to_active:
            orig = e["status_value"] or "none"
            new = e["_new_status"]
            note_str = f" [note: {e['_parking_note'][:40]}...]" if e.get("_parking_note") else ""
            report_lines.append(f"  {e['idea_id']}: {orig!r} → {new}{note_str}")

    # Show to_archive details
    if to_archive:
        report_lines.append("")
        report_lines.append(f"Items moving to ARCHIVE.md ({len(to_archive)}):")
        for e in to_archive:
            report_lines.append(f"  {e['idea_id']}: {e['status_value']!r} → Implemented")

    report_text = "\n".join(report_lines)
    print(report_text)
    print()

    if dry_run:
        print("DRY RUN complete. No files written. Re-run with --apply to execute.")
        # Write report file even in dry-run for review
        REPORT_FILE.write_text(report_text + "\n", encoding="utf-8")
        print(f"Report saved to: {REPORT_FILE}")
        return 0

    # ── APPLY mode: write files ───────────────────────────────────────────────

    # Backup originals
    for path in [backlog_path, IDEAS_ACTIVE, ARCHIVE_MD]:
        if path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            print(f"Backup: {path.name} → {bak.name}")

    # Rebuild IDEAS-BACKLOG.md (stay_in_backlog entries, with updated statuses)
    new_backlog_parts = list(preamble_lines)
    for entry in stay_in_backlog:
        new_lines = reconstruct_entry(entry, entry["_new_status"], entry.get("_parking_note"))
        new_backlog_parts.extend(new_lines)

    new_backlog_text = "".join(new_backlog_parts)
    backlog_path.write_text(new_backlog_text, encoding="utf-8")
    print(f"Written: {backlog_path.name} ({len(stay_in_backlog)} entries)")

    # Rebuild IDEAS-ACTIVE.md — append entries to Queue section
    active_text = IDEAS_ACTIVE.read_text(encoding="utf-8")
    # Insert entries before the "## Completed" section
    insert_marker = "## Completed (move to ARCHIVE.md after outcome review)"
    queue_entries = []
    for rank, entry in enumerate(to_active, 1):
        queue_entries.append(format_active_entry(entry, rank))

    queue_block = "".join(queue_entries)
    if insert_marker in active_text:
        active_text = active_text.replace(insert_marker, queue_block + insert_marker)
    else:
        active_text += "\n" + queue_block

    # Update count
    active_text = re.sub(
        r"\*\*Active count:\*\* \d+ / 15",
        f"**Active count:** {len(to_active)} / 15",
        active_text,
    )
    IDEAS_ACTIVE.write_text(active_text, encoding="utf-8")
    print(f"Written: {IDEAS_ACTIVE.name} ({len(to_active)} entries)")

    # Rebuild ARCHIVE.md — append implemented entries to §Implemented
    archive_text = ARCHIVE_MD.read_text(encoding="utf-8")
    impl_marker = "<!-- Populated by migrate-idea-statuses.py on"
    impl_entries = []
    for entry in to_archive:
        impl_entries.append(format_archive_implemented(entry))

    impl_block = "".join(impl_entries)
    if impl_marker in archive_text:
        archive_text = archive_text.replace(impl_marker, impl_block + impl_marker)
    else:
        archive_text += "\n" + impl_block

    # Update summary count
    archive_text = re.sub(
        r"\| Implemented \| \d+ \|",
        f"| Implemented | {len(to_archive)} |",
        archive_text,
    )
    archive_text = re.sub(
        r"\| \*\*Total\*\* \| \*\*\d+\*\* \|",
        f"| **Total** | **{len(to_archive)}** |",
        archive_text,
    )
    ARCHIVE_MD.write_text(archive_text, encoding="utf-8")
    print(f"Written: {ARCHIVE_MD.name} ({len(to_archive)} entries)")

    # Validate: re-parse output, check for unmapped statuses
    print()
    print("Validation pass...")
    out_text = backlog_path.read_text(encoding="utf-8")
    _, out_entries = parse_entries(out_text)
    invalid = []
    for e in out_entries:
        sv = e["status_value"]
        if sv and sv not in VALID_STATUSES:
            invalid.append(f"  {e['idea_id']}: '{sv}'")

    if invalid:
        print(f"WARN: {len(invalid)} entries with non-pipeline statuses in output:")
        for i in invalid:
            print(i)
    else:
        print(f"OK: All {len(out_entries)} backlog entries have valid pipeline statuses")

    print()
    print("Migration complete.")
    REPORT_FILE.write_text(report_text + "\n", encoding="utf-8")
    print(f"Report saved to: {REPORT_FILE}")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    dry_run = True
    backlog_path = DEFAULT_BACKLOG

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--apply":
            dry_run = False
        elif arg == "--backlog":
            pass  # handled below
        elif sys.argv[sys.argv.index(arg) - 1] == "--backlog":
            backlog_path = pathlib.Path(arg)
        elif arg.endswith(".md"):
            backlog_path = pathlib.Path(arg)

    sys.exit(migrate(backlog_path, dry_run))


if __name__ == "__main__":
    main()
