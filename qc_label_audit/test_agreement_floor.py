#!/usr/bin/env python3
"""Controls for the agreement floor (Dart xd0RMQQ2fb0M).

WHAT THE FLOOR IS FOR: verdict.py previously called fp_measure.finalize() unconditionally,
so a `confirmed_fp` was computed from raters agreeing at chance level and then a kappa was
printed beside it, leaving the reader to spot the contradiction. a101 is the worked case:
kappa 0.1057 over n=123 (57.7% raw agreement). The prescription in evidence_gate.py is to
gate on agreement BEFORE computing confirmed_fp at all.

THE GROUND-TRUTH CONTROL is test_a101_real_labels_reproduce_recorded_values: it runs the
predicate over the ACTUAL a101.raters.json and requires it to reproduce the numbers already
recorded in the artifact (kappa 0.1057, pct_agreement 0.5772). A metric implementation that
is not checked against a known value is just arithmetic with confidence.
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agreement as ag  # noqa: E402

A101 = pathlib.Path.home() / ".claude/logs/fp-gate/a101.raters.json"


def mk(tp_tp=0, fp_fp=0, tp_fp=0, fp_tp=0):
    return ([("TP", "TP")] * tp_tp + [("FP", "FP")] * fp_fp
            + [("TP", "FP")] * tp_fp + [("FP", "TP")] * fp_tp)


class Floor(unittest.TestCase):
    # --- the blocking behaviour ------------------------------------------------------
    def test_perfect_agreement_is_admissible(self):
        a = ag.admissibility(mk(tp_tp=30, fp_fp=30))
        self.assertTrue(a["admissible"])
        self.assertAlmostEqual(a["kappa"], 1.0)
        self.assertIsNone(a["cause"])

    def test_chance_level_is_not_admissible(self):
        # NEGATIVE CONTROL's partner: heavy disagreement must be refused.
        a = ag.admissibility(mk(tp_tp=15, fp_fp=15, tp_fp=15, fp_tp=15))
        self.assertFalse(a["admissible"])
        self.assertIsNotNone(a["cause"])

    def test_just_below_floor_blocks_and_just_above_admits(self):
        """The floor must actually bite AT the boundary, not merely at the extremes.
        A threshold only tested with 1.0 and 0.0 is not tested."""
        below = ag.admissibility(mk(tp_tp=40, fp_fp=40, tp_fp=9, fp_tp=9))
        above = ag.admissibility(mk(tp_tp=45, fp_fp=45, tp_fp=4, fp_tp=4))
        self.assertLess(below["kappa"], ag.KAPPA_FLOOR)
        self.assertFalse(below["admissible"])
        self.assertGreater(above["kappa"], ag.KAPPA_FLOOR)
        self.assertTrue(above["admissible"])

    def test_undefined_kappa_is_not_admissible(self):
        """Both raters label everything TP: kappa is undefined. That is NOT agreement --
        it is the degenerate case, and reporting it as admissible would be the exact
        'cannot tell nothing-to-find from could-not-look' failure."""
        a = ag.admissibility(mk(tp_tp=50))
        self.assertIsNone(a["kappa"])
        self.assertFalse(a["admissible"])
        self.assertIn("undefined", a["cause"])

    def test_empty_input_is_not_admissible(self):
        a = ag.admissibility([])
        self.assertFalse(a["admissible"])

    def test_uncertain_labels_are_excluded_not_counted_as_agreement(self):
        pairs = mk(tp_tp=10) + [("uncertain", "TP")] * 10
        a = ag.admissibility(pairs)
        self.assertEqual(a["n_resolved"], 10)

    def test_pct_agreement_also_excludes_uncertain(self):
        """Covers pct_agreement's OWN filter, not just admissibility's.

        Added after a mutation run: replacing pct_agreement's binary filter with
        `list(pairs)` left the suite fully green (M5 survived), because the only test
        touching pct_agreement runs on real a101 data, which happens to contain zero
        `uncertain` pairs. A function exercised solely through data that never hits the
        branch is untested on that branch. Counting an (uncertain, TP) pair as a
        disagreement would silently deflate agreement and make the floor over-block.
        """
        pairs = mk(tp_tp=10) + [("uncertain", "TP")] * 10
        self.assertEqual(ag.pct_agreement(pairs), 1.0)
        self.assertIsNone(ag.pct_agreement([("uncertain", "uncertain")] * 5))

    # --- the DIAGNOSIS, which is what makes this more than a threshold ---------------
    # DEC-320 rules out "a bare Landis-Koch veto"; the cause is the part that makes the
    # block actionable, so each band gets a control.
    def test_directional_band(self):
        a = ag.admissibility(mk(tp_tp=20, fp_fp=20, tp_fp=30, fp_tp=2))
        self.assertIn("DIRECTIONAL", a["cause"])

    def test_symmetric_band(self):
        a = ag.admissibility(mk(tp_tp=20, fp_fp=20, tp_fp=16, fp_tp=16))
        self.assertIn("SYMMETRIC", a["cause"])

    def test_mixed_band(self):
        """The band that exists because a binary call over-claimed on real data."""
        a = ag.admissibility(mk(tp_tp=20, fp_fp=20, tp_fp=16, fp_tp=36))
        self.assertIn("MIXED", a["cause"])

    # --- GROUND TRUTH ----------------------------------------------------------------
    @unittest.skipUnless(A101.exists(), "a101.raters.json not present")
    def test_a101_real_labels_reproduce_recorded_values(self):
        rows = json.loads(A101.read_text())["rows"]
        pairs = [(r.get("codex", "uncertain"), r.get("deepseek", "uncertain")) for r in rows]
        a = ag.admissibility(pairs)
        self.assertEqual(a["n_resolved"], 123)
        self.assertAlmostEqual(a["kappa"], 0.1057, places=4)      # recorded in a101.json
        self.assertAlmostEqual(ag.pct_agreement(pairs), 0.5772, places=4)
        self.assertFalse(a["admissible"])
        self.assertEqual(a["n_disagreements"], 52)
        self.assertIn("MIXED", a["cause"])   # skew 0.385 -- a 2.25:1 lean, not scatter


if __name__ == "__main__":
    unittest.main(verbosity=2)
