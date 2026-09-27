#!/usr/bin/env python3
"""Tests for suite-ratchet.py: behavioural controls over throwaway fixture directories.

Every control runs the REAL tool as a subprocess (the way cron runs it) and asserts on exit code
AND output marker AND, where relevant, the state/ledger files: never exit status alone.
SELFTEST (falsifiability control): the same controls are re-run against a no-op mutant that
always prints OK and exits 0; the suite must report failures for it, or the suite itself is
vacuous. Denominator is counted at runtime: ran == len(CONTROLS) is asserted.
Run: python3 tests/suite-ratchet.test.py      exit 0 all pass, 1 any failure
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "suite-ratchet.py"

PYT_OK = "def test_a():\n    assert True\n"
PYT_FAIL = "def test_b():\n    assert False\n"
SCR = "import sys\nprint('{out}')\nsys.exit({rc})\n"


def mkdir(td: Path, files: dict) -> Path:
    d = td / "suite"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body)
    return d


def run(tool, d, state, *extra, timeout=120):
    p = subprocess.run([sys.executable, str(tool), "--dir", str(d), "--state", str(state), *extra],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def init(tool, d, state, expires="2099-01-01"):
    return run(tool, d, state, "--init", "--issue", "TESTISSUE", "--owner", "t", "--expires", expires)


BASE = {"test_p.py": PYT_OK + PYT_FAIL,
        "test_s1.py": SCR.format(out="  FAIL: one", rc=1),
        "test_s2.py": SCR.format(out="[FAIL] two", rc=1),
        "test_s3.py": SCR.format(out="t7_x: FAIL — row not written", rc=1),
        "test_s4.py": SCR.format(out="all good", rc=0),
        "test_s5.py": SCR.format(out="3 test(s) failed", rc=3)}   # nonzero rc, no FAIL line


def c_unchanged_ok(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    rc, out = run(tool, d, st)
    return rc == 0 and "SUITE-RATCHET OK" in out, f"rc={rc} {out[-200:]}"


def c_init_counts_formats(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    s = json.loads(st.read_text())
    want = {"test_s1.py": 1, "test_s2.py": 1, "test_s3.py": 1, "test_s5.py": 1}
    got = {k: v["count"] for k, v in s["script_fail_counts"].items()}
    ok = got == want and list(s["pytest_failures"]) == ["test_p::test_b"] and s["denominator"] == 2 + 5
    return ok, f"got={got} pytest={list(s['pytest_failures'])} denom={s['denominator']}"


def c_new_pytest_failure(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    (d / "test_p.py").write_text(PYT_OK + PYT_FAIL + "def test_c():\n    assert 0\n")
    rc, out = run(tool, d, st)
    return rc == 1 and "REGRESSION pytest test_p::test_c" in out, f"rc={rc} {out[-200:]}"


def c_script_count_up(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    (d / "test_s1.py").write_text("print('  FAIL: one')\nprint('  FAIL: two')\nraise SystemExit(1)\n")
    rc, out = run(tool, d, st)
    return rc == 1 and "REGRESSION script test_s1.py: 2 failing (baseline 1)" in out, f"rc={rc} {out[-200:]}"


def c_new_script_file_failing(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    (d / "test_s6.py").write_text(SCR.format(out="boom", rc=2))   # rc!=0, no FAIL line
    rc, out = run(tool, d, st)
    return rc == 1 and "REGRESSION script test_s6.py" in out, f"rc={rc} {out[-200:]}"


def c_ratchet_down_then_rebreak(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    (d / "test_p.py").write_text(PYT_OK)                           # test_b fixed (and removed)
    (d / "test_p.py").write_text(PYT_OK + "def test_b():\n    assert True\n")
    rc1, out1 = run(tool, d, st)
    s = json.loads(st.read_text())
    ledger = (td / "st.ledger.jsonl").read_text().splitlines()
    (d / "test_p.py").write_text(PYT_OK + PYT_FAIL)                # re-break
    rc2, out2 = run(tool, d, st)
    ok = (rc1 == 0 and "RATCHET-DOWN pytest test_p::test_b" in out1 and s["pytest_failures"] == {}
          and any('"ratchet"' in r for r in ledger) and rc2 == 1 and "REGRESSION pytest test_p::test_b" in out2)
    return ok, f"rc1={rc1} rc2={rc2} state={s['pytest_failures']}"


def c_expired(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st, expires="2000-01-01")
    rc, out = run(tool, d, st)
    return rc == 1 and "EXPIRED pytest test_p::test_b" in out, f"rc={rc} {out[-200:]}"


def c_collection_error(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    (d / "test_p.py").write_text("def test_a(:\n")                 # syntax error -> pytest rc 2... or 1
    (d / "test_q.py").write_text("import nonexistent_module_xyz\ndef test_z():\n    pass\n")
    rc, out = run(tool, d, st)
    # pytest exits 2 on collection errors; that is UNKNOWN (page as unknown), never a regression.
    return rc == 2 and "CANNOT-ASSESS" in out, f"rc={rc} {out[-200:]}"


def c_shrunk(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    for n in ("test_s4.py", "test_s5.py", "test_s3.py"):
        (d / n).unlink()
    rc, out = run(tool, d, st)
    return rc == 1 and "SHRUNK denominator" in out, f"rc={rc} {out[-200:]}"


def c_empty_dir(tool, td):
    d = td / "empty"
    d.mkdir()
    rc, out = run(tool, d, td / "st.json")
    return rc == 2 and "CANNOT-ASSESS" in out, f"rc={rc} {out[-200:]}"


def c_state_unreadable(tool, td):
    d, st = mkdir(td, BASE), td / "st.json"
    st.write_text("{not json")
    rc, out = run(tool, d, st)
    return rc == 2 and "CANNOT-ASSESS state unreadable" in out, f"rc={rc} {out[-200:]}"


def c_script_timeout_counts(tool, td):
    d, st = mkdir(td, {"test_s4.py": SCR.format(out="ok", rc=0)}), td / "st.json"
    init(tool, d, st)
    (d / "test_hang.py").write_text("import time\ntime.sleep(30)\n")
    rc, out = run(tool, d, st, "--timeout", "2")
    return rc == 1 and "test_hang.py" in out and "timeout" in out, f"rc={rc} {out[-200:]}"


def c_mixed_night_persists_fix_not_regression(tool, td):
    """A fix and an unrelated regression on the same night: the fix IS persisted (so a later
    re-break of the fixed test is a regression again), the new failure is NOT adopted."""
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    (d / "test_p.py").write_text(PYT_OK + "def test_b():\n    assert True\n")   # fix test_b
    (d / "test_s6.py").write_text(SCR.format(out="  FAIL: new", rc=1))         # new regression
    rc1, _ = run(tool, d, st)
    s = json.loads(st.read_text())
    (d / "test_p.py").write_text(PYT_OK + PYT_FAIL)                            # re-break test_b
    rc2, out2 = run(tool, d, st)
    ok = (rc1 == 1 and s["pytest_failures"] == {} and "test_s6.py" not in s["script_fail_counts"]
          and rc2 == 1 and "REGRESSION pytest test_p::test_b" in out2)
    return ok, f"rc1={rc1} rc2={rc2} pytest_state={s['pytest_failures']}"


def c_crash_never_lowers_baseline(tool, td):
    d, st = mkdir(td, {"test_m.py": "for i in range(5): print(f'  FAIL: c{i}')\nraise SystemExit(5)\n"}), td / "st.json"
    init(tool, d, st)
    (d / "test_m.py").write_text("import nonexistent_module_q\n")             # crash: rc 1, no FAIL line
    rc, out = run(tool, d, st)
    count = json.loads(st.read_text())["script_fail_counts"]["test_m.py"]["count"]
    return rc == 1 and "UNRELIABLE script test_m.py" in out and count == 5, f"rc={rc} count={count}"


def c_overlapping_run_cannot_assess(tool, td):
    import fcntl
    d, st = mkdir(td, BASE), td / "st.json"
    init(tool, d, st)
    with open(st.with_suffix(".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, out = run(tool, d, st)
    return rc == 2 and "another run holds" in out, f"rc={rc} {out[-160:]}"


def c_deadline_cannot_assess(tool, td):
    d, st = mkdir(td, {"test_a.py": "import time; time.sleep(3)\n", "test_b.py": "print('ok')\n"}), td / "st.json"
    init(tool, d, st)
    rc, out = run(tool, d, st, "--deadline", "1")
    return rc == 2 and "deadline exceeded" in out, f"rc={rc} {out[-160:]}"


def c_shell_suite_counted_and_regresses(tool, td):
    d, st = mkdir(td, {"a.test.sh": "echo 'FAIL one'\nexit 1\n", "b.test.sh": "echo ok\nexit 0\n"}), td / "st.json"
    init(tool, d, st)
    s = json.loads(st.read_text())
    (d / "b.test.sh").write_text("echo 'nope'\nexit 1\n")          # shell rc!=0, no FAIL line
    rc, out = run(tool, d, st)
    ok = (s["script_fail_counts"].get("a.test.sh", {}).get("count") == 1 and s["denominator"] == 2
          and rc == 1 and "REGRESSION script b.test.sh" in out)
    return ok, f"init={s['script_fail_counts']} rc={rc} {out[-160:]}"


def c_exclude_is_counted_not_run(tool, td):
    d, st = mkdir(td, {**BASE, "test_x.py": SCR.format(out="  FAIL: x", rc=1)}), td / "st.json"
    rc0, _ = run(tool, d, st, "--init", "--issue", "I", "--owner", "t", "--expires", "2099-01-01",
                 "--exclude", "test_x.py")
    rc, out = run(tool, d, st, "--exclude", "test_x.py")
    return rc0 == 0 and rc == 0 and "excluded=1" in out and "test_x.py" not in st.read_text(), f"rc={rc} {out[-160:]}"


def c_rc0_positive_control_not_counted(tool, td):
    d, st = mkdir(td, {"test_pc.py": SCR.format(out="  FAIL positive control (failed as designed)", rc=0),
                       "test_s1.py": SCR.format(out="  FAIL: one", rc=1)}), td / "st.json"
    init(tool, d, st)
    got = {k: v["count"] for k, v in json.loads(st.read_text())["script_fail_counts"].items()}
    return got == {"test_s1.py": 1}, f"got={got}"


def c_all_naming_conventions_enrolled(tool, td):
    files = {"test-hy.sh": "exit 1\n", "x.test.sh": "exit 0\n", "test_u.sh": "exit 0\n",
             "y_test.py": PYT_OK, "test-hy.py": SCR.format(out="ok", rc=0), "z.test.py": SCR.format(out="ok", rc=0),
             "helper.py": "raise SystemExit(1)\n"}                   # not a test: must NOT be run or counted
    d, st = mkdir(td, files), td / "st.json"
    init(tool, d, st)
    s = json.loads(st.read_text())
    got = {k: v["count"] for k, v in s["script_fail_counts"].items()}
    return s["denominator"] == 1 + 5 and got == {"test-hy.sh": 1}, f"denom={s['denominator']} got={got}"


def c_report_writes_lists_side_effects(tool, td):
    logs = td / "logs"; logs.mkdir()
    (logs / "quiet.log").write_text("x")
    d, st = mkdir(td, {"test_w.py": f"open({str(logs / 'touched.log')!r}, 'a').write('y')\n"}), td / "st.json"
    init(tool, d, st)
    rc, out = run(tool, d, st, "--report-writes", str(logs))
    ok = rc == 0 and "wrote-during-run" in out and "touched.log" in out and "quiet.log" not in out
    return ok, f"rc={rc} {out[-200:]}"


def c_pytest_phase_deadline(tool, td):
    """The deadline must CAP the run, not just be checked after it: with a 20s test and
    --deadline 2 the tool must return in well under 20s (a post-hoc check alone would wait ~20s)."""
    import time as _t
    d, st = mkdir(td, {"test_slow.py": "import time\ndef test_x():\n    time.sleep(20)\n"}), td / "st.json"
    st.write_text(json.dumps({"pytest_failures": {}, "script_fail_counts": {}, "denominator": 1}))
    t0 = _t.monotonic()
    rc, out = run(tool, d, st, "--deadline", "2", "--timeout", "60")
    el = _t.monotonic() - t0
    return rc == 2 and "CANNOT-ASSESS" in out and el < 10, f"rc={rc} elapsed={el:.1f}s {out[-120:]}"


CONTROLS = [c_pytest_phase_deadline, c_report_writes_lists_side_effects, c_all_naming_conventions_enrolled, c_rc0_positive_control_not_counted, c_shell_suite_counted_and_regresses, c_exclude_is_counted_not_run, c_unchanged_ok, c_init_counts_formats, c_new_pytest_failure, c_script_count_up,
            c_new_script_file_failing, c_ratchet_down_then_rebreak, c_expired, c_collection_error,
            c_shrunk, c_empty_dir, c_state_unreadable, c_script_timeout_counts,
            c_mixed_night_persists_fix_not_regression, c_crash_never_lowers_baseline,
            c_overlapping_run_cannot_assess, c_deadline_cannot_assess]


def suite(tool) -> tuple[int, int, list]:
    ran, failed, msgs = 0, 0, []
    for c in CONTROLS:
        with tempfile.TemporaryDirectory() as td:
            try:
                ok, detail = c(tool, Path(td))
            except Exception as exc:          # a crashing control is a failing control
                ok, detail = False, f"{type(exc).__name__}: {exc}"
        ran += 1
        if not ok:
            failed += 1
            msgs.append(f"  FAIL: {c.__name__}: {detail}")
    return ran, failed, msgs


def main() -> int:
    ran, failed, msgs = suite(TOOL)
    print("\n".join(msgs) if msgs else "")
    print(f"real tool: {ran - failed}/{ran} passed")
    ok = failed == 0 and ran == len(CONTROLS)
    # SELFTEST: a no-op mutant (always "OK", exit 0) must FAIL this suite, or the suite is vacuous.
    with tempfile.TemporaryDirectory() as td:
        mutant = Path(td) / "noop.py"
        mutant.write_text("print('SUITE-RATCHET OK'); raise SystemExit(0)\n")
        m_ran, m_failed, _ = suite(mutant)
    print(f"no-op mutant: {m_failed}/{m_ran} controls caught it")
    if m_failed < 8:                           # every adverse/unknown control must catch it
        print("  FAIL: SELFTEST — the suite cannot tell a no-op checker from the real one")
        ok = False
    print("ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
