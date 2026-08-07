#!/usr/bin/env python3
"""Mechanical session-closure inventory — the checks an agent otherwise has to remember.

WHY THIS EXISTS (objectives linkage — read before changing anything)
--------------------------------------------------------------------
Session 231ba059 declared itself complete FIVE times. Each declaration was made in
good faith after a broader inspection than the last, and each missed something:

  close 1  ->  nothing found (later: everything below was already true)
  close 2  ->  handoff carried a superseded next-session prompt
  close 3  ->  five prompts existed only in terminal output, unpersisted
  close 4  ->  a background wait loop had been running for NINE HOURS
  close 5  ->  handoff carried 0 of the session's last 3 artifacts

The structural cause is not carelessness. **Each closure layer validated the layer
BELOW it and asserted its own shape.** repos -> artifacts -> handoff currency ->
background processes -> destination contents. Every round the inspection broadened;
every round the NEW claim went unverified. An agent cannot self-verify the check it
just invented, because inventing it and trusting it are the same act.

So these checks must be MECHANICAL. Two of them were learned in the last hour of
that session and written into a prompt file — i.e. tier-B prose that runs only if a
human pastes it, in a session whose central measured finding is that documentation
does not transfer (204 issuances of one instruction at 3.0% compliance).

OBJECTIVES SERVED (~/dev/share/OBJECTIVES-EXTRACTION-2026-04-03.md):
  * "more like doing the work, less like supervising the help" — the user should not
    have to type "wrap session" five times to surface work an inventory would find.

DESIGN RULES
1. LIVENESS, NOT FILE SHAPE. The obvious orphan predicate ("output file is small and
   old") was tried live and produced TWO false positives — one already-stopped task,
   one that completed normally with a 24-byte result. A predicate that flags the dead
   alongside the running trains dismissal, which is the failure it exists to prevent.
2. FAIL-CLOSED. Verdict is max severity. "I could not determine" is a finding, never
   a pass — an unreadable git repo must not read as a clean one.
3. NO SILENT FAILURES. Self-check asserts the evaluation logic on every run.

Exit: 0 clean, 1 warnings, 2 findings, 3 self-check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
SEV = {"PASS": 0, "WARN": 1, "FAIL": 2}
SEV_NAME = {0: "PASS", 1: "WARN", 2: "FAIL"}

REPOS = [
    HOME / "dev/infrastructure/tools",
    HOME / "bin",
    HOME / "dev/infrastructure/dev-env-config",
    HOME / "dev/infrastructure/dev-env-docs",
    HOME / "dev/share",
]

# Loop shapes an agent writes when waiting on background work.
LOOP_RE = re.compile(r"until \[|while \[|sleep \d+; *done|pgrep -f")


@dataclass
class Finding:
    severity: str
    code: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if not self.findings:
            return "PASS"
        return SEV_NAME[max(SEV[f.severity] for f in self.findings)]

    def add(self, severity: str, code: str, detail: str) -> None:
        if severity not in SEV:
            self.findings.append(Finding("FAIL", "bad_severity", f"unknown severity {severity!r}"))
            severity = "FAIL"
        self.findings.append(Finding(severity, code, detail))


def check_started_not_stopped(rep: Report) -> None:
    """Limb 1: what did this session START that never STOPPED?

    Tests LIVENESS. An artifact-centric check is blind to this by construction — a
    task that never finishes produces no artifact, so 'what did I produce' cannot
    see it. Session 231ba059 lost nine hours to exactly that blind spot.
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid,etimes,args", "--no-headers"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        rep.add("FAIL", "ps_unavailable",
                f"cannot enumerate processes ({e}) — orphan state is UNKNOWN, not clean")
        return

    live = []
    for line in out.splitlines():
        if not LOOP_RE.search(line):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, etimes, args = parts[0], parts[1], parts[2]
        if "session-closure-check" in args or "ps -eo" in args:
            continue          # never flag this checker's own invocation
        try:
            age = int(etimes)
        except ValueError:
            continue
        live.append((pid, age, args[:90]))

    for pid, age, args in live:
        sev = "FAIL" if age > 600 else "WARN"
        rep.add(sev, "orphan_loop",
                f"pid {pid} alive {age}s: {args}  — stop it with TaskStop before closing")
    if not live:
        rep.add("PASS", "no_orphans", "no live wait-loops")


def check_repos(rep: Report) -> None:
    """Limb 2: unpushed commits. Reports authorship so a peer's commit is not misread."""
    for repo in REPOS:
        if not (repo / ".git").exists():
            rep.add("WARN", "not_a_repo", f"{repo} has no .git — skipped")
            continue
        try:
            r = subprocess.run(["git", "log", "origin/main..HEAD", "--format=%h|%an|%s"],
                               cwd=repo, capture_output=True, text=True, timeout=30)
        except Exception as e:
            rep.add("FAIL", "git_unreadable",
                    f"{repo.name}: {e} — push state UNKNOWN, not clean")
            continue
        if r.returncode != 0:
            rep.add("WARN", "no_upstream", f"{repo.name}: no origin/main comparison available")
            continue
        rows = [l for l in r.stdout.strip().splitlines() if l]
        for row in rows:
            sha, author, subj = (row.split("|", 2) + ["", ""])[:3]
            rep.add("WARN", "unpushed",
                    f"{repo.name}: {sha} by {author} — {subj[:60]}  "
                    f"(check authorship: a peer's commit is theirs to push)")


def check_handoff_currency(rep: Report) -> None:
    """Limb 3: does the handoff CONTAIN this session's recent work?

    handoff_validation.py checks the file exists and has its sections. It cannot tell
    a current handoff from one superseded hours earlier — presence, not currency.
    This asks the state question: are the session's newest commits named in it?
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    hits = sorted((HOME / ".claude/handoffs").glob(f"handoff-{sid}-*.md")) if sid else []
    if not hits:
        rep.add("WARN", "no_handoff", "no handoff for this session id — expected if none was run")
        return
    text = hits[-1].read_text(errors="replace")

    recent: list[str] = []
    for repo in REPOS:
        if not (repo / ".git").exists():
            continue
        try:
            r = subprocess.run(["git", "log", "-3", "--format=%h", "--since=8 hours ago"],
                               cwd=repo, capture_output=True, text=True, timeout=30)
            recent += [s for s in r.stdout.split() if s]
        except Exception:
            continue

    missing = [s for s in recent if s not in text]
    if missing:
        rep.add("WARN", "handoff_stale",
                f"{hits[-1].name} does not mention {len(missing)} of {len(recent)} recent "
                f"commits: {' '.join(missing[:6])} — presence is not currency")
    elif recent:
        rep.add("PASS", "handoff_current", f"handoff names all {len(recent)} recent commits")


def self_check() -> list[str]:
    errs = []
    if Report().verdict != "PASS":
        errs.append("empty report must be PASS")
    r = Report(); r.add("WARN", "t", "t")
    if r.verdict != "WARN":
        errs.append("WARN must yield WARN")
    r.add("FAIL", "t", "t")
    if r.verdict != "FAIL":
        errs.append("FAIL must dominate WARN")
    r2 = Report(); r2.add("NOPE", "t", "t")
    if r2.verdict != "FAIL":
        errs.append("REGRESSION: unknown severity must escalate to FAIL")
    # The predicate that produced two false positives must not come back.
    if not LOOP_RE.search("until [ -f x ]; do sleep 5; done"):
        errs.append("LOOP_RE failed to match a real wait loop")
    if LOOP_RE.search("echo done"):
        errs.append("LOOP_RE matched a non-loop")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    errs = self_check()
    if errs:
        for e in errs:
            print(f"[SELF-CHECK FAILED] {e}", file=sys.stderr)
        return 3
    if args.self_check:
        print("self-check: all assertions pass")
        return 0

    rep = Report()
    check_started_not_stopped(rep)
    check_repos(rep)
    check_handoff_currency(rep)

    if args.json:
        print(json.dumps({"verdict": rep.verdict,
                          "findings": [f.__dict__ for f in rep.findings]}, indent=2))
    else:
        print(f"SESSION CLOSURE CHECK: {rep.verdict}")
        for f in rep.findings:
            print(f"  [{f.severity}/{f.code}] {f.detail}")

    return {"PASS": 0, "WARN": 1, "FAIL": 2}[rep.verdict]


if __name__ == "__main__":
    sys.exit(main())
