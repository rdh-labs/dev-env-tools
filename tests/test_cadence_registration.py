#!/usr/bin/env python3
"""Every wrapped scheduled check in the live crontab is registered in EXPECTED_CADENCE_H.

OBJECTIVE:   Tier 1 "System Catches Own Errors": a scheduled check that is live in cron but absent from
             governed-outcomes-check.py's EXPECTED_CADENCE_H is never audited for going silent (found for
             codex-sandbox-canary, session 6cdb3561, fixed in tools 432ebbf).
SUCCESS:     no "in the live schedule but absent from EXPECTED_CADENCE_H" drift; codex-sandbox-canary = 24.
FAILURE:     a live wrapped check with no cadence entry; or the mutant control below is NOT detected.
TRIGGERS:    date: nightly via the ratchet-tools-tests cron (suite-ratchet); event: none (crontab edits have
             no producer hook here; see RESIDUALS.md, runner self-registration check).
predicate-rung: sufficiency (compares the live crontab population to the table, not a token's presence)
"""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

GOC = Path(__file__).resolve().parent.parent / "governed-outcomes-check.py"
LIVE_ABSENT = "in the live schedule but absent from EXPECTED_CADENCE_H"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("goc_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _unregistered(mod) -> list[str]:
    drift = mod.outcome_marker_cannot_fire().get("registration_drift") or []
    return [d for d in drift if LIVE_ABSENT in d]


class CadenceRegistration(unittest.TestCase):
    def test_canary_entry(self):
        self.assertEqual(_load(GOC).EXPECTED_CADENCE_H.get("codex-sandbox-canary"), 24)

    def test_no_live_check_unregistered(self):
        mod = _load(GOC)
        entries, errs = mod._live_scheduled_checks(None)
        # Unreadable or empty cron is a FAILURE here, never a skip: this suite runs on the host that owns the crontab.
        self.assertEqual(errs, [], f"crontab parse errors: {errs}")
        self.assertTrue(entries, "no wrapped scheduled entries read; the check examined nothing")
        self.assertEqual(_unregistered(mod), [])

    def test_mutant_is_detected(self):
        """Negative control: the same predicate must flag a table with the canary line removed."""
        mod = _load(GOC)
        entries, _errs = mod._live_scheduled_checks(None)
        self.assertTrue(any(e["name"] == "codex-sandbox-canary" for e in entries),
                        "codex-sandbox-canary is not in the live crontab; the control cannot run")
        src = GOC.read_text()
        mutated = re.sub(r'^\s*"codex-sandbox-canary": 24,\n', "", src, flags=re.M)
        self.assertNotEqual(src, mutated, "mutant anchor not found; the control proves nothing")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "goc_mutant.py"
            p.write_text(mutated)
            compile(mutated, str(p), "exec")
            flagged = _unregistered(_load(p))
        self.assertTrue(any(d.startswith("codex-sandbox-canary:") for d in flagged))


if __name__ == "__main__":
    unittest.main()
