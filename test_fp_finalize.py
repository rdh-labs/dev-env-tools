#!/usr/bin/env python3
"""Tests for fp_measure.finalize — the FP-gate finalize step (re-derive fires_labeled +
confirmed_fp from inline labels so evidence_gate._fp_artifact_admits_promotion can admit).

Runnable standalone (`python3 test_fp_finalize.py`) and pytest-discoverable. Self-contained:
monkeypatches fp_measure.ARTIFACT_DIR to a temp dir; never mutates real ~/.claude/logs state
(the real A97.json schema is checked on a COPY).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import fp_measure


def _gate_admits(art: dict) -> bool:
    """Replica of evidence_gate._fp_artifact_admits_promotion's admit conditions
    (evidence_gate.py:682-695) — the consumer this finalizer feeds."""
    total, labeled, cfp = art.get("fires_total"), art.get("fires_labeled"), art.get("confirmed_fp")
    if not isinstance(total, int) or not isinstance(labeled, int):
        return False
    if total == 0:
        return False
    if labeled != total:
        return False
    if not isinstance(cfp, int) or isinstance(cfp, bool) or cfp != 0:
        return False
    return True


def _artifact(fires_labels: list[str]) -> dict:
    return {
        "scanner_id": "A99001", "schema_v": 1, "fires_total": len(fires_labels),
        "fires_labeled": 0, "confirmed_fp": None, "decision": "pending-labeling",
        "fires": [{"excerpt": f"fire {i}", "source": "x.jsonl", "label": lab, "rationale": ""}
                  for i, lab in enumerate(fires_labels)],
    }


def _run(tmp: Path, scanner_id: str, art: dict) -> dict:
    (tmp / f"{scanner_id}.json").write_text(json.dumps(art))
    return fp_measure.finalize(scanner_id)


def run_all() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="fp-finalize-test-"))
    fp_measure.ARTIFACT_DIR = tmp  # monkeypatch the module global
    checks = 0

    # 1. Fully-labeled, zero FP → confirmed_fp==0, fully labeled, ready, GATE ADMITS.
    art = _artifact(["TP", "TP", "TP"])
    s = _run(tmp, "A99001", art)
    final = json.loads((tmp / "A99001.json").read_text())
    assert s["fires_labeled"] == 3 and s["confirmed_fp"] == 0, s
    assert s["newly_ready"] is True, s
    assert _gate_admits(final) is True, "ready artifact must admit promotion"
    checks += 1

    # 2. One FP → confirmed_fp==1, fully labeled, NOT ready, GATE HOLDS.
    s = _run(tmp, "A99001", _artifact(["TP", "FP", "TP"]))
    final = json.loads((tmp / "A99001.json").read_text())
    assert s["confirmed_fp"] == 1 and s["newly_ready"] is False, s
    assert _gate_admits(final) is False, "FP-present artifact must NOT admit"
    checks += 1

    # 3. One unlabeled → incomplete, confirmed_fp None, GATE HOLDS (no silent admit).
    s = _run(tmp, "A99001", _artifact(["TP", "unlabeled", "TP"]))
    final = json.loads((tmp / "A99001.json").read_text())
    assert s["fires_labeled"] == 2 and s["confirmed_fp"] is None, s
    assert _gate_admits(final) is False, "partially-labeled artifact must NOT admit"
    checks += 1

    # 4. 'uncertain' counts as NOT-resolved → holds (conservative polarity).
    s = _run(tmp, "A99001", _artifact(["TP", "uncertain"]))
    assert s["fires_labeled"] == 1 and s["confirmed_fp"] is None, s
    checks += 1

    # 5. Zero fires → never admits (vacuous-truth guard mirrors the gate).
    s = _run(tmp, "A99001", _artifact([]))
    final = json.loads((tmp / "A99001.json").read_text())
    assert s["newly_ready"] is False and _gate_admits(final) is False, s
    checks += 1

    # 6. Idempotency: finalize twice → identical derived fields.
    a = _run(tmp, "A99001", _artifact(["TP", "FP"]))
    b = fp_measure.finalize("A99001")
    assert (a["fires_labeled"], a["confirmed_fp"]) == (b["fires_labeled"], b["confirmed_fp"]), (a, b)
    checks += 1

    # 7. Fail-loud: malformed artifact (fires not a list) RAISES, never silently writes.
    (tmp / "A99002.json").write_text(json.dumps({"scanner_id": "A99002", "fires": "oops"}))
    try:
        fp_measure.finalize("A99002")
        raise AssertionError("malformed artifact must raise")
    except ValueError:
        pass
    checks += 1

    # 8. Fail-loud: missing artifact RAISES FileNotFoundError.
    try:
        fp_measure.finalize("A_nope")
        raise AssertionError("missing artifact must raise")
    except FileNotFoundError:
        pass
    checks += 1

    # 9. finalize_all: catches the malformed one as an error row, does NOT abort the batch.
    results = fp_measure.finalize_all()
    by_id = {r["scanner_id"]: r for r in results}
    assert "error" in by_id.get("A99002", {}), "malformed artifact must surface as an error row"
    assert by_id.get("A99001", {}).get("confirmed_fp") == 1, "good artifact still finalized in batch"
    checks += 1

    # 9b. newly_ready is a TRANSITION, not steady state (cron must not re-notify a ready artifact).
    s1 = _run(tmp, "A99003", _artifact(["TP", "TP"]))  # not-ready fixture → ready: transition
    assert s1["newly_ready"] is True, "first finalize-to-ready must signal newly_ready"
    s2 = fp_measure.finalize("A99003")                 # already ready → no transition
    assert s2["newly_ready"] is False, "re-finalize of an already-ready artifact must NOT re-notify"
    checks += 1

    # 9c. scanner_id path-traversal is refused (fail-loud), not silently resolved outside the dir.
    try:
        fp_measure.finalize("../../etc/passwd")
        raise AssertionError("path-traversal scanner_id must raise")
    except ValueError:
        pass
    checks += 1

    # 10. Real-schema compatibility: finalize a COPY of the live A97.json without error.
    real = Path.home() / ".claude" / "logs" / "fp-gate" / "A97.json"
    if real.exists():
        shutil.copy(real, tmp / "A97.json")
        s = fp_measure.finalize("A97")
        assert isinstance(s["fires_total"], int) and "decision" in s, s
        checks += 1
        print(f"   [info] real A97.json: {s['fires_labeled']}/{s['fires_total']} resolved, "
              f"confirmed_fp={s['confirmed_fp']}")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"PASS — {checks} checks (gate-admit transition, fail-loud, idempotency, real schema)")


def test_run_all() -> None:
    """Expose run_all() to pytest collection.

    Without this, pytest imports this module, finds no `test_*` item, and
    reports success having run none of the 12 checks — the file is tracked,
    named test_*.py, and silently unexecuted in CI. run_all() asserts, so a
    real failure propagates.
    """
    run_all()


if __name__ == "__main__":
    run_all()
