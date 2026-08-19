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


def has_trigger(cell: str) -> bool:
    """Does anything make this remediation RUN without someone remembering?

    Session-end critique 2026-08-11: BUILT previously meant "a commit SHA that resolves",
    which proves a FILE WAS COMMITTED, not that a MECHANISM RUNS. `crontab -l` showed 5 of 6
    tools built that day had zero triggers and zero references outside tools/ — all five
    graded BUILT. The instrument for measuring form-over-substance was committing
    form-over-substance. A committed file with no trigger is SHIPPED, not BUILT.
    """
    import subprocess
    names = re.findall(r"[\w.-]+\.(?:py|sh|mjs|js)", cell)
    if not names:
        return False
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        cron = ""
    settings = ""
    sp = Path.home() / ".claude" / "settings.json"
    if sp.exists():
        try: settings = sp.read_text(errors="replace")
        except OSError: pass
    return any(n in cron or n in settings for n in names)


def classify(cell: str, repo: Path | None) -> str:
    """One register row's remediation cell -> BUILT / SHIPPED / TESTED / WAIVED / PROSE.

    Order matters: a cell claiming BOTH a SHA and 'none' is BUILT, because the artifact
    exists regardless of how the prose hedges. A SHA that does not RESOLVE is not BUILT --
    that is the difference between citing a commit and having made one.
    """
    if WAIVED_RE.search(cell):
        return "WAIVED"
    for sha in SHA_RE.findall(cell):
        if repo is None or sha_resolves(sha, repo):
            # A SHA proves a file exists. A TRIGGER proves it runs. They are different claims
            # and conflating them is exactly the defect this tool exists to measure.
            return "BUILT" if has_trigger(cell) else "SHIPPED"
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


def parse_jsonl(text: str, repo: Path | None) -> list[tuple[str, str]]:
    """JSONL ledger records -> (instance, classification).

    WHY THIS EXISTS. Until 2026-08-19 this tool could read only markdown tables, so
    ~/dev/share/anomaly-instances.jsonl — the estate's machine-readable anomaly ledger,
    90+ records across three sessions — was UNREADABLE by the one component able to
    resolve a closure and grade it. Measured: running this tool against the ledger's
    markdown mirror returned total=0, because the mirror emits headings and paragraphs
    and `parse()` takes only `|`-rows whose first cell is a digit.

    The consequence was the deepest finding in that ledger's own register: the `built`
    count was a number NOTHING OUTSIDE ITS WRITER COULD CONTRADICT. Both the writer and
    every reader trusted the same field, so agreement between them was a mirror, not a
    check — the exact defect ("a ledger whose authority is its FORMAT") that the ledger
    was built to replace, reproduced one layer up.

    This gives it an external falsifier. The grading is deliberately the SAME as for
    tables: a sha that resolves AND names a triggered artifact is BUILT; a sha that
    resolves without a trigger is SHIPPED. The ledger's own `closure` field is NOT
    trusted — reading it back would be the mirror again.

    DO NOT QUOTE THIS PATH'S HEADLINE NUMBER. Measured on first run, 91 records:
    BUILT=0 SHIPPED=4 TESTED=5 WAIVED=12 PROSE=70, 18.7%. Both figures below are
    MATCHER ARTIFACTS, not properties of the ledger, and each was characterised
    before the number was allowed near a report:

      1. BUILT=0 is guaranteed by construction. has_trigger() searches the CELL for
         a .py/.sh filename, and a ledger pointer is a SHA. Only 13 of 91 cells
         contain a filename at all, so 78 records can never reach BUILT however
         well they are remediated.
      2. sha_resolves() takes ONE repo, while ledger pointers are repo-QUALIFIED
         across four (bin:, share:, tools:, dev-env-config:). A bin: sha checked
         against ~/dev/share does not resolve and grades PROSE. That is why 52 of
         the 61 pointer-carrying records landed in PROSE.

    Genuinely closed by wiring this: the ledger now HAS an external reader, where
    before it had none and its `built` count was uncontradictable by anything.
    NOT closed: this reader cannot yet grade it correctly. The fix for both is to
    resolve repo:sha per-repo and derive the artifact from the COMMIT'S CHANGED
    FILES rather than from cell text — the same correction the session register
    applied to its own grader in PART 9a.

    A wired reader emitting a confident wrong number is worse than no reader: it
    looks like the falsifier that was missing.
    """
    import json
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue                      # same rule as a malformed table row: skip, do not crash
        if not isinstance(rec, dict) or not rec.get("what"):
            continue
        # `closure_evidence` is the pointer; `closure` is the CLAIM. Grade the pointer.
        cell = f"{rec.get('closure_evidence') or ''} {rec.get('evidence') or ''}"
        if rec.get("closure") == "waived":
            # NOT "WAIVED". A waiver read verbatim from the record's own field is the ledger
            # grading itself, and `report()` counts WAIVED toward `converted` — so 12 of 15
            # "converted" records were self-asserted, i.e. 80% of the published rate was the
            # mirror this docstring claims to have removed. Found by an adversarial ship review
            # that reduced it to one line: {"what":"x","closure":"waived"} -> pct=100.0.
            # A waiver is a CLAIM. Only a human-auditable one counts, and this tool cannot
            # audit it, so it is reported in its own bucket and excluded from `converted`.
            out.append((rec["what"][:80], "SELF-ASSERTED"))
        elif rec.get("closure") == "open" or not cell.strip():
            out.append((rec["what"][:80], "PROSE"))
        else:
            out.append((rec["what"][:80], grade_pointer(rec.get("closure_evidence") or "")))
    return out


# ── repo-qualified pointer grading ────────────────────────────────────────────────────
LEDGER_REPOS = {
    "bin":            Path.home()/"bin",
    "share":          Path.home()/"dev/share",
    "tools":          Path.home()/"dev/infrastructure/tools",
    "dev-env-config": Path.home()/"dev/infrastructure/dev-env-config",
}
HEARTBEAT = Path.home()/".metrics/scheduled-check-heartbeat.jsonl"


def _ran_checks() -> set:
    """Names of scheduled checks that have ACTUALLY EXECUTED at least once.

    THE DISTINCTION THIS EXISTS FOR. An adversarial review of this estate's own
    self-assessment found its headline rate counted artifacts that were SCHEDULED, and
    every one of them had been wired the same day: 4 of 5 had never run. Measuring
    "does it fire" on the day you wire it measures INTENT. The heartbeat file has existed
    all along and no grader read it.
    """
    import json as _j
    out = set()
    try:
        for line in HEARTBEAT.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                out.add(_j.loads(line).get("check"))
            except (ValueError, TypeError):
                continue
    except OSError:
        pass
    return {c for c in out if c}


# Generic wrappers appear on MANY schedule lines and are not the artifact being remediated.
# Measured: scheduled-check-runner.sh is on 14 lines, and _scheduled_as returned the FIRST
# (artifact-sweep), so any commit touching the wrapper graded BUILT against an unrelated check.
_NOT_AN_ARTIFACT = {"scheduled-check-runner.sh", "hook-runner.sh", "notify.sh"}


def _scheduled_as(basename: str, cron: str):
    """The scheduled-check NAME that invokes this artifact, or None.

    BOUNDARY CLASS, NOT \b. `re.search(r'\banomaly-log\b', 'anomaly-log.test.sh')` is TRUE
    because \b matches before a '.', so a word-boundary matcher does NOT separate a tool from
    its own test. That exact substitution put two false positives into a published rate, and
    the remedy shipped for it was itself a \b matcher. The correct class excludes '.' and '-'.
    """
    if basename in _NOT_AN_ARTIFACT:
        return None
    if not re.search(rf"(?<![\w.-]){re.escape(basename)}(?![\w.-])", cron):
        return None
    hits = []
    for line in cron.splitlines():
        if line.lstrip().startswith("#"):
            continue                      # a commented-out entry does not run (CLI review)
        if not re.search(rf"(?<![\w.-]){re.escape(basename)}(?![\w.-])", line):
            continue
        hits.append(line)
    # MULTIPLE LINES IS NOT AMBIGUITY. A first attempt refused on >1 hit, which broke
    # session-artifact-sweep.py — it is scheduled TWICE (--all-sessions at 07:00/19:00 and
    # --archive-gate-ledgers hourly). An artifact scheduled twice is more scheduled, not less.
    # Return every check name; the caller promotes if ANY of them has actually run.
    names = []
    for line in hits:
        m = re.search(r"scheduled-check-runner\.sh\s+(\S+)", line)
        names.append(m.group(1) if m else "__direct__")
    return names or None


def grade_pointer(spec: str) -> str:
    """repo:sha -> BUILT / SHIPPED / PROSE, graded on the COMMIT'S CHANGED FILES.

    Never on the cell text: a ledger pointer is a sha, and has_trigger() searches for a
    FILENAME, so cell-text grading can only ever return SHIPPED for a sha-only pointer.
    """
    repo, _, sha = spec.partition(":")
    d = LEDGER_REPOS.get(repo)
    if not d or not (d/".git").exists() or not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        return "PROSE"
    try:
        r = subprocess.run(["git", "-C", str(d), "show", "--name-only", "--format=", sha],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return "PROSE"
    if r.returncode != 0:
        return "PROSE"
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        cron = ""
    ran = _ran_checks()
    best = "SHIPPED"
    for f in (l.strip() for l in r.stdout.splitlines() if l.strip()):
        # --name-only lists DELETED files too. A commit that removes a scheduled artifact is
        # the opposite of a remediation that fires (CLI review).
        if not (d/f).exists():
            continue
        names = _scheduled_as(Path(f).name, cron)
        if not names:
            continue
        # SCHEDULED is not FIRED. Only a heartbeat row promotes to BUILT. "__direct__" means
        # the schedule invokes the artifact without the runner wrapper, so no heartbeat exists
        # for it and it can never be promoted — SHIPPED is the honest floor, not a bug.
        if any(n in ran for n in names):
            return "BUILT"
        best = "SHIPPED"
    return best


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


def marker_line(counts, total, converted, pct, ok) -> str:
    """The line the SCHEDULED CONSUMER greps. Kept next to report() on purpose.

    scheduled-check-runner.sh matches the regex `BUILT=0|below` against this tool's stdout.
    The human-readable block prints `  BUILT   0` (spaces, no equals) and `BELOW THRESHOLD`
    (uppercase, against a case-sensitive grep -qE), so that regex could never match. From the
    day it was wired, a run reporting 6.1% against a 25% threshold produced STATUS=ok and sent
    no notification. The check was silent by construction exactly when it had something to say
    -- the defect the runner's own header says it exists to prevent.

    Found by an adversarial verifier that RAN the pair. Reading either file alone shows
    nothing wrong; the mismatch lives between them.

    A marker the consumer cannot match is not a lesser problem than no marker. It is the same
    false all-clear, with an audit trail that reads as healthy.

    Emitting a matchable line here fixes delivery WITHOUT a scheduler edit, which is
    gate-blocked to agents (SCHEDULE_PERSIST, denied by default).
    """
    return ("CONVERSION-MARKER: "
            + " ".join(f"{k}={counts[k]}" for k in ("BUILT", "SHIPPED", "TESTED", "WAIVED", "PROSE"))
            + f" total={total} converted={converted} pct={pct:.1f}"
            + (" at-or-above threshold" if ok else " below threshold"))


def report(rows, threshold=THRESHOLD_PCT):
    counts = {k: 0 for k in ("BUILT", "SHIPPED", "TESTED", "WAIVED", "PROSE", "SELF-ASSERTED")}
    for _, k in rows:
        counts[k] = counts.get(k, 0) + 1
    total = len(rows)
    # SHIPPED is NOT converted. A file nobody runs remediates nothing.
    converted = counts["BUILT"] + counts["TESTED"] + counts["WAIVED"]
    pct = (100.0 * converted / total) if total else 0.0
    return counts, total, converted, pct, pct >= threshold


def self_check() -> int:
    """Fixtures with known answers. A measurement nobody can falsify is a number, not evidence."""
    ok = []
    # ── JSONL path fixtures (added 2026-08-19 after an adversarial ship review) ──────────
    # THE DEFECT THESE CLOSE: stubbing grade_pointer -> "BUILT" moved the headline to 80.2%
    # and BOTH suites stayed green, because none of the 15 fixtures called any new function
    # and the contract suite only exercises the markdown path. That is the file's own ACC-3
    # defect ("gutting parse() to return [] left 8/8 PASSING") reproduced one function over.
    ok.append(("a waived record is SELF-ASSERTED, never WAIVED — the field is a claim",
               parse_jsonl('{"what":"x","closure":"waived"}', None) == [("x", "SELF-ASSERTED")]))
    ok.append(("a self-asserted record does NOT count toward conversion",
               report([("a", "SELF-ASSERTED"), ("b", "SELF-ASSERTED")], 25.0)[3] == 0.0))
    ok.append(("parse_jsonl returning nothing is NOT a pass — a gutted parser must be visible",
               len(parse_jsonl('{"what":"y","closure":"built","closure_evidence":"bin:0000000"}',
                               None)) == 1))
    ok.append(("an open record is PROSE, not converted",
               parse_jsonl('{"what":"z","closure":"open"}', None) == [("z", "PROSE")]))
    ok.append(("a malformed line is skipped, not crashed on",
               parse_jsonl('not json\n{"what":"q","closure":"open"}', None) == [("q", "PROSE")]))
    ok.append(("grade_pointer refuses a mutable ref",
               grade_pointer("bin:HEAD") == "PROSE"))
    ok.append(("grade_pointer refuses an unknown repo qualifier",
               grade_pointer("nosuchrepo:0000000") == "PROSE"))
    ok.append(("grade_pointer refuses an unresolvable sha",
               grade_pointer("bin:deadbee") == "PROSE"))
    _cron_fixture = ("0 7 * * * /x/scheduled-check-runner.sh artifact-sweep /l - -- /y/foo.py\n"
                     "#0 9 * * * /x/scheduled-check-runner.sh dead /l - -- /y/bar.py\n")
    ok.append(("_scheduled_as does not match a tool inside its own test filename",
               _scheduled_as("foo", _cron_fixture) is None))
    ok.append(("_scheduled_as finds a genuinely scheduled artifact",
               _scheduled_as("foo.py", _cron_fixture) == ["artifact-sweep"]))
    ok.append(("_scheduled_as ignores a COMMENTED-OUT schedule line",
               _scheduled_as("bar.py", _cron_fixture) is None))
    ok.append(("_scheduled_as refuses a generic wrapper that appears on many lines",
               _scheduled_as("scheduled-check-runner.sh", _cron_fixture) is None))

    ok.append(("an unresolvable SHA is NOT counted as BUILT",
               classify("fixed in deadbeef1", Path("/nonexistent")) != "BUILT"))
    # The above passed even with sha_resolves BYPASSED, because an untriggered cell falls to
    # SHIPPED either way — the later SHIPPED verdict made the older fixture vacuous. Found by
    # the scheduled mutation canary, not by authorship. This pins the resolution check itself:
    # an unresolvable SHA naming a TRIGGERED file must still not be BUILT.
    ok.append(("an UNRESOLVABLE SHA naming a triggered file is still NOT BUILT",
               classify("fixed in deadbeef1, session-artifact-sweep.py",
                        Path("/nonexistent")) != "BUILT"))
    ok.append(("'none' is PROSE", classify("none", None) == "PROSE"))
    ok.append(("a SHA with NO trigger is SHIPPED, not BUILT — a file nobody runs",
               classify("fixed in 13de4bf, see foo-with-no-trigger.py", None) == "SHIPPED"))
    ok.append(("SHIPPED must NOT count toward conversion",
               report([("a", "SHIPPED"), ("b", "SHIPPED")], 25.0)[3] == 0.0))
    ok.append(("'ACKs only; unbuilt' is PROSE", classify("ACKs only; unbuilt", None) == "PROSE"))
    ok.append(("an explicit waiver is WAIVED, not PROSE",
               classify("no mechanism warranted -- one-off", None) == "WAIVED"))
    ok.append(("a sabotage-proven fixture counts as TESTED",
               classify("sabotage-proven fixture added", None) == "TESTED"))
    ok.append(("a SHA outranks hedging prose in the same cell (SHIPPED: no trigger named)",
               classify("mostly documented, none really, 13de4bf", None) == "SHIPPED"))
    ok.append(("a SHA naming a TRIGGERED file is BUILT",
               classify("fixed in 13de4bf, session-artifact-sweep.py", None) == "BUILT"))
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

    # Dispatch on CONTENT, not filename. An artifact is JSONL if its first non-blank line
    # is an object; extension-sniffing would miss a ledger written to any other suffix.
    first = next((l for l in text.splitlines() if l.strip()), "")
    rows = (parse_jsonl(text, args.repo if args.repo.exists() else None)
            if first.lstrip().startswith("{")
            else parse(text, args.repo if args.repo.exists() else None))
    counts, total, converted, pct, passed = report(rows, args.threshold)

    print(marker_line(counts, total, converted, pct, passed))
    print(f"ANOMALY CONVERSION: {pct:.1f}% ({converted}/{total} instances closed by something "
          f"that EXISTS)")
    for k in ("BUILT", "SHIPPED", "TESTED", "WAIVED", "PROSE"):
        print(f"  {k:7s} {counts[k]}")
    if counts["PROSE"]:
        print(f"\n  {counts['PROSE']} instance(s) closed in PROSE. Prose is the failure mode,")
        print("  not the remedy. Each needs a mechanism, a test, or an explicit waiver.")
    print(f"\n  threshold {args.threshold:.0f}% -> {'PASS' if passed else 'BELOW THRESHOLD'}")
    return 0 if (passed or args.report_only) else 1


if __name__ == "__main__":
    sys.exit(main())
