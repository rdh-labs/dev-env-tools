#!/usr/bin/env python3
"""Check a response's closure fields against EACH OTHER. Adds no field; checks the ones there.

WHY. On 2026-08-11 a response listed four unresolved risks and then declared `Open: Nothing`
twelve lines later. Every gate passed: A14 saw a tail, A26 saw no untracked item — because the
risks were never *items*. The format permits disclosing in one field and denying in another.

WHY THIS SHAPE. The agent's stated reason for not fixing it was "the workspace's standard
response is to add a field, and I would be adding the sixth." That reasoning was covert
deferral: it explained why ONE path was counterproductive and never searched for another. This
is the alternative that was never looked for — a consistency check BETWEEN existing fields,
adding none. It is also what `O7t4WAplaNNk`'s directive asks for: not mechanism #119, but
making what exists actually work. Prior art in this workspace says the same: the memory
`sealed-exits-cause-fabrication-prune-dont-add` records that adding exits made things worse.

WHAT IT CANNOT DO, said plainly:
- It is a TEXT check. It catches contradictions between fields, not false content within one.
  `Open: Nothing` with no risks listed passes even if plenty is open. That is the residue.
- It cannot tell a rhetorical question in `You:` from an assigned action.
- It is advisory by construction. A blocking version would be mechanism #119.

Exit 0 = consistent; 1 = contradictions found; 2 = no tail present to check.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NOTHING = re.compile(r"^\s*(nothing|none)\b", re.I)
# Lines that ASSERT something is unresolved. If any fire, `Open: Nothing` contradicts them.
UNRESOLVED = [
    ("Risk:", re.compile(r"^\s*[-*]?\s*Risk:", re.I | re.M)),
    ("Not checked:", re.compile(r"^\s*[-*]?\s*Not checked:", re.I | re.M)),
    ("Limitation:", re.compile(r"^\s*[-*]?\s*Limitation:", re.I | re.M)),
]
# An imperative aimed at the user, i.e. an actual assignment.
IMPERATIVE = re.compile(
    r"\b(run|read|install|decide|confirm|approve|tell me|say the word|paste|check)\b", re.I)


def field(text: str, name: str) -> str | None:
    m = re.search(rf"^\s*\**{re.escape(name)}\**\s*:?\s*(.+)$", text, re.I | re.M)
    return m.group(1).strip() if m else None


def check(text: str) -> tuple[list[str], bool]:
    findings, has_tail = [], False
    open_f, you_f = field(text, "Open"), field(text, "You")
    if open_f is None and you_f is None:
        return findings, has_tail
    has_tail = True

    # C1 — the defect that motivated this: Open denies what Risks asserts.
    if open_f and NOTHING.match(open_f):
        hits = [(label, len(rx.findall(text))) for label, rx in UNRESOLVED
                if rx.search(text)]
        if hits:
            detail = ", ".join(f"{n}x {label}" for label, n in hits)
            findings.append(
                f"CONTRADICTION: 'Open: {open_f[:40]}' while the same response asserts "
                f"unresolved items ({detail}). Disclosing in one field and denying in another.")

    # C2 — 'You: Nothing' followed by an instruction is self-contradicting. Occurred 3x in one
    # session before anyone noticed.
    if you_f and NOTHING.match(you_f) and IMPERATIVE.search(you_f):
        findings.append(
            f"CONTRADICTION: 'You: {you_f[:60]}' says nothing is required AND issues an "
            f"instruction in the same line.")

    # C3 — a You: line that assigns nothing and names no artifact is a reconstruction task.
    if you_f and not NOTHING.match(you_f) and not IMPERATIVE.search(you_f):
        findings.append(
            f"VAGUE: 'You: {you_f[:60]}' names no action the user can take. A You: line must "
            f"assign an action or say plainly that none is required.")
    return findings, has_tail


def self_check() -> int:
    ok = []
    bad_tail = ("## Risks\n- Risk: something might break here for sure\n"
                "- Not checked: whether the thing was verified\n\n"
                "Done: stuff\nOpen: Nothing\nYou: Nothing — complete\n")
    f, _ = check(bad_tail)
    ok.append(("Open: Nothing while Risk: lines exist must be caught",
               any("CONTRADICTION" in x and "Open:" in x for x in f)))
    good = ("## Risks\n- Risk: something might break\n\nDone: stuff\n"
            "Open: the thing is unverified\nYou: run the installer\n")
    f2, _ = check(good)
    ok.append(("a consistent tail must NOT be flagged", not f2))
    f3, _ = check("Done: x\nOpen: Nothing\nYou: Nothing — run the installer\n")
    ok.append(("'You: Nothing' plus an instruction must be caught",
               any("says nothing is required AND issues an instruction" in x for x in f3)))
    f4, _ = check("Done: x\nOpen: Nothing\nYou: the design is interesting\n")
    ok.append(("a You: line assigning no action must be flagged as VAGUE",
               any("VAGUE" in x for x in f4)))
    _, ht = check("no tail at all here")
    ok.append(("absence of a tail is reported, not treated as consistent", ht is False))
    f6, _ = check("Done: x\nOpen: Nothing\nYou: Nothing — complete\n")
    ok.append(("Open: Nothing with NO risk lines is legitimately clean", not f6))
    bad = [m for m, good_ in ok if not good_]
    for m in bad:
        print(f"  [FAIL/self-check] {m}")
    if not bad:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the comparison")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", nargs="?", help="file containing the response text; omit for stdin")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("TAIL CONSISTENCY CHECK: self-check")
        return self_check()

    text = Path(args.file).read_text(errors="replace") if args.file else sys.stdin.read()
    findings, has_tail = check(text)
    if not has_tail:
        print("TAIL CONSISTENCY: no Open:/You: tail found — nothing to check")
        return 2
    if not findings:
        print("TAIL CONSISTENCY: consistent")
        print("  NOTE: this checks fields against EACH OTHER only. A tail with no risks listed")
        print("  passes even when much is open — that residue is not covered and never was.")
        return 0
    print(f"TAIL CONSISTENCY: {len(findings)} contradiction(s)")
    for f in findings:
        print(f"  {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
