#!/usr/bin/env python3
"""Tests for findings_ledger.py (decouple-capability Phase 1).

The centrepiece is the PARITY test: findings_ledger._append_capped copies its append/
rotation semantics from remediation_ledger._append_capped (a different repo), so this test
pins the two together on the invariants that matter — never-drop-batch (F2) and split-on-"\\n"
(F4, the U+2028 gotcha). If the source drifts, this test is the tripwire.

Run: python3 -m pytest test_findings_ledger.py -q   (or: python3 test_findings_ledger.py)
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import findings_ledger as fl  # noqa: E402

# Import the copy-source directly from dev-env-config/claude/hooks/stop.
_SRC = (Path.home() / "dev" / "infrastructure" / "dev-env-config"
        / "claude" / "hooks" / "stop" / "remediation_ledger.py")


def _load_remediation_ledger():
    if not _SRC.exists():
        return None
    spec = importlib.util.spec_from_file_location("remediation_ledger_src", _SRC)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


class TestClassify(unittest.TestCase):
    def test_below_bar_is_logged(self):
        self.assertEqual(fl.classify({"impact_axes": ["LOW", "MEDIUM", "LOW"]}), "logged")

    def test_two_of_three_high_is_queued(self):
        self.assertEqual(fl.classify({"impact_axes": ["HIGH", "HIGH", "LOW"]}), "queued")

    def test_single_high_is_logged(self):
        self.assertEqual(fl.classify({"impact_axes": ["HIGH", "LOW", "LOW"]}), "logged")

    def test_work_blocking_is_queued(self):
        self.assertEqual(fl.classify({"work_blocking": True}), "queued")

    def test_irreversible_override_is_queued(self):
        self.assertEqual(fl.classify({"irreversible": True}), "queued")

    def test_string_impact_axes_does_not_miscount(self):
        # a bare string must NOT iterate per-character (would silently count 0 HIGH)
        self.assertEqual(fl.classify({"impact_axes": "HIGH,HIGH"}), "logged")


class TestRecordAndRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = fl.LEDGER_PATH
        fl.LEDGER_PATH = Path(self.tmp) / "findings-ledger.jsonl"

    def tearDown(self):
        fl.LEDGER_PATH = self._orig

    def test_record_schema_and_disposition(self):
        row = fl.record({"normalized_class": "trivial-log", "impact_axes": ["LOW", "LOW", "LOW"]},
                        session_id="s1")
        for k in ("id", "normalized_class", "first_seen", "last_seen", "recurrence_count",
                  "impact_axes", "disposition", "session_id", "retriage_history",
                  "calibration_outcome"):
            self.assertIn(k, row)
        self.assertEqual(row["disposition"], "logged")
        self.assertEqual(row["session_id"], "s1")

    def test_queued_and_logged_partition(self):
        fl.record({"normalized_class": "a", "impact_axes": ["LOW"]})
        fl.record({"normalized_class": "b", "work_blocking": True})
        self.assertEqual(fl.queued_depth(fl.LEDGER_PATH), 1)
        self.assertEqual(len(fl.logged_rows(fl.LEDGER_PATH)), 1)


class TestSplitOnNewlineU2028(unittest.TestCase):
    """F4: reads must use split("\\n"), NOT splitlines() (which breaks on U+2028)."""

    def test_raw_u2028_in_value_is_one_row(self):
        tmp = Path(tempfile.mkdtemp()) / "u2028.jsonl"
        # Manually write a row whose value contains a RAW U+2028 (ensure_ascii=False),
        # then a normal row. splitlines() would see 3 lines; split("\n") sees 2.
        rec = {"id": "x", "normalized_class": "has sep"}
        tmp.write_text(json.dumps(rec, ensure_ascii=False) + "\n"
                       + json.dumps({"id": "y"}, ensure_ascii=False) + "\n", encoding="utf-8")
        rows = fl.read_all(tmp)
        self.assertEqual(len(rows), 2, "U+2028 inside a value must not split the row")
        self.assertEqual(rows[0]["normalized_class"], "has sep")


class TestParityWithRemediationLedger(unittest.TestCase):
    """Pin findings_ledger._append_capped to remediation_ledger._append_capped."""

    def setUp(self):
        self.src = _load_remediation_ledger()
        if self.src is None:
            self.skipTest("remediation_ledger.py source not importable")

    def _run(self, mod, cap, preexisting, batch):
        tmp = Path(tempfile.mkdtemp()) / "ledger.jsonl"
        # seed pre-existing rows
        if preexisting:
            tmp.write_text("".join(json.dumps(e) + "\n" for e in preexisting), encoding="utf-8")
        orig_cap = mod.LEDGER_CAP
        mod.LEDGER_CAP = cap
        try:
            ret = mod._append_capped(tmp, batch)
        finally:
            mod.LEDGER_CAP = orig_cap
        content = [ln for ln in tmp.read_text(encoding="utf-8").split("\n") if ln.strip()]
        return ret, content

    def test_eviction_parity(self):
        # cap=5, 10 pre-existing + 3 appended -> both keep last max(5,3)=5, return 5
        pre = [{"i": i} for i in range(10)]
        batch = [{"b": j} for j in range(3)]
        r_ret, r_content = self._run(self.src, 5, pre, batch)
        f_ret, f_content = self._run(fl, 5, pre, batch)
        self.assertEqual(r_ret, f_ret, "return value must match source")
        self.assertEqual(r_ret, 5)
        self.assertEqual(r_content, f_content, "kept-tail must match source")
        # never-drop-batch: the 3 appended rows are the last 3
        self.assertEqual([json.loads(x) for x in f_content[-3:]], batch)

    def test_never_drop_batch_parity(self):
        # cap=5, batch of 8 alone exceeds cap -> both keep all 8 (F2), return 8
        pre = [{"i": i} for i in range(4)]
        batch = [{"b": j} for j in range(8)]
        r_ret, r_content = self._run(self.src, 5, pre, batch)
        f_ret, f_content = self._run(fl, 5, pre, batch)
        self.assertEqual(r_ret, f_ret)
        self.assertEqual(f_ret, 8, "must NEVER drop the just-appended batch")
        self.assertEqual([json.loads(x) for x in f_content], batch)

    def test_empty_batch_parity(self):
        self.assertEqual(self.src._append_capped(Path("/nonexistent/x"), []), -1)
        self.assertEqual(fl._append_capped(Path("/nonexistent/x"), []), -1)


class TestGuardrail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = fl.GUARDRAIL_PATH
        fl.GUARDRAIL_PATH = Path(self.tmp) / ".findings-guardrail.json"

    def tearDown(self):
        fl.GUARDRAIL_PATH = self._orig

    def test_bump_increments_and_persists(self):
        self.assertEqual(fl.guardrail_state().get("backlog_opens", 0), 0)
        fl.bump_backlog_opens()
        fl.bump_backlog_opens()
        st = fl.guardrail_state()
        self.assertEqual(st["backlog_opens"], 2)
        self.assertIsNotNone(st["last_open"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
