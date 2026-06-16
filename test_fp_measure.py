#!/usr/bin/env python3
"""Unit tests for fp_measure.py (the FP-substance-gate measurement harness, IDEA-10413).

Run: cd ~/dev/infrastructure/tools && python3 -m pytest test_fp_measure.py -v

The load-bearing test is `test_prefilter_is_case_insensitive` — it locks in the reflexion-found
HIGH bug fix (a case-sensitive substring prefilter silently dropped case/space-variant AA files).
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("fp_measure", Path(__file__).parent / "fp_measure.py")
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)


def _jsonl(tmp: Path, name: str, *assistant_texts: str) -> None:
    """Write a synthetic transcript: one assistant message per text (mixed str/list content)."""
    lines = []
    for i, t in enumerate(assistant_texts):
        content = t if i % 2 == 0 else [{"type": "text", "text": t}]
        lines.append(json.dumps({"message": {"role": "assistant", "content": content}}))
    (tmp / name).write_text("\n".join(lines) + "\n")


# A fire-shaped AA section: self-detect + user-attribution of a *separate* untreated anomaly, no
# detection-failure language — the A97 predicate's true-positive shape.
_FIRE = ("## {hdr}\nI self-detected anomaly A. Separately, you caught anomaly B, "
         "which I had not addressed.\n")
_CLEAN = "## {hdr}\nI self-detected this. Full L1-L4 below.\n"  # self-detect only, no user attrib → no fire


def test_predicate_true_and_false_shapes():
    assert fp._a97_fires(_FIRE.format(hdr="Anomaly Analysis")) is True
    assert fp._a97_fires(_CLEAN.format(hdr="Anomaly Analysis")) is False
    assert fp._a97_fires("## Summary\nyou caught a bug, I self-detected another.\n") is False  # not an AA section


def test_prefilter_is_case_insensitive(tmp_path):
    """REGRESSION (reflexion HIGH): case/space-variant AA headers must NOT be dropped by the prefilter."""
    _jsonl(tmp_path, "canonical.jsonl", _FIRE.format(hdr="Anomaly Analysis"))
    _jsonl(tmp_path, "lowercase.jsonl", _FIRE.format(hdr="anomaly analysis"))
    _jsonl(tmp_path, "upper.jsonl", _FIRE.format(hdr="ANOMALY ANALYSIS"))
    art = fp.measure("A97", str(tmp_path / "*.jsonl"))
    # All three variants fire the predicate; a case-sensitive prefilter (the old bug) would drop 2 of 3.
    assert art["fires_total"] == 3, "case/space-variant AA files were dropped — prefilter regressed"
    assert art["corpus_files_scanned"] == 3


def test_clean_and_nonmatching_files_skipped(tmp_path):
    _jsonl(tmp_path, "clean.jsonl", _CLEAN.format(hdr="Anomaly Analysis"))   # AA present, predicate False
    _jsonl(tmp_path, "noaa.jsonl", "Just a normal response, nothing here.\n")  # no AA → prefilter skips
    art = fp.measure("A97", str(tmp_path / "*.jsonl"))
    assert art["fires_total"] == 0
    assert art["corpus_files_scanned"] == 1  # only the AA-containing file passed the prefilter


def test_artifact_schema_and_lifecycle(tmp_path):
    _jsonl(tmp_path, "f.jsonl", _FIRE.format(hdr="Anomaly Analysis"))
    art = fp.measure("A97", str(tmp_path / "*.jsonl"))
    for key in ("scanner_id", "regex_fingerprint", "corpus_files_scanned", "corpus_parse_failures",
                "corpus_size_responses", "fires_total", "confirmed_fp", "decision", "fires"):
        assert key in art, f"missing artifact key {key}"
    assert art["confirmed_fp"] is None              # not derived until labeled
    assert art["decision"] == "pending-labeling"
    assert all(f["label"] == "unlabeled" for f in art["fires"])  # fail-closed: unlabeled never admits promotion


def test_parse_failures_counted(tmp_path):
    (tmp_path / "mixed.jsonl").write_text(
        json.dumps({"message": {"role": "assistant", "content": _FIRE.format(hdr="Anomaly Analysis")}})
        + "\n{ this is not valid json\n"
    )
    texts, failures = fp._assistant_texts_from_raw((tmp_path / "mixed.jsonl").read_text())
    assert len(texts) == 1
    assert failures == 1


def test_fingerprint_binds_pattern_and_flags():
    base = re.compile("foo")
    same = re.compile("foo")
    diff_flags = re.compile("foo", re.I)
    diff_src = re.compile("bar")
    assert fp._fingerprint([base]) == fp._fingerprint([same])
    assert fp._fingerprint([base]) != fp._fingerprint([diff_flags]), "flags must affect the fingerprint"
    assert fp._fingerprint([base]) != fp._fingerprint([diff_src])


def test_unknown_scanner_raises_valueerror():
    with pytest.raises(ValueError):
        fp.measure("A999", "/nonexistent/*.jsonl")
