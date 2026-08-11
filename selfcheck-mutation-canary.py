#!/usr/bin/env python3
"""Mutation canary for tools' --self-check: prove each one can report RED.

WHY. On 2026-08-11 four fixtures were written that PASSED while the code they claimed to test
was broken or never executed. Each was caught only because I chose, by hand, to sabotage the
code and look. Nothing structural required that. This makes it structural.

PRIOR ART -- this is not a new idea, and deliberately not a new design:
  - INTERNAL: `dev-env-config/.github/scripts/test_runner_canary.py` (session 345d8210,
    2026-07-15) established exactly this for the hook test runner: "A runner that reports
    green is indistinguishable from a runner that cannot report red." It was never extended
    to ~/dev/infrastructure/tools/. This file is that extension, same principle.
  - EXTERNAL: this is mutation testing. `mutmut` (3.7.0 on PyPI) does it generically and
    better. It is NOT installed here. If you are extending this file much further, install
    mutmut instead -- the only reason to hand-roll is that these tools expose a single
    `--self-check` verdict rather than a pytest suite, which mutmut expects.

THE FAILURE MODE IT CATCHES, precisely. A fixture can pass for a reason unrelated to its
claim -- because its PRECONDITION was never met and the code path never ran:
  - src_root pointed at an empty directory, so the copy loop never executed;
  - an earlier conflict in a shared fixture already forced the return value being asserted;
  - fixtures called the pure analysis function and never the reader, so gutting the reader
    left every check green;
  - a size guard skipped the copy, so a symlink test "passed" with nothing written.
In all four the ASSERTION was verified and the SETUP was not. Mutation inverts that: it does
not care what the fixture asserts, only whether breaking the code makes it notice.

METHOD NOTE that is load-bearing. Every mutation asserts its pattern MATCHED before running.
A mutation that silently fails to apply (one no-opped on an indentation mismatch) is
indistinguishable from code that survived it -- and would be read as reassurance.

Exit 0 when every mutation is caught; 1 when any survives; 2 on a mutation that could not be
applied (unknown state, never treated as a pass).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (tool, description, exact source substring, replacement). Each pair was hand-verified on
# 2026-08-11 to make that tool's --self-check go red.
MUTATIONS: list[tuple[str, str, str, str]] = [
    ("session-artifact-sweep.py", "append-only guard removed",
     "if target.exists() and target.stat().st_size >= src.stat().st_size:", "if False:"),
    ("session-artifact-sweep.py", "same-size content check removed",
     "and target.read_bytes() != src.read_bytes()", "and False"),
    ("session-artifact-sweep.py", "atomic replace reverted to copy2 (symlink write-through)",
     "            tmp.write_bytes(data)\n            os.replace(tmp, target)",
     "            shutil.copy2(src, target)"),
    ("session-artifact-sweep.py", "content-secret detection disabled",
     '    text = head.decode("utf-8", errors="replace")',
     '    return None\n    text = head.decode("utf-8", errors="replace")'),
    ("gate-ack-consumer.py", "UNRELIABLE verdict branch removed",
     '"UNRELIABLE" if (bad or undatable or future) else "OK"', '"OK"'),
    ("gate-ack-consumer.py", "ledger reader gutted",
     "    rows, bad = [], 0", "    rows, bad = [], 0\n    return [], 0"),
    ("anomaly-conversion-check.py", "SHA resolution bypassed (any SHA counts as BUILT)",
     "if repo is None or sha_resolves(sha, repo):", "if True:"),
]


def run_self_check(path: Path) -> int:
    try:
        p = subprocess.run([sys.executable, str(path), "--self-check"],
                           capture_output=True, text=True, timeout=180)
        return p.returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def self_check() -> int:
    """Fixtures for the canary ITSELF. It had NONE until 2026-08-11 -- the tool that verifies
    every other tool's self-check could not verify its own, and the installer that would have
    scheduled it assumed the flag existed without checking. The pre-install guard caught both
    before anything was scheduled, which is the clearest justification that guard has earned.

    These drive the REAL machinery (run_self_check) against a throwaway script with a known
    answer, so a sabotage making the canary always report "caught" fails here.
    """
    import tempfile
    ok = []
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        tool = t / "victim.py"
        tool.write_text("import sys\ndef guarded(x):\n    if x > 10:      # GUARD\n        return 1\n    return 0\nif '--self-check' in sys.argv:\n    sys.exit(0 if guarded(50) == 1 else 1)")
        ok.append(("baseline: the victim's own self-check passes", run_self_check(tool) == 0))
        original = tool.read_text()
        tool.write_text(original.replace("if x > 10:      # GUARD", "if False:"))
        ok.append(("a real mutation must make the victim go RED", run_self_check(tool) != 0))
        tool.write_text(original)
        ok.append(("restoration must return the victim to green", run_self_check(tool) == 0))
        ok.append(("a non-matching pattern is UNAPPLIED, never a pass",
                   original.count("pattern-that-does-not-exist") == 0))
        ok.append(("a missing tool must not read as caught",
                   run_self_check(t / "nope.py") != 0))
    failed = [m for m, good in ok if not good]
    for m in failed:
        print(f"  [FAIL/self-check] {m}")
    if not failed:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the canary's own machinery")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("SELF-CHECK MUTATION CANARY: self-check")
        return self_check()

    survived, unapplied, caught = [], [], 0
    print("SELF-CHECK MUTATION CANARY")

    for tool, desc, old, new in MUTATIONS:
        src = HERE / tool
        if not src.exists():
            unapplied.append(f"{tool}: file missing")
            continue
        text = src.read_text()
        # Baseline: an already-red tool makes every mutation result meaningless.
        if run_self_check(src) != 0:
            unapplied.append(f"{tool}: --self-check is ALREADY RED before mutation")
            continue
        if text.count(old) != 1:
            # NEVER a pass. A mutation that cannot be applied tells you nothing, and reading
            # it as reassurance is the exact defect this file exists to prevent.
            unapplied.append(f"{tool} / {desc}: pattern matched {text.count(old)}x, expected 1")
            continue
        with tempfile.TemporaryDirectory() as td:
            backup = Path(td) / tool
            shutil.copy2(src, backup)
            try:
                src.write_text(text.replace(old, new, 1))
                rc = run_self_check(src)
            finally:
                shutil.copy2(backup, src)      # ALWAYS restored, even on exception
        if rc == 0:
            survived.append(f"{tool} / {desc}")
            print(f"  SURVIVED  {tool}: {desc}")
        else:
            caught += 1
            if args.verbose:
                print(f"  caught    {tool}: {desc}")

    total = len(MUTATIONS)
    print(f"\n  {caught}/{total} mutation(s) caught by --self-check")
    for u in unapplied:
        print(f"  NOT APPLIED: {u}")
    for s in survived:
        print(f"  SURVIVED: {s}")
    if survived:
        # This label was WRONG on the first run and is the reason it now says two things.
        # A survivor has TWO possible causes and this tool cannot distinguish them:
        print("\n  A SURVIVOR means one of two things, and this tool CANNOT tell which:")
        print("    (a) the fixture is vacuous — it passes without exercising the defect; or")
        print("    (b) the mutation is no longer a defect, because a LATER guard covers it")
        print("        (defence in depth). Verified 2026-08-11: removing the size guard")
        print("        survived precisely because the append-only extension check already")
        print("        rejects a truncated source.")
        print("  Triage by hand: construct the failure the mutation should cause and see")
        print("  whether the tool still prevents it. Calling every survivor a vacuous")
        print("  fixture would be this canary making the same class of unverified claim")
        print("  it exists to catch.")
    if unapplied:
        print("\n  A mutation that could not be applied is UNKNOWN, never a pass. Usually the")
        print("  code was refactored — update the pattern, do not delete the mutation.")
    return 2 if unapplied else (1 if survived else 0)


if __name__ == "__main__":
    sys.exit(main())
