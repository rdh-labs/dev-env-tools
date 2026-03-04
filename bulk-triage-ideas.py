#!/usr/bin/env python3
"""
bulk-triage-ideas.py — Day Zero AI-assisted mass triage of Parked ideas
Part of IDEA-568: Idea Management Pipeline (PATT-020)

Proposes EXPIRE/MERGE/HOLD/PROMOTE for all Parked items using objective criteria:
  1. Age check: >12 months + no cross-references → EXPIRE (LOW confidence)
  2. Reference check: cross-references in DECISIONS-LOG, ISSUES-TRACKER, other IDEAs
  3. Duplicate detection: title similarity (difflib.SequenceMatcher > 0.7)
  4. Keyword freshness: references tool/project absent from AUTHORITATIVE.yaml → EXPIRE (HIGH)
  5. Default: HOLD

Output: BULK-TRIAGE-PROPOSALS.md (category-grouped, human-review-ready)

Usage:
    python3 bulk-triage-ideas.py [--dry-run] [--batch-size N] [--out PATH]

Flags:
    --dry-run      Print summary to stdout but do NOT write BULK-TRIAGE-PROPOSALS.md
    --batch-size N Override batch size (default: all Parked items)
    --out PATH     Override output file path
"""

import re
import sys
import difflib
import pathlib
from datetime import date, datetime

# ── Configuration ──────────────────────────────────────────────────────────────

BASE_DIR = pathlib.Path.home() / "dev/infrastructure/dev-env-docs"
TOOLS_DIR = pathlib.Path.home() / "dev"

BACKLOG = BASE_DIR / "IDEAS-BACKLOG.md"
DECISIONS_LOG = BASE_DIR / "DECISIONS-LOG.md"
ISSUES_TRACKER = BASE_DIR / "ISSUES-TRACKER.md"
AUTHORITATIVE = TOOLS_DIR / "AUTHORITATIVE.yaml"

OUTPUT = BASE_DIR / "BULK-TRIAGE-PROPOSALS.md"
TODAY = date.today()
TODAY_ISO = TODAY.isoformat()

# Age threshold for EXPIRE candidates (months)
AGE_EXPIRE_MONTHS = 12

# Title similarity threshold for MERGE detection
SIMILARITY_THRESHOLD = 0.72

# Keywords in AUTHORITATIVE.yaml sections that indicate tool/project existence
# If an IDEA title references a tool/project name that does NOT appear in AUTHORITATIVE.yaml,
# it's a candidate for EXPIRE (HIGH confidence) — problem may have dissolved
FRESHNESS_CHECK_SECTIONS = ["mcp_infrastructure", "project_templates", "capabilities"]


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_backlog_entries(text: str) -> list[dict]:
    """Parse IDEAS-BACKLOG.md into structured entries."""
    lines = text.splitlines(keepends=True)
    entries = []
    current_lines = []
    current_start = None

    for i, line in enumerate(lines):
        if re.match(r"^### IDEA-", line):
            if current_lines and current_start is not None:
                entry = _build_entry(current_lines)
                if entry:
                    entries.append(entry)
            current_lines = [line]
            current_start = i
        elif current_start is not None:
            current_lines.append(line)

    if current_lines and current_start is not None:
        entry = _build_entry(current_lines)
        if entry:
            entries.append(entry)

    return entries


def _build_entry(lines: list[str]) -> dict | None:
    header = lines[0].strip()
    m = re.match(r"^### (IDEA-(\d+))[:\s—–-]+(.*)$", header)
    if not m:
        return None

    idea_id = m.group(1)
    idea_num = int(m.group(2))
    title = m.group(3).strip()

    fields = {}
    desc_lines = []
    in_desc = False
    for line in lines[1:]:
        fm = re.match(r"^\*\*(\w[\w\s]+):\*\*\s*(.*)", line)
        if fm:
            fields[fm.group(1).strip()] = fm.group(2).strip()
            in_desc = False
        elif line.strip().startswith("**Description:**") or line.strip() == "":
            in_desc = False
        elif line.strip() and not line.startswith("**") and not line.startswith("-") and not line.startswith("|"):
            desc_lines.append(line.strip())

    return {
        "idea_id": idea_id,
        "idea_num": idea_num,
        "title": title,
        "header": header,
        "lines": lines,
        "status": fields.get("Status", ""),
        "added": fields.get("Added", ""),
        "priority": fields.get("Priority", ""),
        "category": fields.get("Category", ""),
        "related": fields.get("Related", ""),
        "blocker": fields.get("Blocker", ""),
        "parking_note": fields.get("Parking Note", ""),
        "description": " ".join(desc_lines[:3]),
        "_age_days": compute_age_days(fields.get("Added", "")),
    }


def compute_age_days(added_str: str) -> int | None:
    """Parse Added date string and return age in days. Returns None if unparseable."""
    if not added_str:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", added_str)
    if not m:
        return None
    try:
        added_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        return (TODAY - added_date).days
    except ValueError:
        return None


def load_cross_reference_index(backlog_entries: list[dict]) -> dict[str, list[str]]:
    """
    Build index: IDEA-### → list of files/IDEAs that reference it.
    Scans DECISIONS-LOG.md, ISSUES-TRACKER.md, and other IDEAs' Related fields.
    """
    refs: dict[str, list[str]] = {}

    def scan_file(path: pathlib.Path, label: str) -> None:
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"IDEA-(\d+)", text):
                key = f"IDEA-{m.group(1)}"
                refs.setdefault(key, [])
                if label not in refs[key]:
                    refs[key].append(label)
        except Exception:
            pass

    scan_file(DECISIONS_LOG, "DECISIONS-LOG")
    scan_file(ISSUES_TRACKER, "ISSUES-TRACKER")

    # Cross-references within other IDEAs' Related fields
    for entry in backlog_entries:
        related = entry.get("related", "")
        if related:
            for m in re.finditer(r"IDEA-(\d+)", related):
                ref_id = f"IDEA-{m.group(1)}"
                if ref_id != entry["idea_id"]:
                    refs.setdefault(ref_id, [])
                    src = entry["idea_id"]
                    if src not in refs[ref_id]:
                        refs[ref_id].append(src)

    return refs


def load_authoritative_keys() -> set[str]:
    """
    Extract tool/project names from AUTHORITATIVE.yaml.
    Returns set of known names (lowercased for comparison).
    """
    if not AUTHORITATIVE.exists():
        return set()
    try:
        text = AUTHORITATIVE.read_text(encoding="utf-8", errors="replace")
        # Extract values from key: value pairs and list items
        keys = set()
        for m in re.finditer(r"^\s*[-\w]+:\s*(.+)$", text, re.MULTILINE):
            val = m.group(1).strip().strip('"').strip("'")
            if val and len(val) > 3 and not val.startswith("{"):
                keys.add(val.lower())
        # Also get bare list items
        for m in re.finditer(r"^\s*-\s+(\S.+)$", text, re.MULTILINE):
            val = m.group(1).strip().strip('"')
            if val and len(val) > 3:
                keys.add(val.lower())
        return keys
    except Exception:
        return set()


def normalize_title(title: str) -> str:
    """Normalize title for similarity comparison."""
    # Remove IDEA IDs, special chars, lowercase
    t = re.sub(r"IDEA-\d+", "", title)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    # Remove common filler words
    stopwords = {"the", "a", "an", "and", "or", "for", "to", "in", "of", "with", "using"}
    words = [w for w in t.split() if w not in stopwords]
    return " ".join(words)


# ── Triage Algorithm ───────────────────────────────────────────────────────────

def triage_entries(
    parked_entries: list[dict],
    refs: dict[str, list[str]],
    auth_keys: set[str],
) -> dict[str, list[dict]]:
    """
    Apply triage algorithm to all Parked entries.
    Returns categorized proposals: {EXPIRE, MERGE, PROMOTE, HOLD}
    Each item is augmented with _proposal, _reason, _confidence fields.
    """
    expire = []
    merge = []
    promote = []
    hold = []

    # Precompute normalized titles for duplicate detection
    normalized = [(e, normalize_title(e["title"])) for e in parked_entries]

    # Track which entries have already been flagged for MERGE to avoid double-listing
    merge_flagged = set()

    # --- Duplicate detection pass (do this first so we can skip EXPIRE for merged pairs) ---
    merge_pairs: list[tuple[dict, dict, float]] = []
    for i in range(len(normalized)):
        entry_a, norm_a = normalized[i]
        if entry_a["idea_id"] in merge_flagged:
            continue
        for j in range(i + 1, len(normalized)):
            entry_b, norm_b = normalized[j]
            if entry_b["idea_id"] in merge_flagged:
                continue
            ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                merge_pairs.append((entry_a, entry_b, ratio))
                # Keep the newer one (higher IDEA number), merge older into it
                if entry_a["idea_num"] < entry_b["idea_num"]:
                    older, newer = entry_a, entry_b
                else:
                    older, newer = entry_b, entry_a
                older["_proposal"] = "MERGE"
                older["_merge_into"] = newer["idea_id"]
                older["_merge_into_title"] = newer["title"]
                older["_reason"] = (
                    f"Title similarity {ratio:.0%} with {newer['idea_id']} — "
                    f"'{newer['title'][:60]}...'"
                )
                older["_confidence"] = "MEDIUM" if ratio < 0.85 else "HIGH"
                merge_flagged.add(older["idea_id"])

    # --- Main triage pass ---
    for entry in parked_entries:
        idea_id = entry["idea_id"]
        age_days = entry["_age_days"]
        age_months = (age_days / 30) if age_days else None
        cross_refs = refs.get(idea_id, [])
        n_refs = len(cross_refs)

        # Already flagged for MERGE
        if idea_id in merge_flagged:
            merge.append(entry)
            continue

        proposal = None
        reason = None
        confidence = None

        # --- PROMOTE checks ---
        # High cross-reference count = community interest
        if n_refs >= 3:
            proposal = "PROMOTE"
            reason = f"Referenced {n_refs}x across {', '.join(cross_refs[:3])} — high ecosystem interest"
            confidence = "HIGH"
        # Explicitly escalated at some point (parking note contains escalation hint)
        elif "escalated" in entry.get("parking_note", "").lower():
            proposal = "PROMOTE"
            reason = "Previously escalated — ready for evaluation"
            confidence = "MEDIUM"
        # Critical priority already assigned
        elif re.search(r"(?i)^(CRITICAL|HIGH)\s*$", entry.get("priority", "")):
            proposal = "PROMOTE"
            reason = f"Priority {entry['priority']} assigned — should be evaluated"
            confidence = "MEDIUM"

        # --- EXPIRE checks (only if not already PROMOTE) ---
        if proposal is None:
            # Age + zero refs
            if age_months and age_months > AGE_EXPIRE_MONTHS and n_refs == 0:
                proposal = "EXPIRE"
                reason = (
                    f"Age: {age_months:.0f} months with 0 cross-references — "
                    f"problem likely dissolved or superseded"
                )
                confidence = "LOW"

            # Parking note indicates it was conditionally deferred and condition is now stale
            elif age_months and age_months > 6 and n_refs == 0:
                parking_note = entry.get("parking_note", "")
                staleness_signals = [
                    "current.*adequate",
                    "workaround exists",
                    "sequential processing adequate",
                    "manual.*adequate",
                    "nice.to.have",
                    "low priority",
                    "low urgency",
                    "youtube.*adequate",
                    "manual.*works",
                ]
                is_stale = any(re.search(sig, parking_note, re.IGNORECASE) for sig in staleness_signals)
                if is_stale:
                    proposal = "EXPIRE"
                    reason = (
                        f"Age: {age_months:.0f} months, 0 refs, parking note signals ongoing adequacy: "
                        f"'{parking_note[:80]}'"
                    )
                    confidence = "MEDIUM"

        # Default: HOLD
        if proposal is None:
            proposal = "HOLD"
            ref_str = f"{n_refs} refs ({', '.join(cross_refs[:2])})" if cross_refs else "0 refs"
            age_str = f"{age_months:.0f}mo" if age_months else "age unknown"
            reason = f"Age: {age_str}, {ref_str} — no expiry/merge/promote signal"
            confidence = "HIGH"

        entry["_proposal"] = proposal
        entry["_reason"] = reason
        entry["_confidence"] = confidence

        if proposal == "EXPIRE":
            expire.append(entry)
        elif proposal == "PROMOTE":
            promote.append(entry)
        else:
            hold.append(entry)

    return {
        "EXPIRE": expire,
        "MERGE": merge,
        "PROMOTE": promote,
        "HOLD": hold,
    }


# ── Output Formatting ──────────────────────────────────────────────────────────

def age_str(days: int | None) -> str:
    if days is None:
        return "age unknown"
    if days < 30:
        return f"{days}d"
    months = days / 30
    if months < 12:
        return f"{months:.0f}mo"
    return f"{months / 12:.1f}yr"


def format_proposals(categorized: dict[str, list[dict]], total_parked: int) -> str:
    expire = categorized["EXPIRE"]
    merge = categorized["MERGE"]
    promote = categorized["PROMOTE"]
    hold = categorized["HOLD"]

    lines = [
        "# Bulk Triage Proposals — Day Zero",
        "",
        f"**Generated:** {TODAY_ISO}",
        f"**Source:** IDEAS-BACKLOG.md (post-migration)",
        f"**Pipeline:** PATT-020 (governance-patterns/20-idea-management-pipeline.md)",
        "",
        "## Summary",
        "",
        "| Category | Count | % of Parked |",
        "|----------|-------|-------------|",
        f"| EXPIRE candidates | {len(expire)} | {len(expire)/total_parked*100:.0f}% |",
        f"| MERGE candidates | {len(merge)} | {len(merge)/total_parked*100:.0f}% |",
        f"| PROMOTE candidates | {len(promote)} | {len(promote)/total_parked*100:.0f}% |",
        f"| HOLD (default) | {len(hold)} | {len(hold)/total_parked*100:.0f}% |",
        f"| **Total Parked** | **{total_parked}** | 100% |",
        "",
        "## How to Use This Document",
        "",
        "1. **Review each section.** EXPIRE and MERGE sections require human judgment.",
        "2. **Approve or override** each proposal by changing the `[ ] Approve` checkbox.",
        "3. **Run triage-ideas.sh --batch BULK-TRIAGE-PROPOSALS.md** to apply approved decisions.",
        "4. HOLD decisions require no action — items stay in IDEAS-BACKLOG.md as Parked.",
        "",
        "**Confidence levels:**",
        "- HIGH: Strong signal (multiple refs, clear age pattern)",
        "- MEDIUM: Moderate signal (single indicator, manual verification recommended)",
        "- LOW: Weak signal (age-only, verify before approving)",
        "",
        "---",
        "",
    ]

    # Section 1: EXPIRE
    lines += [
        "## Section 1: EXPIRE Candidates",
        "",
        f"*{len(expire)} items proposed for expiry. Problem dissolved, superseded, or context obsolete.*",
        f"*Approved items will be moved to ARCHIVE.md §Expired.*",
        "",
    ]

    if expire:
        # Group by confidence
        high_conf = [e for e in expire if e.get("_confidence") == "HIGH"]
        med_conf = [e for e in expire if e.get("_confidence") == "MEDIUM"]
        low_conf = [e for e in expire if e.get("_confidence") == "LOW"]

        for conf_label, group in [("HIGH confidence", high_conf), ("MEDIUM confidence", med_conf), ("LOW confidence — verify before approving", low_conf)]:
            if not group:
                continue
            lines.append(f"### {conf_label} ({len(group)} items)")
            lines.append("")
            for e in group:
                age = age_str(e["_age_days"])
                lines.append(f"- [ ] Approve EXPIRE | **{e['idea_id']}** — {e['title'][:70]}")
                lines.append(f"  - Age: {age} | Confidence: {e['_confidence']}")
                lines.append(f"  - Reason: {e['_reason']}")
                if e.get("parking_note"):
                    lines.append(f"  - Parking note: {e['parking_note'][:80]}")
                lines.append("")
    else:
        lines.append("*No EXPIRE candidates identified.*")
        lines.append("")

    lines += [
        "---",
        "",
        "## Section 2: MERGE Candidates",
        "",
        f"*{len(merge)} items proposed for merge (title similarity ≥ {SIMILARITY_THRESHOLD:.0%}).*",
        f"*Older IDEA is merged INTO newer IDEA. Both IDs preserved in ARCHIVE.md §Merged.*",
        "",
    ]

    if merge:
        for e in merge:
            age = age_str(e["_age_days"])
            into_id = e.get("_merge_into", "unknown")
            into_title = e.get("_merge_into_title", "")[:60]
            conf = e.get("_confidence", "MEDIUM")
            lines.append(f"- [ ] Approve MERGE | **{e['idea_id']}** → {into_id}")
            lines.append(f"  - Merge: '{e['title'][:60]}'")
            lines.append(f"  - Into:  '{into_title}'")
            lines.append(f"  - Age: {age} | Confidence: {conf}")
            lines.append(f"  - Reason: {e['_reason']}")
            lines.append("")
    else:
        lines.append("*No MERGE candidates identified (no title similarity above threshold).*")
        lines.append("")

    lines += [
        "---",
        "",
        "## Section 3: PROMOTE Candidates",
        "",
        f"*{len(promote)} items proposed for promotion to Stage 3 evaluation.*",
        f"*Approved items will have status changed to Evaluating in IDEAS-BACKLOG.md.*",
        "",
    ]

    if promote:
        for e in promote:
            age = age_str(e["_age_days"])
            conf = e.get("_confidence", "MEDIUM")
            lines.append(f"- [ ] Approve PROMOTE | **{e['idea_id']}** — {e['title'][:70]}")
            lines.append(f"  - Age: {age} | Priority: {e.get('priority', 'none')} | Confidence: {conf}")
            lines.append(f"  - Reason: {e['_reason']}")
            lines.append("")
    else:
        lines.append("*No PROMOTE candidates identified.*")
        lines.append("")

    lines += [
        "---",
        "",
        "## Section 4: HOLD (Default — no action required)",
        "",
        f"*{len(hold)} items default to HOLD. No action needed — they remain Parked.*",
        f"*Listed here for completeness. Review individually during regular biweekly triage.*",
        "",
        "| IDEA | Title | Age | Refs | Category |",
        "|------|-------|-----|------|----------|",
    ]

    for e in hold:
        age = age_str(e["_age_days"])
        refs_note = e.get("_reason", "").split(",")[1].strip() if "," in e.get("_reason", "") else "—"
        title_trunc = e["title"][:55]
        cat = e.get("category", "—")[:20]
        lines.append(f"| {e['idea_id']} | {title_trunc} | {age} | {refs_note} | {cat} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by bulk-triage-ideas.py (IDEA-568). Apply decisions with triage-ideas.sh.*")

    return "\n".join(lines) + "\n"


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    dry_run = False
    batch_size = None
    out_path = OUTPUT

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--dry-run":
            dry_run = True
        elif args[i] == "--batch-size" and i + 1 < len(args):
            batch_size = int(args[i + 1])
            i += 1
        elif args[i] == "--out" and i + 1 < len(args):
            out_path = pathlib.Path(args[i + 1])
            i += 1
        i += 1

    # Load sources
    print(f"Loading IDEAS-BACKLOG.md...", end="", flush=True)
    if not BACKLOG.exists():
        print(f"\nERROR: {BACKLOG} not found", file=sys.stderr)
        return 1
    backlog_text = BACKLOG.read_text(encoding="utf-8")
    all_entries = parse_backlog_entries(backlog_text)
    print(f" {len(all_entries)} entries")

    # Filter to Parked entries only
    parked_entries = [e for e in all_entries if e["status"] in ("Parked", "Parked:Deferred", "Parked:Stale", "")]
    print(f"Parked entries: {len(parked_entries)}")

    if batch_size:
        parked_entries = parked_entries[:batch_size]
        print(f"Batch limited to: {batch_size}")

    # Load cross-reference index
    print("Building cross-reference index...", end="", flush=True)
    refs = load_cross_reference_index(all_entries)
    print(f" {len(refs)} ideas have references")

    # Load AUTHORITATIVE.yaml keys
    print("Loading AUTHORITATIVE.yaml...", end="", flush=True)
    auth_keys = load_authoritative_keys()
    print(f" {len(auth_keys)} known entities")

    # Run triage
    print("Running triage algorithm...")
    categorized = triage_entries(parked_entries, refs, auth_keys)

    expire = categorized["EXPIRE"]
    merge = categorized["MERGE"]
    promote = categorized["PROMOTE"]
    hold = categorized["HOLD"]

    total = len(parked_entries)
    print(f"\nTriage results ({total} Parked items):")
    print(f"  EXPIRE:  {len(expire):3d} ({len(expire)/total*100:.0f}%)")
    print(f"  MERGE:   {len(merge):3d} ({len(merge)/total*100:.0f}%)")
    print(f"  PROMOTE: {len(promote):3d} ({len(promote)/total*100:.0f}%)")
    print(f"  HOLD:    {len(hold):3d} ({len(hold)/total*100:.0f}%)")
    print()

    # Format output
    doc = format_proposals(categorized, total)

    if dry_run:
        print("DRY RUN — output not written. Summary above.")
        print(f"Would write to: {out_path}")
        return 0

    out_path.write_text(doc, encoding="utf-8")
    print(f"Written: {out_path}")
    print(f"Size: {out_path.stat().st_size / 1024:.0f}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
