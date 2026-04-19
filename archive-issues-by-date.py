#!/usr/bin/env python3
"""
Date-Based ISSUES-TRACKER.md Archival Script
Archives OPEN issues from ISSUES-TRACKER.md older than a date cutoff.
Context: ISSUE-3232, Dart task RZ3wfP7n1usu.

Usage:
  python3 archive-issues-by-date.py [--dry-run] [--cutoff YYYY-MM-DD]

Default cutoff: 2026-04-01
"""

import fcntl
import importlib.util
import os
import re
import shutil
import sys
import argparse
import tempfile
from pathlib import Path
from datetime import datetime, date

GOV_DIR = Path.home() / "dev/infrastructure/dev-env-docs"
ARCHIVE_DIR = GOV_DIR / "archive/governance"
BACKUP_DIR = ARCHIVE_DIR / "pre-archive-backup"
TODAY = datetime.now().strftime("%Y-%m-%d")
TS = datetime.now().strftime("%Y%m%d-%H%M%S")
DEFAULT_CUTOFF = date(2026, 4, 1)


def _load_split_into_blocks():
    src = GOV_DIR / "scripts/archive-terminal-governance.py"
    spec = importlib.util.spec_from_file_location("archive_terminal_governance", src)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"Cannot load split_into_blocks: {src} not found or unreadable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.split_into_blocks


def parse_entry_date(header: str) -> "date | None":
    parts = header.split(" | ")
    if len(parts) < 2:
        return None
    try:
        return datetime.strptime(parts[1].strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def archive_issues_by_date(cutoff: date, dry_run: bool) -> dict:
    split_into_blocks = _load_split_into_blocks()

    filepath = GOV_DIR / "ISSUES-TRACKER.md"
    archive_path = ARCHIVE_DIR / "ISSUES-TRACKER-archive.md"
    header_pat = re.compile(r"^### ISSUE-", re.MULTILINE)

    if dry_run:
        content = filepath.read_text(errors="replace")
        blocks = split_into_blocks(content, header_pat)
        f = None
    else:
        f = open(filepath, "r+b")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        content = f.read().decode("utf-8", errors="replace")
        blocks = split_into_blocks(content, header_pat)

    to_archive = []
    to_keep = []

    for block in blocks:
        if block["type"] == "meta":
            to_keep.append(block)
            continue
        entry_date = parse_entry_date(block["header"])
        if entry_date is not None and entry_date < cutoff:
            if block.get("preamble", "").strip():
                to_keep.append({"type": "meta", "content": block["preamble"], "preamble": ""})
                block = dict(block, preamble="")
            to_archive.append(block)
        else:
            to_keep.append(block)

    source_size = len(content.encode("utf-8", errors="replace"))
    archive_size = sum(len(b["content"].encode()) for b in to_archive)
    keep_size = sum(len((b.get("preamble", "") + b["content"]).encode()) for b in to_keep)

    print(f"  ISSUES date-based archival (cutoff={cutoff}):")
    print(f"    Source: {source_size / 1024:.1f}KB")
    print(f"    To archive: {len(to_archive)} entries ({archive_size / 1024:.1f}KB)")
    print(f"    To keep: {keep_size / 1024:.1f}KB after archival")

    if dry_run:
        print("    [DRY RUN] No changes made.")
        return {"archived": len(to_archive), "archive_kb": archive_size / 1024, "remaining_kb": keep_size / 1024}

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    backup = BACKUP_DIR / f"ISSUES-TRACKER.md.{TS}.bak"
    shutil.copy2(filepath, backup)
    print(f"    Backup: {backup}")

    if to_archive:
        ids = [b["header"].split(" | ")[0].replace("### ", "").strip() for b in to_archive]
        note = f"Archived {TODAY}: {len(to_archive)} entries before {cutoff} ({ids[0]} to {ids[-1]})"
        archive_header = f"\n\n<!-- {note} -->\n\n"
        archive_content = "\n\n---\n\n".join(b["content"] for b in to_archive)
        if archive_path.exists():
            existing = archive_path.read_text(errors="replace")
            archive_path.write_text(existing + archive_header + archive_content, encoding="utf-8")
        else:
            preamble = "# ISSUES-TRACKER Archive\n\nArchived issues (date-based).\n\n"
            archive_path.write_text(preamble + archive_header + archive_content, encoding="utf-8")

    parts = []
    for b in to_keep:
        part = b.get("preamble", "") + b["content"]
        parts.append(part.strip("\n"))
    new_content = "\n\n".join(parts)
    if not new_content.endswith("\n"):
        new_content += "\n"

    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=filepath.parent, delete=False, suffix=".tmp"
    )
    try:
        tmp.write(new_content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, filepath)
    except Exception:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
        raise
    finally:
        if f:
            f.close()

    print(f"    Archived {len(to_archive)} entries. New size: {keep_size / 1024:.1f}KB")
    return {"archived": len(to_archive), "archive_kb": archive_size / 1024, "remaining_kb": keep_size / 1024}


def main():
    parser = argparse.ArgumentParser(description="Archive ISSUES-TRACKER.md entries older than a date cutoff")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--cutoff",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=DEFAULT_CUTOFF,
        metavar="YYYY-MM-DD",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN\n")

    result = archive_issues_by_date(args.cutoff, args.dry_run)
    print(f"\nTotal archived: {result['archived']}")

    if not args.dry_run and result["archived"] > 0:
        generator = Path.home() / "dev/infrastructure/tools/generate-governance-summaries.py"
        if generator.exists():
            import subprocess
            r = subprocess.run(["python3", str(generator)], capture_output=True, text=True)
            if r.returncode == 0:
                print("YAML indexes synced.")
            else:
                print(f"[WARN] YAML sync failed (exit {r.returncode}).")
                if r.stderr:
                    print(f"  {r.stderr[:300]}")


if __name__ == "__main__":
    main()
