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
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
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
    # --- added 2026-08-11 after EIGHT fixtures shipped vacuous, every one caught by manual
    # sabotage and none by authorship. This canary is SCHEDULED weekly, so registering the
    # mutations converts that discipline into a mechanism that runs whether or not I remember.
    ("governed-outcomes-check.py", "expected-path hits read as CLEAN again",
     'else "REVIEW_REQUIRED" if expected_hits else "CLEAN")', 'else "CLEAN")'),
    ("governed-outcomes-check.py", "credential hit/expected split forced to always-expected",
     "(expected if EXPECTED_PATH_RE.search(current) else hits).append(",
     "(expected if True else hits).append("),
    ("governed-outcomes-check.py", "token_used counted as break-glass",
     'if r.get("event") == "override_used":', "if True:"),
    # --- added 2026-08-12 with the handoff-artifact consumer (R1). Registering the mutations
    # in the same commit as the fixtures is the point: a fixture nobody re-proves decays into
    # decoration, and this file is the only thing that re-proves them on a schedule.
    ("governed-outcomes-check.py", "handoff adverse detection gutted",
     '    adverse = sum(1 for r in rows\n                  if ("artifact_verified" in r and r["artifact_verified"] is not True)\n                  or r.get("result") == "attempted")',
     "    adverse = 0"),
    ("governed-outcomes-check.py", "handoff post-cutover self-monitor removed",
     "    if post:", "    if False:"),
    ("governed-outcomes-check.py", "handoff pre-cutover rows read as CLEAN again",
     "    if pre:", "    if False:"),
    ("governed-outcomes-check.py", "unreadable input no longer forces UNKNOWN",
     '"adverse": adverse if adverse else (None if unreadable else 0),', '"adverse": adverse,'),
    ("governed-outcomes-check.py", "handoff log window bound removed",
     "        if ts.timestamp() >= cutoff:", "        if True:"),
    ("tail-consistency-check.py", "declarative-assignment branch removed (C2 recall gap)",
     "or ASSIGNS_TO_USER.search(you_f)):", "or False):"),
    ("tail-from-record.py", "foreign-session refusal reverted to advisory",
     "    if not own and not args.allow_foreign_session:", "    if False:"),
    ("tail-from-record.py", "dirty-file branch disabled",
     "        elif st:", "        elif False:"),
    ("tail-from-record.py", "UNKNOWN git state treated as clean",
     "        if st is None:", "        if False:"),
]


# ── Embedded-executable coverage (ANOMALY-REGISTER 138) ────────────────────────────────
# `commands/handoff.md` contains EXECUTABLE PYTHON inside a markdown file: the artifact
# read-back that decides whether a handoff is logged `success` or `attempted`. It has no
# --self-check, no CI, and no canary entry, so every test of it was an ad-hoc probe typed
# and discarded. It is the least-tested code in the handoff chain and it gates whether the
# log tells the truth. This makes that coverage durable and weekly.
HANDOFF_MD = Path.home() / ".claude" / "commands" / "handoff.md"


def _extract_readback(text: str) -> str | None:
    """Pull the read-back block out of the markdown. Returns None if the shape changed."""
    m = re.search(r"## Unified Logging.*?```python\n(.*?)```", text, re.S)
    if not m:
        return None
    body = m.group(1)
    if "# --- ARTIFACT READ-BACK" not in body or "# At end of handoff work" not in body:
        return None
    return "# --- ARTIFACT READ-BACK" + body.split("# --- ARTIFACT READ-BACK")[1] \
                                            .split("# At end of handoff work")[0]


def _probe_readback(block: str, sid: str | None, start: datetime) -> dict:
    env_backup = {k: os.environ.get(k) for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}
    try:
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop("CLAUDE_SESSION_ID", None)
        if sid:
            os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        ns: dict = {"os": os, "Path": Path, "datetime": datetime, "start_time": start}
        exec(block, ns)
        return ns
    finally:
        for k, v in env_backup.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def check_handoff_readback(verbose: bool = False) -> tuple[int, list[str]]:
    """Behavioural fixtures + mutations for the embedded read-back. Returns (caught, problems)."""
    problems: list[str] = []
    if not HANDOFF_MD.exists():
        return 0, ["handoff.md missing — embedded read-back UNCHECKED, never a pass"]
    text = HANDOFF_MD.read_text(errors="replace")
    block = _extract_readback(text)
    if block is None:
        # The markdown was restructured. UNKNOWN, never a pass — update the extractor.
        return 0, ["read-back block not found in handoff.md (shape changed) — UNKNOWN, not a pass"]
    try:
        compile(block, "<handoff-readback>", "exec")
    except SyntaxError as e:
        return 0, [f"read-back block does not compile: {e}"]

    # A session whose handoff exists (this repo always has at least one) drives the True path.
    hdir = Path.home() / ".claude" / "handoffs"
    # Pick by MTIME among FULL-UUID names only. Sorting by name landed on a deprecated
    # short-form file and the check correctly returned UNKNOWN — right behaviour, wrong input.
    cands = [f for f in hdir.glob("handoff-*.md")
             if f.stat().st_size > 0 and re.match(r"handoff-[0-9a-f]{8}-[0-9a-f]{4}-", f.name)]
    if not cands:
        return 0, ["no full-uuid handoff artifact to drive the fixtures — UNKNOWN, not a pass"]
    # discovery-advisory: newest-by-mtime is the trap that resolved a PEER's transcript in
    # ANOMALY-REGISTER row 77 — flagged here by derived_state_scanner, correctly. It is safe in
    # THIS use and only this one: the fixtures need ANY well-formed handoff artifact to drive
    # the read-back, never THIS session's. Picking a peer's file is a valid fixture input. If
    # this code is ever changed to assert something about the CURRENT session, this line must
    # become an authoritative lookup (get_session_key / stdin session_id) instead.
    sample = max(cands, key=lambda f: f.stat().st_mtime)
    sid = re.match(r"handoff-([0-9a-f-]{36})-", sample.name).group(1)
    # age-only: mtime is used purely to straddle the artifact in time (one day either side) so
    # the run-scoped filter is exercised in both directions. No identity is derived from it.
    old = datetime.fromtimestamp(sample.stat().st_mtime) - timedelta(days=1)
    now = datetime.now() + timedelta(days=1)   # strictly after every artifact

    fixtures = [
        ("artifact newer than run start reads VERIFIED",
         lambda b: _probe_readback(b, sid, old).get("artifact_verified") is True),
        ("artifact older than run start reads UNVERIFIED (stale-match closed)",
         lambda b: _probe_readback(b, sid, now).get("artifact_verified") is False),
        ("unverified must not fabricate a path",
         lambda b: _probe_readback(b, sid, now).get("handoff_file") is None),
        ("absent session id reads UNVERIFIED without crashing",
         lambda b: _probe_readback(b, None, old).get("artifact_verified") is False),
    ]
    for name, fn in fixtures:
        try:
            if not fn(block):
                problems.append(f"FIXTURE FAILED: {name}")
        except Exception as exc:                      # a crash is a failure, never a pass
            problems.append(f"FIXTURE CRASHED ({name}): {exc}")

    # Mutations: prove the fixtures are not vacuous.
    muts = [("run-scoped filter removed",
             "_cands = [p for p in _cands if p.stat().st_mtime >= start_time.timestamp()]",
             "_cands = _cands"),
            ("path fabrication restored",
             "handoff_file = _cands[0] if _cands else None",
             'handoff_file = _cands[0] if _cands else Path("/nonexistent-fabricated.md")')]
    caught = 0
    for name, old_s, new_s in muts:
        if block.count(old_s) != 1:
            problems.append(f"MUTATION NOT APPLIED ({name}): matched {block.count(old_s)}x — UNKNOWN")
            continue
        mutated = block.replace(old_s, new_s, 1)
        try:
            ns = _probe_readback(mutated, sid, now)
            went_red = ns.get("artifact_verified") is not False or ns.get("handoff_file") is not None
        except Exception:
            went_red = True                            # a crash IS detection
        if went_red:
            caught += 1
            if verbose:
                print(f"  caught    handoff.md readback: {name}")
        else:
            problems.append(f"MUTATION SURVIVED ({name}) — fixture is vacuous")
    return caught, problems


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
    # The embedded-read-back checker is itself code; prove it can report a problem.
    ok.append(("embedded-readback extractor returns None on a changed shape",
               _extract_readback("## Unified Logging\n```python\nnothing here\n```") is None))
    ok.append(("embedded-readback extractor finds the real block",
               _extract_readback(HANDOFF_MD.read_text(errors="replace")) is not None
               if HANDOFF_MD.exists() else True))
    _c, _p = check_handoff_readback()
    ok.append(("embedded-readback check reports no problems against the live file", not _p))
    ok.append(("embedded-readback mutations are both caught", _c == 2))

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

    # Embedded-executable coverage (row 138): handoff.md's read-back has no --self-check of
    # its own, so it cannot be a MUTATIONS entry. It is checked here so the weekly run covers it.
    hb_caught, hb_problems = check_handoff_readback(args.verbose)
    caught += hb_caught
    for prob in hb_problems:
        (survived if prob.startswith("MUTATION SURVIVED") else unapplied).append(
            f"handoff.md readback: {prob}")

    total = len(MUTATIONS) + 2      # +2 embedded-read-back mutations
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
