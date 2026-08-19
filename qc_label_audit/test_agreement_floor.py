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
    def test_band_is_a_discrete_key_not_only_prose(self):
        """`band` must be machine-readable and must AGREE with `cause`.

        Added because the suite was green over an addition it never touched: `band` was
        introduced and 11 controls still passed without asserting it once. Two review legs
        asked for the key precisely so callers stop substring-matching an English sentence;
        a key nothing checks would have shipped the stringly-typed problem with extra steps.
        The agreement assertion is the load-bearing half — two fields that can disagree are
        worse than one field.
        """
        for pairs, expected in (
            (mk(tp_tp=30, fp_fp=30), "ADMISSIBLE"),
            (mk(tp_tp=50), "UNDEFINED"),
            (mk(tp_tp=20, fp_fp=20, tp_fp=30, fp_tp=2), "DIRECTIONAL"),
            (mk(tp_tp=20, fp_fp=20, tp_fp=16, fp_tp=36), "MIXED"),
            (mk(tp_tp=20, fp_fp=20, tp_fp=16, fp_tp=16), "SYMMETRIC"),
        ):
            a = ag.admissibility(pairs)
            self.assertEqual(a["band"], expected)
            # Case-insensitive: the prose reads "kappa undefined ...", the band is
            # "UNDEFINED". They agree in substance, which is what must not drift. Forcing
            # the band's casing into the sentence would damage the human-readable half to
            # satisfy the machine-readable one.
            if a["cause"]:                       # cause is None only when ADMISSIBLE
                self.assertIn(expected.lower(), a["cause"].lower(),
                              f"band {a['band']} disagrees with cause {a['cause']!r}")

    def test_a101_real_labels_carry_the_mixed_band(self):
        """Ground truth again: the real corpus must land in MIXED, not just say so in prose."""
        if not A101.exists():
            self.skipTest("a101.raters.json not present")
        rows = json.loads(A101.read_text())["rows"]
        pairs = [(r.get("codex", "uncertain"), r.get("deepseek", "uncertain")) for r in rows]
        self.assertEqual(ag.admissibility(pairs)["band"], "MIXED")

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
        # BOTH statistics pinned. The floor gates on Scott's pi because 0.667 is
        # Krippendorff's bound and alpha ~= pi for two raters on nominal data; Cohen's kappa
        # is still reported for comparability with every artifact recorded before the switch.
        # Cohen uses each rater's own marginals and so credits systematic BIAS as agreement,
        # making it the more lenient statistic against this threshold -- here by +0.0257.
        # Pinning both is what makes that gap visible instead of a silent swap under a reader.
        self.assertEqual(a["statistic"], "scott_pi")
        self.assertAlmostEqual(a["kappa"], 0.0800, places=4)          # Scott's pi — gated on
        self.assertAlmostEqual(a["cohen_kappa"], 0.1057, places=4)    # as recorded in a101.json
        self.assertGreater(a["cohen_kappa"], a["kappa"],
                           "Cohen must be the more lenient statistic here; if not, the "
                           "pooled-vs-separate marginal distinction has been broken")
        self.assertAlmostEqual(ag.pct_agreement(pairs), 0.5772, places=4)
        self.assertFalse(a["admissible"])
        self.assertEqual(a["n_disagreements"], 52)
        self.assertIn("MIXED", a["cause"])   # skew 0.385 -- a 2.25:1 lean, not scatter


class UncoveredByMutationScore(unittest.TestCase):
    """wilson_ci and concordance had ZERO coverage until cosmic-ray said so.

    My hand-picked mutation set scored 7/7 on this module. cosmic-ray 8.7.0 over 716
    generated mutants scored 377 KILLED / 339 SURVIVED = 52.7%, and 256 of the 339 survivors
    (75%) sat on lines 165-183 — wilson_ci and concordance — where EVERY mutant survived
    (114/114, 57/57, 35/35, 26/26). The suite never called either function.

    The lesson is not "I missed two functions". It is that I mutation-tested THE DIFF, not
    the MODULE: I generated mutants only for code I had just written, so the score measured
    my attention rather than the suite's coverage. A curated mutant set cannot find a
    function you forgot exists.

    wilson_ci is not incidental — verdict.py prints its output as the Wilson 95%% CI beside
    friction_rate, so an untested statistical function was feeding a reported interval.
    Values below are the published Wilson score interval, not this implementation's output:
    computing expectations FROM the code under test would assert only that it is
    self-consistent.
    """

    def test_wilson_ci_matches_published_values(self):
        lo, hi = ag.wilson_ci(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)   # published Wilson 95% for 5/10
        self.assertAlmostEqual(hi, 0.7634, places=3)
        lo0, hi0 = ag.wilson_ci(0, 10)
        self.assertAlmostEqual(lo0, 0.0, places=3)     # zero successes: lower bound pinned
        self.assertAlmostEqual(hi0, 0.2775, places=3)

    def test_wilson_ci_is_bounded_and_ordered(self):
        """Edge cases: bounds must stay in [0,1] and lo<=hi at the extremes."""
        for k, n in ((0, 1), (1, 1), (0, 1000), (1000, 1000), (1, 3)):
            lo, hi = ag.wilson_ci(k, n)
            self.assertLessEqual(0.0, lo)
            self.assertLessEqual(lo, hi)
            self.assertLessEqual(hi, 1.0)

    def test_wilson_ci_zero_n_is_none_not_a_number(self):
        """n=0 must not yield a CI. Returning one would be an interval over no data."""
        self.assertIsNone(ag.wilson_ci(0, 0))

    def test_concordance_counts_only_checkable_pairs(self):
        r = ag.concordance(["TP", "FP", "TP"], ["TP", "FP", "NA"])
        self.assertEqual(r["n_checkable"], 2)      # the NA pair is excluded
        self.assertEqual(r["concordant"], 2)
        self.assertAlmostEqual(r["rate"], 1.0)

    def test_concordance_disagreement_lowers_the_rate(self):
        """The discriminating half: agreement and disagreement must differ."""
        r = ag.concordance(["TP", "FP"], ["TP", "TP"])
        self.assertEqual(r["n_checkable"], 2)
        self.assertEqual(r["concordant"], 1)
        self.assertAlmostEqual(r["rate"], 0.5)

    def test_concordance_no_checkable_pairs_is_none_rate(self):
        r = ag.concordance(["TP", "FP"], ["NA", "NA"])
        self.assertEqual(r["n_checkable"], 0)
        self.assertIsNone(r["rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
