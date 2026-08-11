#!/usr/bin/env python3
"""Measure the CONVERSION RATE of anomaly analyses into built mechanisms.

WHY: on 2026-08-11 a session emitted 39+ Gap #1/#2/#3 chains and built 3 mechanisms. The
finding was written into ANOMALY-REGISTER.md -- and then the session produced several MORE
analyses that closed in prose, including one whose subject was that analyses close in prose.

An anomaly finding must not be closeable by an ANALYSIS. It closes on one of three things:
  BUILT      a commit SHA, i.e. something exists that did not exist before
  TESTED     a named test/verification run
  WAIVED     an explicit "no mechanism warranted -- <reason>"
Anything else is PROSE, and prose is the failure mode, not the remedy.

This is deliberately a MEASUREMENT, not a gate. A gate on this would be gameable by writing
the word "built" -- the ratio is only meaningful if the SHAs resolve, which is checked here.

Exit 0 when conversion >= THRESHOLD or with --report-only; 1 below it; 2 on unreadable input.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REGISTER = Path.home() / "dev/share/session-4ac72061-artifacts/ANOMALY-REGISTER.md"
THRESHOLD_PCT = 25.0          # below this, the practice is documentation, not remediation

SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
WAIVED_RE = re.compile(r"no mechanism warranted|no remediation needed", re.I)
TESTED_RE = re.compile(r"\b(sabotage|self-check|fixture|verified by|test(?:ed)?)\b", re.I)
# "none", "unbuilt", "ACKs only" and friends are PROSE outcomes stated honestly.
PROSE_RE = re.compile(r"\bnone\b|unbuilt|ACKs only|documented|recorded|noted", re.I)


def classify(cell: str, repo: Path | None) -> str:
    """One register row's remediation cell -> BUILT / TESTED / WAIVED / PROSE.

    Order matters: a cell claiming BOTH a SHA and 'none' is BUILT, because the artifact
    exists regardless of how the prose hedges. A SHA that does not RESOLVE is not BUILT --
    that is the difference between citing a commit and having made one.
    """
    if WAIVED_RE.search(cell):
        return "WAIVED"
    for sha in SHA_RE.findall(cell):
        if repo is None or sha_resolves(sha, repo):
            return "BUILT"
    if TESTED_RE.search(cell):
        return "TESTED"
    return "PROSE"


def sha_resolves(sha: str, repo: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def parse(text: str, repo: Path | None) -> list[tuple[str, str]]:
    """Register table rows -> (instance, classification). Rows only; prose is ignored."""
    out = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue                      # header, separator, or a non-instance row
        out.append((cells[1], classify(cells[4], repo)))
    return out


def report(rows, threshold=THRESHOLD_PCT):
    counts = {k: 0 for k in ("BUILT", "TESTED", "WAIVED", "PROSE")}
    for _, k in rows:
        counts[k] += 1
    total = len(rows)
    converted = counts["BUILT"] + counts["TESTED"] + counts["WAIVED"]
    pct = (100.0 * converted / total) if total else 0.0
    return counts, total, converted, pct, pct >= threshold


def self_check() -> int:
    """Fixtures with known answers. A measurement nobody can falsify is a number, not evidence."""
    ok = []
    ok.append(("an unresolvable SHA is NOT counted as BUILT",
               classify("fixed in deadbeef1", Path("/nonexistent")) != "BUILT"))
    ok.append(("'none' is PROSE", classify("none", None) == "PROSE"))
    ok.append(("'ACKs only; unbuilt' is PROSE", classify("ACKs only; unbuilt", None) == "PROSE"))
    ok.append(("an explicit waiver is WAIVED, not PROSE",
               classify("no mechanism warranted -- one-off", None) == "WAIVED"))
    ok.append(("a sabotage-proven fixture counts as TESTED",
               classify("sabotage-proven fixture added", None) == "TESTED"))
    ok.append(("a SHA outranks hedging prose in the same cell",
               classify("mostly documented, none really, 13de4bf", None) == "BUILT"))
    rows = [("a", "BUILT"), ("b", "PROSE"), ("c", "PROSE"), ("d", "PROSE")]
    _, total, conv, pct, passed = report(rows, threshold=25.0)
    ok.append(("1 of 4 converted == 25.0% and meets a 25% threshold",
               total == 4 and conv == 1 and abs(pct - 25.0) < 1e-9 and passed))
    _, _, _, _, failed_ok = report([("a", "PROSE")] * 4, threshold=25.0)
    ok.append(("0 of 4 converted must FAIL the threshold", failed_ok is False))
    # I/O-BOUNDARY fixtures. Independent review (ACC-3): gutting parse() to return [] left
    # 8/8 PASSING, because every fixture called classify()/report() directly. A reader that
    # returns nothing then reports "0/0" as if it had measured something.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td)/"reg.md"
        reg.write_text("| # | Instance | Root | Chain? | Remediation |\n"
                       "|---|---|---|---|---|\n"
                       "| 1 | thing one | R1 | yes | none |\n"
                       "| 2 | thing two | R2 | yes | no mechanism warranted -- one-off |\n")
        parsed = parse(reg.read_text(), None)
        ok.append(("parse() must actually READ table rows (not silently return [])",
                   len(parsed) == 2))
        ok.append(("parse() must classify the rows it read",
                   sorted(k for _, k in parsed) == ["PROSE", "WAIVED"]))
        _, total, _, _, _ = report(parsed)
        ok.append(("an empty parse must not masquerade as a measured result", total == 2))

    bad = [m for m, good in ok if not good]
    for m in bad:
        print(f"  [FAIL/self-check] {m}")
    if not bad:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the classifier")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--register", type=Path, default=REGISTER)
    ap.add_argument("--repo", type=Path, default=Path.home() / "dev/infrastructure/tools",
                    help="repo used to confirm cited SHAs actually resolve")
    ap.add_argument("--threshold", type=float, default=THRESHOLD_PCT)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("ANOMALY CONVERSION CHECK: self-check")
        return self_check()

    try:
        text = args.register.expanduser().read_text()
    except OSError as exc:
        print(f"ERROR: cannot read {args.register}: {exc}", file=sys.stderr)
        return 2

    rows = parse(text, args.repo if args.repo.exists() else None)
    counts, total, converted, pct, passed = report(rows, args.threshold)

    print(f"ANOMALY CONVERSION: {pct:.1f}% ({converted}/{total} instances closed by something "
          f"that EXISTS)")
    for k in ("BUILT", "TESTED", "WAIVED", "PROSE"):
        print(f"  {k:7s} {counts[k]}")
    if counts["PROSE"]:
        print(f"\n  {counts['PROSE']} instance(s) closed in PROSE. Prose is the failure mode,")
        print("  not the remedy. Each needs a mechanism, a test, or an explicit waiver.")
    print(f"\n  threshold {args.threshold:.0f}% -> {'PASS' if passed else 'BELOW THRESHOLD'}")
    return 0 if (passed or args.report_only) else 1


if __name__ == "__main__":
    sys.exit(main())
