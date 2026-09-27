#!/usr/bin/env python3
# BUDGET: 3600
"""suite-ratchet.py — give an already-red test directory a consumer that pages only on regressions.

WHY THIS EXISTS (measured 2026-09-26, session 9945d90f, Dart CutVIZjLRXmv / tKncp8RlPlkr):
  ~/.claude/hooks/stop/tests was recorded as "2 failed" and had NO scheduled runner. A clean-tree
  run found >=100 failing checks across 6 files, because 35 of 91 test_*.py files are script-mode
  (no pytest-collectable test_ functions) and `pytest .` never executes them. A red suite with no
  consumer normalises: a NEW failure is indistinguishable from the old red, so nobody looks.
  Plain "any FAIL pages" on a red suite pages forever and is tuned out. So: a RATCHET.

MECHANISM. Every test_*.py in --dir runs in its NATIVE mode (pytest when it has test_ functions,
else `python3 FILE`). Failures are identified per pytest node id and, for script-mode files, as a
per-file failing-check COUNT (script FAIL lines carry variable detail, a count is stable). Each is
compared with a state file of known failures, each entry carrying issue, owner and expiry.
  - a NEW failure, a count ABOVE its baseline, an EXPIRED entry, or a SHRUNK denominator -> page
  - a failure that went away -> the baseline RATCHETS DOWN automatically (ledger row, no page), so
    a later re-break is a regression again instead of hiding in stale slack
  - the run itself failing (collection error, timeout, unparseable report, 0 files) -> UNKNOWN

OBJECTIVE:   OBJECTIVES.md Tier 1 #2 (System Catches Own Errors): a regression in a test directory
             reaches a human the night it happens, even when the directory was already red.
SUCCESS:     exit 0 + "SUITE-RATCHET OK" when every failure is a known, unexpired entry; exit 1 +
             "SUITE-RATCHET-ADVERSE" on any regression/expiry/shrink; exit 2 + "SUITE-RATCHET-
             CANNOT-ASSESS" when the run could not be evaluated. One ledger row per state change.
FAILURE:     a new failure that exits 0; a ratchet-down with no ledger row; a crashed run reported ok.
TRIGGERS:    date-driven: crontab via scheduled-check-runner.sh (daily). Event-driven: none yet —
             a /ship consumer for evidence_gate predicate changes is Dart tKncp8RlPlkr.
CONSUMER:    scheduled-check-runner.sh (DEC-334 exit contract: 1 adverse, 2 unknown -> notify.sh page;
             heartbeat row every run); the human acts on the page; the ledger is the audit trail.
SILENT-FAIL: exit 2 on every path that could not measure; a missing heartbeat is paged by the
             runner's own census; unknown flags never default to "clean".
PRIOR-ART:   ratchet gates (imbue-ai/ratchets; ~/bin/denominator-ratchet); quarantine-with-expiry
             (oneuptime.com/blog/post/2026-07-28-quarantine-flaky-tests); scheduled-check-runner.sh.
             Increment over denominator-ratchet: per-identity failure sets over a MIXED-mode test
             directory, with auto-ratchet-down and expiring entries.
PROMOTION:   cannot block anything (a cron check has no action to gate).
predicate-rung: occurrence — tests/suite-ratchet.test.py (a no-op checker that always reports OK
             fails that suite: the regression, expiry and cannot-assess controls go red).

USAGE
  suite-ratchet.py --dir DIR --state STATE.json [--ledger L.jsonl] [--timeout S] [--deadline S]
                   [--script-env K=V ...] [--no-env-for FILE ...] [--exclude FILE ...]
                   [--report-writes DIR ...]
  suite-ratchet.py --dir DIR --state STATE.json --init --issue ID --owner NAME --expires YYYY-MM-DD
EXIT  0 ok   1 adverse   2 cannot-assess   4 usage
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PYTEST_FN = re.compile(r"^\s*def test_\w+\s*\(", re.M)
# Script-mode suites print failures at least three ways (all observed 2026-09-26):
#   "  FAIL: name", "[FAIL] name", "name: FAIL — detail", and "FAILED: 2 test(s)" summaries.
# The per-line counter takes the first three; summary lines ("N test(s) failed") are not counted
# twice because a nonzero rc with ZERO counted lines is recorded as 1 synthetic failure instead.
FAIL_LINE = re.compile(r"^\s*(?:\[FAIL\]|FAIL\b(?!ED)|\S.*?:\s+FAIL\b)", re.M)
SHRINK_TOLERANCE = 0.9     # page if the executed denominator falls below 90% of the baseline's
MASS_REGRESSION = 10       # this many at once is usually infrastructure, said so in the page


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Every naming convention present in the estate on 2026-09-26 (tools/tests uses hyphens: missing
# one glob is SILENT non-enrollment -- the file is neither run nor counted).
SHELL_GLOBS = ("*.test.sh", "test_*.sh", "test-*.sh")
PY_GLOBS = ("test_*.py", "*_test.py", "*.test.py", "test-*.py")


def classify(d: Path, exclude: set) -> tuple[list[Path], list[Path], list[str]]:
    """(pytest files, script files [python or shell], unreadable names). Shell suites run as
    `bash FILE`; a file matching no glob is not a test and is not counted anywhere."""
    pyt, scr, bad = [], [], []
    seen = set()
    for g in PY_GLOBS + SHELL_GLOBS:
        for f in sorted(d.glob(g)):
            if f.name in seen or f.name in exclude or not f.is_file():
                continue
            seen.add(f.name)
            if f.suffix == ".sh":
                scr.append(f)
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                bad.append(f.name)
                continue
            collectable = f.name.startswith("test_") or f.name.endswith("_test.py")  # pytest defaults
            (pyt if PYTEST_FN.search(text) and collectable else scr).append(f)
    return pyt, sorted(scr), bad


def run_pytest(d: Path, files: list[Path], timeout: int) -> tuple[set[str], int]:
    """Return (failing node ids, tests collected). Raises RuntimeError when not assessable."""
    if not files:
        return set(), 0
    # Preflight: `python -m pytest` with pytest missing exits 1 -- the same code as "tests failed".
    # pytest lives in the USER site here, so a changed HOME (cron edge, sandbox) loses it. Name it.
    pre = subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    if pre.returncode != 0:
        raise RuntimeError(f"pytest not importable by {sys.executable} (HOME={os.environ.get('HOME')}): "
                           f"{pre.stderr.strip().splitlines()[-1:]}")
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "r.xml"
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                f"--junitxml={xml}", *[f.name for f in files]],
                               cwd=d, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"pytest timed out after {timeout}s")
        if p.returncode not in (0, 1):      # 2 interrupted/collection error, 3 internal, 4 usage, 5 none
            raise RuntimeError(f"pytest rc={p.returncode}: {p.stdout.strip().splitlines()[-1:]}")
        try:
            root = ET.parse(xml).getroot()
        except (ET.ParseError, OSError) as exc:
            raise RuntimeError(f"junit report unreadable: {exc}")
    failed, collected = set(), 0
    for tc in root.iter("testcase"):
        collected += 1
        if any(c.tag in ("failure", "error") for c in tc):
            failed.add(f"{tc.get('classname')}::{tc.get('name')}")
    return failed, collected


def run_script(f: Path, timeout: int, env: dict) -> tuple[int, str, bool]:
    """Return (failing-check count, note, synthetic). SYNTHETIC = the count is not a measurement
    (timeout, or nonzero rc with no FAIL line) and is recorded as 1: it may flag a regression but
    must NEVER lower a baseline -- a 93-failure suite that crashes would otherwise read as '93 -> 1',
    i.e. a crash reported as an improvement."""
    cmd = ["bash", f.name] if f.suffix == ".sh" else [sys.executable, f.name]
    try:
        p = subprocess.run(cmd, cwd=f.parent, capture_output=True,
                           text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 1, f"timeout after {timeout}s", True
    n = len(FAIL_LINE.findall(p.stdout + "\n" + p.stderr))
    if p.returncode == 0:
        # The EXIT CODE is the verdict (as suite-count.sh states for pytest). Suites print FAIL on
        # purpose in positive controls ("  FAIL positive control ... failed as designed", seen in
        # test_last_assistant_text_ismeta.py 2026-09-26). A suite that prints FAIL but can never
        # exit nonzero is test-suite-lint's R1 (CANNOT-FAIL) class, not this tool's to guess at.
        return 0, (f"rc=0 ({n} FAIL-shaped line(s) not counted)" if n else "rc=0"), False
    if n == 0:
        tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
        return 1, f"rc={p.returncode} with no FAIL line: {tail[0][:100]}", True
    return n, f"rc={p.returncode}", False


def measure(d: Path, timeout: int, script_env: dict, no_env_for: set, exclude: set,
            deadline: float | None = None) -> dict:
    pyt, scr, bad = classify(d, exclude)
    if bad:
        raise RuntimeError(f"unreadable test files (would be silently skipped): {bad}")
    if not pyt and not scr:
        raise RuntimeError(f"no test files ({', '.join(PY_GLOBS + SHELL_GLOBS)}) in {d}")
    def _eff() -> int:   # no single run may outlast the OVERALL deadline
        if deadline is None:
            return timeout
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:   # never launch a run with no time left
            raise RuntimeError("overall --deadline exceeded before the next run (a partial run is not a result)")
        return max(1, min(timeout, remaining))
    failed, collected = run_pytest(d, pyt, _eff())
    if deadline is not None and time.monotonic() > deadline:
        raise RuntimeError("overall --deadline exceeded during the pytest phase (a partial run is not a result)")
    counts, notes, synthetic = {}, {}, set()
    for f in scr:
        if deadline is not None and time.monotonic() > deadline:
            raise RuntimeError(f"overall --deadline exceeded before {f.name} (a partial run is not a result)")
        env = dict(os.environ)
        if f.name not in no_env_for:
            env.update(script_env)
        n, note, syn = run_script(f, _eff(), env)
        if deadline is not None and time.monotonic() > deadline:
            raise RuntimeError(f"overall --deadline exceeded during {f.name} (a partial run is not a result)")
        notes[f.name] = note
        if n:
            counts[f.name] = n
        if syn:
            synthetic.add(f.name)
    present = {f.name for f in scr} | {f.stem for f in pyt}
    return {"pytest_failed": failed, "pytest_collected": collected, "script_files": len(scr),
            "script_counts": counts, "script_notes": notes, "script_synthetic": synthetic,
            "present": present}


def evaluate(state: dict, m: dict, today: str) -> tuple[list[str], list[str], dict]:
    """Pure: (adverse lines, ratchet-down lines, new state)."""
    adverse, down = [], []
    known_p = state.get("pytest_failures", {})
    known_s = state.get("script_fail_counts", {})
    for nid in sorted(m["pytest_failed"] - set(known_p)):
        adverse.append(f"REGRESSION pytest {nid}")
    syn = m.get("script_synthetic", set())
    for f, n in sorted(m["script_counts"].items()):
        base = known_s.get(f, {}).get("count", 0)
        if n > base:
            adverse.append(f"REGRESSION script {f}: {n} failing (baseline {base}) "
                           f"[{m['script_notes'].get(f, '')}]")
        elif f in syn and base > n:
            adverse.append(f"UNRELIABLE script {f}: crashed or timed out "
                           f"[{m['script_notes'].get(f, '')}]; baseline {base} NOT lowered")
    for kind, known in (("pytest", known_p), ("script", known_s)):
        for key, meta in sorted(known.items()):
            if str(meta.get("expires", "9999-12-31")) < today:
                adverse.append(f"EXPIRED {kind} {key} (expired {meta.get('expires')}, "
                               f"issue {meta.get('issue')}, owner {meta.get('owner')})")
    denom_now = m["pytest_collected"] + m["script_files"]
    denom_base = state.get("denominator", 0)
    if denom_base and denom_now < SHRINK_TOLERANCE * denom_base:
        adverse.append(f"SHRUNK denominator {denom_now} < {SHRINK_TOLERANCE:.0%} of baseline {denom_base}")
    # The new state carries ONLY known entries (a new failure is never adopted as baseline, however
    # the night went), lowered where a real measurement says so. It is persisted even on adverse
    # nights: otherwise a fix landing on the same night as an unrelated regression stays "known",
    # and its later re-break hides in the stale entry.
    present = m.get("present", set())
    new = {"pytest_failures": {}, "script_fail_counts": {},
           "denominator": max(denom_now, denom_base) if not adverse else denom_base}
    for nid, meta in known_p.items():
        if nid in m["pytest_failed"]:
            new["pytest_failures"][nid] = meta
        else:
            why = "now passes" if any(p in present for p in nid.split("::")[0].split(".")) else "FILE REMOVED"
            down.append(f"RATCHET-DOWN pytest {nid} {why} (was: issue {meta.get('issue')})")
    for f, meta in known_s.items():
        n = m["script_counts"].get(f, 0)
        base = meta.get("count", 0)
        if f in syn and base > n:           # unreliable count: keep the baseline as it was
            new["script_fail_counts"][f] = meta
            continue
        if n < base:
            why = "" if f in present else " (FILE REMOVED)"
            down.append(f"RATCHET-DOWN script {f}: {base} -> {n}{why}")
        if n:
            new["script_fail_counts"][f] = {**meta, "count": min(n, base)}
    return adverse, down, new


def _ledger_append(ledger: Path, **fields) -> None:
    with open(ledger, "a") as fh:
        fh.write(json.dumps({"ts": _now(), **fields}) + "\n")


def _atomic_write(path: Path, obj: dict) -> None:
    """temp + os.replace: an interrupted write can never leave truncated JSON behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(obj, indent=1, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):      # only on failure: never leave debris behind
            os.unlink(tmp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--ledger", type=Path)
    ap.add_argument("--timeout", type=int, default=900, help="per-file / per-pytest-run seconds")
    ap.add_argument("--deadline", type=int, default=3000,
                    help="overall seconds; past it the run is CANNOT-ASSESS, never partial-ok")
    ap.add_argument("--script-env", action="append", default=[])
    ap.add_argument("--no-env-for", action="append", default=[])
    ap.add_argument("--exclude", action="append", default=[],
                    help="file already run by its own scheduled check (counted as excluded, never silently)")
    ap.add_argument("--report-writes", type=Path, action="append", default=[],
                    help="INFO only: list DIRECT CHILDREN of DIR modified during the run (not recursive)")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--issue"); ap.add_argument("--owner"); ap.add_argument("--expires")
    a = ap.parse_args(argv)
    try:
        script_env = dict(kv.split("=", 1) for kv in a.script_env)
    except ValueError:
        print("SUITE-RATCHET usage: --script-env needs K=V"); return 4
    ledger = a.ledger or a.state.with_suffix(".ledger.jsonl")
    # One run per state file: an overlapping run (slow night, manual run during cron) would race on
    # the state. Non-blocking: the second run reports CANNOT-ASSESS at once rather than queueing.
    a.state.parent.mkdir(parents=True, exist_ok=True)
    with open(a.state.with_suffix(".lock"), "a") as lock_fh:   # closed on every path, incl. raise
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"SUITE-RATCHET-CANNOT-ASSESS another run holds {a.state.with_suffix('.lock')}"); return 2
        return _run(a, script_env, ledger)


def _run(a, script_env: dict, ledger: Path) -> int:
    today = dt.date.today().isoformat()
    def _mtimes():
        out = {}
        for d in a.report_writes:
            try:
                entries = list(d.iterdir())
            except OSError:          # missing or vanished dir: nothing to report, never a crash
                continue
            for f in entries:
                try:
                    out[f] = f.stat().st_mtime
                except OSError:
                    pass
        return out
    before = _mtimes()
    try:
        m = measure(a.dir, a.timeout, script_env, set(a.no_env_for), set(a.exclude),
                    deadline=time.monotonic() + a.deadline)
    except RuntimeError as exc:
        print(f"SUITE-RATCHET-CANNOT-ASSESS {a.dir}: {exc}"); return 2
    for f, t in sorted(_mtimes().items()):
        if before.get(f) != t:   # attribution is only clean when nothing else writes (nightly)
            print(f"SUITE-RATCHET INFO wrote-during-run {f}")
    denom = m["pytest_collected"] + m["script_files"]
    summary = (f"dir={a.dir} pytest_collected={m['pytest_collected']} pytest_failed={len(m['pytest_failed'])} "
               f"script_files={m['script_files']} script_failing_files={len(m['script_counts'])} "
               f"script_failing_checks={sum(m['script_counts'].values())} excluded={len(a.exclude)}")
    if a.init:
        if not (a.issue and a.owner and a.expires):
            print("SUITE-RATCHET usage: --init needs --issue --owner --expires"); return 4
        meta = {"issue": a.issue, "owner": a.owner, "expires": a.expires, "since": today}
        state = {"pytest_failures": {n: dict(meta) for n in sorted(m["pytest_failed"])},
                 "script_fail_counts": {f: {**meta, "count": n} for f, n in sorted(m["script_counts"].items())},
                 "denominator": denom}
        # ledger FIRST (as for ratchets): a crash in between leaves a duplicate row, never a gap
        _ledger_append(ledger, event="init", dir=str(a.dir), **meta,
                       pytest_failures=len(state["pytest_failures"]),
                       script_fail_counts={k: v["count"] for k, v in state["script_fail_counts"].items()},
                       denominator=denom)
        _atomic_write(a.state, state)
        print(f"SUITE-RATCHET INIT {summary}"); return 0
    try:
        state = json.loads(a.state.read_text())
    except (OSError, ValueError) as exc:
        print(f"SUITE-RATCHET-CANNOT-ASSESS state unreadable ({exc}); run --init"); return 2
    adverse, down, new = evaluate(state, m, today)
    print(f"SUITE-RATCHET {summary}")
    for line in down:
        print(line)
    grew = new["denominator"] != state.get("denominator", 0)
    if down or grew:   # new state never adopts a new failure; see evaluate()
        # ledger FIRST: a crash after it leaves a duplicate row, never a persisted ratchet with no row
        _ledger_append(ledger, event="ratchet", dir=str(a.dir), changes=down,
                       denominator=[state.get("denominator", 0), new["denominator"]])
        _atomic_write(a.state, new)
    if adverse:
        if len(adverse) >= MASS_REGRESSION:
            print(f"SUITE-RATCHET-ADVERSE MASS: {len(adverse)} at once — suspect infrastructure first")
        for line in adverse:
            print(f"SUITE-RATCHET-ADVERSE {line}")
        return 1
    print("SUITE-RATCHET OK every failure is a known, unexpired entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
