# GUARDS: fp_measure.py
"""fp_measure prefilter audit (2026-10-10, session 780cdf81).

A prefilter skips files that lack a substring, on the claim that the substring is a NECESSARY
condition of firing. The a14h prefilter "you:" is not necessary per line ("**You**:" fires), so
fp_measure now parses every Nth skipped file and refuses to write when the predicate fires there.

Cases: the hidden fire is refused (positive witness: the RuntimeError names the prefilter); the
visible fire is written and counted (negative control); with the audit sampled too sparsely to
reach the file, the same hidden fire is silently LOST (mutant witness: fires_total == 0 while the
file holds a fire), which is the undercount the audit exists to catch.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
os.environ.setdefault("EVIDENCE_GATE_NO_LOG", "1")
import fp_measure as F  # noqa: E402

BODY = ("Body text explaining the change in enough words to pass the minimum length check, "
        "padding padding padding padding padding padding padding padding padding padding.\n\n"
        "Done: changed the file\nOpen: Nothing\n")


def _corpus(tmp: Path, you_line: str) -> str:
    rows = [{"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": BODY + you_line}]}}]
    (tmp / "t.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(tmp / "*.jsonl")


def test_hidden_fire_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_PREFILTER_AUDIT_EVERY", "1")
    glob_ = _corpus(tmp_path, "**You**: Nothing. If you want more, say so.")
    with pytest.raises(RuntimeError, match=r"prefilter 'you:' is NOT a necessary condition"):
        F.measure("a14h", glob_)


def test_visible_fire_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_PREFILTER_AUDIT_EVERY", "1")
    art = F.measure("a14h", _corpus(tmp_path, "You: Nothing. If you want more, say so."))
    assert art["fires_total"] == 1
    assert art["corpus_files_prefiltered"] == 0
    assert art["prefilter"] == "you:"
    assert art["prefilter_audit"]["mode"] == "none"   # nothing skipped: no completeness claim
    assert art["corpus_files_total"] == (art["corpus_files_scanned"] + art["corpus_files_prefiltered"]
                                         + art["corpus_read_errors"])


def test_mutant_unaudited_skip_loses_the_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_PREFILTER_AUDIT_EVERY", "1000")
    glob_ = _corpus(tmp_path, "**You**: Nothing. If you want more, say so.")
    predicate = F.SCANNER_PREDICATES["a14h"][0]
    text = BODY + "**You**: Nothing. If you want more, say so."
    assert predicate(text), "fixture must fire, or this mutant proves nothing"
    art = F.measure("a14h", glob_)
    assert art["fires_total"] == 0 and art["corpus_files_prefiltered"] == 1
    assert art["prefilter_audit"]["files_audited"] == 0
    assert art["prefilter_audit"]["mode"] == "sampled"   # the artifact must not claim a proof


def test_complete_audit_is_labelled_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_PREFILTER_AUDIT_EVERY", "1")
    (tmp_path / "t.jsonl").write_text(json.dumps(
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "no tail"}]}}) + "\n")
    art = F.measure("a14h", str(tmp_path / "*.jsonl"))
    assert art["corpus_files_prefiltered"] == 1
    assert art["prefilter_audit"] == {"mode": "complete", "every_nth_skipped": 1, "files_audited": 1,
                                      "fires_in_skipped": 0, "parse_failures_in_audited": 0}


def test_unparseable_audited_record_is_not_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("FP_PREFILTER_AUDIT_EVERY", "1")
    good = json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": "no tail"}]}})
    (tmp_path / "t.jsonl").write_text(good + '\n{"type": "assistant", "message": BROKEN\n')
    art = F.measure("a14h", str(tmp_path / "*.jsonl"))
    assert art["prefilter_audit"]["parse_failures_in_audited"] >= 1, "fixture must produce a parse failure"
    assert art["prefilter_audit"]["mode"] == "complete_with_parse_failures"


@pytest.mark.parametrize("bad", ["0", "-3", "ten"])
def test_bad_audit_interval_names_the_variable(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("FP_PREFILTER_AUDIT_EVERY", bad)
    with pytest.raises(RuntimeError, match="FP_PREFILTER_AUDIT_EVERY must be a positive integer"):
        F.measure("a14h", _corpus(tmp_path, "You: Nothing."))
