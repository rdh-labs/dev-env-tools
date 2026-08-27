#!/usr/bin/env python3
"""Re-measure THE-ELEVEN's stated baselines and report drift.

WHY. ~/dev/share/THE-ELEVEN-2026-08-26.md coordinates a 17-run multi-session sequence and states
measured baselines that its prompts depend on. Those numbers go stale as peers edit the schedule
and the hook tree. The file's own control says "re-count, never cite" -- a RULE standing in for a
MECHANISM. Measured 2026-08-27: 14 commits on that file, at least four of them hand-corrections
of its own stale or false content, one titled "correct a false cross-prompt claim I introduced
one commit ago", across three different sessions. This is that recount, run instead of remembered.

PREDICATE QUALITY BEFORE WIRING (peer dev-c1, measured: a naive unpushed-commits predicate was
60% false-positive) -- "wiring a bad predicate manufactures noise, and that noise is why nobody
wires the next one." So this compares an EXPLICITLY REGISTERED stated value against a
re-measurement of the same quantity. No regex over prose, no inference, near-zero FP by
construction. A claim is either registered here with its measurement or it is not checked -- and
divergence between registry and document is itself reported, so coverage cannot be lost silently.

Markers/exit codes (the runner escalates rc=1|2 unconditionally):
  0 BASELINE-OK · 1 BASELINE-DRIFT · 2 BASELINE-ERROR (never silent)
"""
from __future__ import annotations
import os, re, subprocess, sys, glob, io, contextlib

DOC = os.path.expanduser("~/dev/share/THE-ELEVEN-2026-08-26.md")

def _sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=60).stdout.strip()

def m_sched_lines():  return len(_sh("crontab -l").splitlines())
def m_runner_hits():  return int(_sh("crontab -l | grep -c scheduled-check-runner.sh") or 0)
def m_suites():       return len([q for q in glob.glob(os.path.expanduser("~/bin/tests/*.test.sh"))
                                  + glob.glob(os.path.expanduser("~/bin/tests/*.py"))
                                  if "__pycache__" not in q])
def m_cron_reach():   return int(_sh("crontab -l | grep -oE 'bin/tests/[a-z0-9._-]+' | sort -u | wc -l") or 0)
def m_event_reach():
    # INVOCATION-INDEPENDENT BY CONSTRUCTION. THE-ELEVEN's P-G4 states this baseline as a bare
    # `grep -rl "bin/tests" ~/.claude/hooks/ | wc -l`, which returns a DIFFERENT ANSWER depending
    # on how it is run: this workspace aliases grep to ugrep interactively (which skips compiled
    # bytecode) while a subprocess gets real grep (which does not, and matches the string inside
    # __pycache__/peer_comms_reminder.cpython-312.pyc). Measured 2026-08-27: 0 interactively,
    # 1 from a subprocess. A baseline whose value depends on the caller is not a baseline, and it
    # was the FIRST false positive this tool produced -- caught before publication, which is the
    # only reason this comment is a note and not an incident.
    return int(_sh("grep -rl --binary-files=without-match --exclude-dir=__pycache__ "
                   "bin/tests ~/.claude/hooks/ 2>/dev/null | wc -l") or 0)

CLAIMS = [
    ("schedule lines",           243, m_sched_lines),
    ("runner-caller grep hits",   32, m_runner_hits),
    ("bin/tests suites",          39, m_suites),
    ("suites cron-reachable",     15, m_cron_reach),
    ("suites event-reachable",     0, m_event_reach),
]

def check():
    if not os.path.isfile(DOC):
        print("BASELINE-ERROR: %s not readable -- cannot verify anything" % DOC); return 2
    doc = open(DOC, errors="replace").read()
    drift, notdoc = [], []
    for label, stated, fn in CLAIMS:
        try: actual = fn()
        except Exception as exc:
            print("BASELINE-ERROR: measuring %r raised %r" % (label, exc)); return 2
        if not re.search(r"\b%d\b" % stated, doc): notdoc.append((label, stated))
        if actual != stated: drift.append((label, stated, actual))
    for label, stated, actual in drift:
        print("BASELINE-DRIFT: %s: document states %d, measured %d" % (label, stated, actual))
    for label, stated in notdoc:
        print("BASELINE-DRIFT: %s: registry expects %d but that value no longer appears in the "
              "document -- registry and document have diverged" % (label, stated))
    if drift or notdoc:
        print("BASELINE-DRIFT: %d of %d registered claims no longer hold. Update the document "
              "AND this registry together." % (len(drift)+len(notdoc), len(CLAIMS)))
        return 1
    print("BASELINE-OK: %d/%d registered claims reproduce (%s)"
          % (len(CLAIMS), len(CLAIMS), ", ".join(l for l, _, _ in CLAIMS)))
    return 0

def self_test():
    """Both polarities. A check that cannot be shown failing is not a check."""
    ok = True
    saved = CLAIMS[0]
    CLAIMS[0] = (saved[0], saved[2]() + 999, saved[2])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): rc = check()
    CLAIMS[0] = saved
    if rc != 1 or "BASELINE-DRIFT" not in buf.getvalue():
        print("SELF-TEST FAIL: a wrong stated value did not report drift (rc=%s)" % rc); ok = False
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2): check()
    if "BASELINE-DRIFT: schedule lines" in buf2.getvalue():
        print("SELF-TEST FAIL: correct value still reported as drift"); ok = False
    if not ok: print("SELF-TEST FAILED"); return 2
    print("SELF-TEST PASS: %d registered claims, both polarities (wrong value -> BASELINE-DRIFT "
          "rc=1; correct value -> no drift on that claim)" % len(CLAIMS))
    return 0

if __name__ == "__main__":
    sys.exit(self_test() if "--self-check" in sys.argv else check())
