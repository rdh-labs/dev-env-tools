#!/usr/bin/env python3
"""Assert enforcement-registration-cron.sh DELEGATES to notify_ledger and fails loud without it.

WHY THIS EXISTS. That cron once MIRRORED the canonical classifier inline, to avoid a cross-repo
runtime dependency in an unattended job. The mirror copied only the branch ORDER and omitted
truthiness normalisation -- the entire reason the shared module exists -- so it disagreed with
canonical on 6 of 12 shapes ({"success": 1} -> unknown vs delivered) and raised AttributeError
on a non-dict row. It shipped under a comment reading "Keep the two in step": a behavioural norm
guarding a machine invariant, the weakest enforcement class this workspace has.

The mirror is now a guarded import. Two independent reviewers converged on the reason: an
ImportError is LOUD and unambiguous, while a drifted mirror produces a wrong answer that looks
correct. The "no cross-repo dependency" principle was already lost anyway -- governance-health-
cron.sh, same directory, same class of job, takes the dependency and guards it.

So this file no longer checks parity between two implementations; there is only one. It checks
the three properties that replaced that guarantee. Note it would have been WORSE than useless
left as a shape comparison: with the mirror gone it would have run the canonical classifier
against itself and reported agreement -- a tautology wearing a green tick.

Usage:  python3 notify_ledger_parity.py [path-to-cron-script]
        MIRROR_UNDER_TEST=<path>  overrides the subject (used by the mutation controls).
Exit 0 = delegating correctly. Exit 1 = a checked property failed (printed). Exit 2 = the
subject could not be read, a FAILURE state per DEC-326 -- a check that cannot find its subject
has verified nothing and must never report a pass.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify_ledger import classify_row  # noqa: E402

DEFAULT_MIRROR = Path.home() / "bin" / "enforcement-registration-cron.sh"

# The verdict word each classification prints, mapped back to the canonical state name.
VERDICT_TO_STATE = {
    "OK": "delivered",
    "FAILED": "failed",
    "WITHHELD_ONLY": "withheld",
    "DRY_RUN_ONLY": "rehearsed",
    "UNKNOWN": "unknown",
}

# Shapes chosen to exercise NORMALISATION, not just branch order — order was never the part
# that differed. Every non-bool `success` here was a live divergence in the first mirror.
SHAPES = [
    {"success": True},
    {"success": False},
    {"success": "False"},          # one real row on the live ledger carries this
    {"success": "true"},
    {"success": 1},
    {"success": 0},
    {"success": "yes"},
    {"success": "no"},
    {"success": "T"},
    {"success": 1.0},
    {"success": "1"},
    {"success": "maybe"},          # unrecognisable -> unknown, never folded into a neighbour
    {"success": None},
    {"success": True, "suppressed": True},
    {"success": False, "suppressed": True},
    {"success": True, "dry_run": True},
    {"success": True, "dry_run": True, "suppressed": True},
    {},
]


def extract_mirror(path):
    """Pull the embedded python classification block out of the bash heredoc."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    start = end = None
    for i, line in enumerate(lines):
        if start is None and line.rstrip().endswith("<<'PYEOF'"):
            start = i + 1
        elif start is not None and line.strip() == "PYEOF":
            end = i
            break
    if start is None or end is None:
        return None, f"no PYEOF heredoc found in {path}"
    return "\n".join(lines[start:end]), None


def main():
    """Assert the cron DELEGATES to the canonical classifier and FAILS LOUD without it.

    THE SUBJECT CHANGED, SO THE ASSERTION CHANGED. This script used to compare a hand-mirrored
    classifier in the cron against the canonical one over 18 shapes. That mirror is gone --
    replaced by a guarded import after two independent reviewers converged on the failure-mode
    argument (ImportError is loud; a drifted mirror is a wrong answer that looks correct).
    Left as-is, the shape comparison would now be running the canonical classifier against
    ITSELF and reporting agreement: a tautology wearing a green tick, which is the defect class
    this whole change set exists to remove. So it now checks the two properties that are
    actually load-bearing after the change.
    """
    path = os.environ.get("MIRROR_UNDER_TEST") or (
        sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MIRROR)
    block, err = extract_mirror(path)
    if err:
        print(f"UNKNOWN: {err} — delegation NOT verified")
        return 2

    problems = []
    # (1) It must IMPORT, not reimplement. A returning inline classifier is the drift risk.
    if "from notify_ledger import classify_row" not in block:
        problems.append("does not import notify_ledger.classify_row — a mirror may have returned")
    for smell in ("_truthy", 'str(r.get("success")).strip().lower()'):
        if smell in block:
            problems.append(f"contains inline classification logic ({smell!r})")

    # (2) Unavailability must ESCALATE, not skip. A cron that silently skips on a missing
    # dependency is the silent failure its own NO SILENT FAILURES header forbids.
    with tempfile.TemporaryDirectory() as tmp:
        prog = Path(tmp) / "cron.py"
        prog.write_text(block)
        ledger = Path(tmp) / "row.jsonl"
        ledger.write_text(json.dumps({"timestamp": "t", "success": True}) + "\n")
        env = dict(os.environ, HOME="/nonexistent-for-parity-probe")
        out = subprocess.run([sys.executable, str(prog), str(ledger), "0"],
                             capture_output=True, text=True, timeout=30, env=env)
        verdict = (out.stdout or "").split(":")[0].strip()
        if verdict != "UNKNOWN" or out.returncode != 2:
            problems.append(f"missing classifier yields {verdict!r}/rc={out.returncode}, "
                            f"want 'UNKNOWN'/rc=2 (DEC-326: cannot-assess is a failure state)")

        # (3) With the classifier available, every shape must still classify identically.
        for shape in SHAPES:
            ledger.write_text(json.dumps({**shape, "timestamp": "t"}) + "\n")
            r = subprocess.run([sys.executable, str(prog), str(ledger), "0"],
                               capture_output=True, text=True, timeout=30)
            got = VERDICT_TO_STATE.get((r.stdout or "").split(":")[0].strip())
            want = classify_row(shape)
            if got != want:
                problems.append(f"{json.dumps(shape)}: cron={got} canonical={want}")

    if problems:
        print(f"DIVERGENCE: {len(problems)} problem(s)")
        for p_ in problems:
            print(f"  {p_}")
        return 1
    print(f"DELEGATION: cron imports the canonical classifier, escalates when it is "
          f"unavailable, and agrees on {len(SHAPES)}/{len(SHAPES)} shapes")
    return 0


if __name__ == "__main__":
    # This guard was deleted by an edit that replaced everything from `def main():` to EOF.
    # The script then ran, executed nothing, and exited 0 — so every control "passed" while
    # verifying nothing at all. A no-op reporting success, in the file built to catch exactly
    # that. Caught only because the controls were run and BOTH failed to fire.
    raise SystemExit(main())
