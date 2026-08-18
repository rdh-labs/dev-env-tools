#!/usr/bin/env python3
"""Edge-case + falsifiability tests for fp_measure.py's EVIDENCE layer (2026-08-18).

Run: cd ~/dev/infrastructure/tools && python3 -m pytest test_fp_evidence.py -v

WHAT THIS GUARDS. fp_measure stored `text.strip()[:600]` -- a HEAD excerpt -- while every
registered scanner matches on the response TAIL or on a mid-body section. The stored evidence
therefore could not contain the match, and no label was verifiable from the artifact. a101 had
95 reviewer labels of which only 3 excerpts contained a `You:` line, and a live promotion
verdict (`hold`, confirmed_fp=16) rested on them.

THE LOAD-BEARING TEST is `test_invariant_is_not_vacuous_reverting_the_fix_fails`: it rebuilds
the OLD head-excerpt behaviour and asserts the invariant REJECTS it. Without that test the
whole file could pass against an invariant that never raises. Every expectation below was
verified by RUNNING the real predicate first (pinned in `test_a14c_firing_shapes_are_empirical`)
rather than asserted from reading the regex -- the sibling tool `you-token-check` records what
happens otherwise: "My own first edge-case battery LABELLED the Nil-check fire as expected --
the wrong expectation was baked into the probe, and only re-reading the output caught it."
"""
import importlib.util
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("EVIDENCE_GATE_NO_LOG", "1")
_spec = importlib.util.spec_from_file_location("fp_measure", Path(__file__).parent / "fp_measure.py")
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)


def _jsonl(tmp: Path, name: str, *texts: str) -> None:
    lines = [json.dumps({"message": {"role": "assistant", "content": t}}) for t in texts]
    (tmp / name).write_text("\n".join(lines) + "\n")


# A response whose HEAD and TAIL are distinguishable. The head sentinel must NEVER appear in
# stored evidence; the tail value must ALWAYS appear.
_HEAD = "HEADSENTINEL this is the opening of the response. " + ("filler word " * 80)
_TAIL_FIRES = "\n\nDone: work\nOpen: Nothing\nYou: [action] Nothing - complete\n"
_TAIL_CLEAN = "\n\nDone: work\nOpen: Nothing\nYou: [decision] Approve A or B\n"


# ── The empirical ground truth every other expectation depends on ───────────
def test_a14c_firing_shapes_are_empirical():
    """Pin the ACTUAL predicate behaviour. If this drifts, every expectation below is void."""
    assert fp._a14c_fires("You: [action] Nothing - complete") is True
    assert fp._a14c_fires("You: [approval] Nothing further") is True
    assert fp._a14c_fires("You: [action] None right now") is True       # recall fix, 2026-08-18
    assert fp._a14c_fires("You: [decision] Approve the plan or pick option B") is False
    assert fp._a14c_fires("You: Nothing - agent handles next") is False  # no token => no contradiction
    assert fp._a14c_fires("You: [question] Which repo?") is False


# ── THE regression: evidence must be the match, not the head ────────────────
def test_excerpt_contains_the_match_not_the_head(tmp_path):
    _jsonl(tmp_path, "s.jsonl", _HEAD + _TAIL_FIRES)
    art = fp.measure("a14c", str(tmp_path / "*.jsonl"))
    assert art["fires_total"] == 1
    f = art["fires"][0]
    assert "[action] Nothing" in f["excerpt"], "stored evidence does not contain the match"
    assert "HEADSENTINEL" not in f["excerpt"], "head excerpt regressed into the evidence field"
    assert f["matched"] == ["[action] Nothing - complete"]
    assert f["evidence_kind"] == "matched-span"


def test_a14c_stores_only_the_firing_you_line(tmp_path):
    """A response with a clean You: AND a firing You: must evidence only the one that fired."""
    text = _HEAD + "\n\nYou: [decision] Approve A or B\n\n---\n\nYou: [action] Nothing - complete\n"
    _jsonl(tmp_path, "s.jsonl", text)
    art = fp.measure("a14c", str(tmp_path / "*.jsonl"))
    assert art["fires"][0]["matched"] == ["[action] Nothing - complete"]
    assert "Approve A or B" not in art["fires"][0]["excerpt"]


def test_clean_tail_does_not_fire(tmp_path):
    _jsonl(tmp_path, "s.jsonl", _HEAD + _TAIL_CLEAN)
    art = fp.measure("a14c", str(tmp_path / "*.jsonl"))
    assert art["fires_total"] == 0


# ── FALSIFIABILITY CONTROL — the load-bearing test ──────────────────────────
def test_invariant_is_not_vacuous_reverting_the_fix_fails():
    """Rebuild the PRE-FIX artifact shape and assert the invariant rejects it.

    Without this, every other assertion in this file would still pass against an invariant
    that never raises. A suite that cannot fail measures nothing -- the 'no-op score' this
    workspace measures elsewhere."""
    reverted = {"fires": [{
        "matched": ["[action] Nothing - complete"],
        "excerpt": _HEAD[:600].replace("\n", " "),      # the OLD behaviour, verbatim
        "source": "s.jsonl", "label": "unlabeled", "rationale": "",
    }]}
    with pytest.raises(RuntimeError, match="REGRESSED"):
        fp._assert_evidence_invariant(reverted)


def test_invariant_rejects_evidence_free_fire():
    with pytest.raises(RuntimeError, match="no matched evidence"):
        fp._assert_evidence_invariant({"fires": [{"matched": [], "excerpt": "x", "source": "s"}]})


def test_invariant_rejects_whitespace_only_evidence():
    """`matched: ["   "]` is a non-empty list of nothing -- must not pass as evidence."""
    with pytest.raises(RuntimeError, match="no matched evidence"):
        fp._assert_evidence_invariant({"fires": [{"matched": ["   "], "excerpt": "   ", "source": "s"}]})


def test_invariant_accepts_correct_evidence():
    """Negative control FOR the control: the invariant must PASS on valid input, else the
    three tests above prove only that it always raises."""
    fp._assert_evidence_invariant({"fires": [{
        "matched": ["[action] Nothing"], "excerpt": "[action] Nothing", "source": "s"}]})


# ── Drift + registration: no silent head-excerpt fallback ───────────────────
def test_unregistered_scanner_refuses_to_measure(monkeypatch):
    """A scanner with a predicate but no extractor must RAISE, never fall back to a head
    excerpt. An unauditable artifact is worse than none: it looks like substance to a gate
    that only counts labels."""
    monkeypatch.setitem(fp.SCANNER_PREDICATES, "zz_noextractor",
                        (lambda t: True, [fp.evidence_gate.YOU_FIELD_VALUE_RE], None))
    with pytest.raises(ValueError, match="no evidence extractor registered"):
        fp.measure("zz_noextractor", "/nonexistent/*.jsonl")


def test_predicate_extractor_drift_raises(tmp_path, monkeypatch):
    """Predicate fires, extractor returns nothing => they have DRIFTED. Fail the run."""
    _jsonl(tmp_path, "s.jsonl", _HEAD + _TAIL_FIRES)
    monkeypatch.setitem(fp.EVIDENCE_EXTRACTORS, "a14c", (lambda t: [], "matched-span"))
    with pytest.raises(RuntimeError, match="DRIFTED"):
        fp.measure("a14c", str(tmp_path / "*.jsonl"))


# ── Edge cases ──────────────────────────────────────────────────────────────
def test_multiline_evidence_survives_normalization():
    """A97's evidence is a MULTI-LINE section. A newline mismatch between the excerpt builder
    and the invariant checker would fail on VALID input -- the check being wrong, not the
    data. This pins the shared normalizer."""
    section = "## Anomaly Analysis\nline one\nline two\nline three"
    exc = fp._excerpt_with_evidence([section])
    assert "\n" not in exc
    fp._assert_evidence_invariant({"fires": [{"matched": [section], "excerpt": exc, "source": "s"}]})


def test_span_longer_than_cap_still_satisfies_invariant():
    """Evidence longer than EXCERPT_CAP is legitimately truncated; the invariant compares a
    prefix, so truncation must not read as regression."""
    long_span = "You value " + ("x" * 5000)
    exc = fp._excerpt_with_evidence([long_span])
    assert len(exc) == fp.EXCERPT_CAP
    fp._assert_evidence_invariant({"fires": [{"matched": [long_span], "excerpt": exc, "source": "s"}]})


def test_unicode_and_separator_safe():
    """Em-dashes and the ' || ' joiner must not break containment."""
    spans = ["[action] Nothing — complete", "[approval] Nothing further"]
    exc = fp._excerpt_with_evidence(spans)
    fp._assert_evidence_invariant({"fires": [{"matched": spans, "excerpt": exc, "source": "s"}]})


# ── Label preservation across a re-measure ──────────────────────────────────
def test_labels_carry_across_remeasure(tmp_path):
    """A re-measure must NOT reset reviewer labels. The pre-fix write path was a bare
    write_text that would have silently erased a101's 95 labels and 16 confirmed FPs."""
    corpus = tmp_path / "c"
    corpus.mkdir()
    _jsonl(corpus, "s.jsonl", _HEAD + _TAIL_FIRES)
    art1 = fp.measure("a14c", str(corpus / "*.jsonl"))
    art1["fires"][0]["label"] = "TP"
    art1["fires"][0]["rationale"] = "adjudicated by hand"
    path = tmp_path / "a14c.json"
    path.write_text(json.dumps(art1))

    art2 = fp.measure("a14c", str(corpus / "*.jsonl"))
    stats = fp._carry_labels(art2, path)
    assert stats["carried"] == 1 and stats["prior_labeled"] == 1 and stats["orphaned"] == 0
    assert art2["fires"][0]["label"] == "TP"
    assert art2["fires"][0]["rationale"] == "adjudicated by hand"
    assert art2["fires"][0]["label_provenance"] == "pre-evidence-fix", \
        "carried labels must be distinguishable from audit-clean ones"


def test_orphaned_label_is_counted_not_silent(tmp_path):
    """A previously-labeled fire that no longer reproduces is a real event (predicate change
    or corpus drift) and must be COUNTED, never silently dropped."""
    corpus = tmp_path / "c"
    corpus.mkdir()
    _jsonl(corpus, "s.jsonl", _HEAD + _TAIL_FIRES)
    art_new = fp.measure("a14c", str(corpus / "*.jsonl"))
    stale = {"fires": [{"source": "gone.jsonl", "legacy_key": "vanished", "excerpt": "vanished",
                        "label": "FP", "rationale": "was here"}]}
    path = tmp_path / "a14c.json"
    path.write_text(json.dumps(stale))
    stats = fp._carry_labels(art_new, path)
    assert stats["orphaned"] == 1 and stats["carried"] == 0 and stats["fresh"] == 1


def test_unreadable_artifact_refuses_overwrite(tmp_path):
    """Silently overwriting an unreadable artifact would destroy labels we could not read
    back first. Refuse, loudly."""
    path = tmp_path / "a14c.json"
    path.write_text("{ not valid json")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        fp._carry_labels({"fires": []}, path)


def test_missing_artifact_is_all_fresh(tmp_path):
    stats = fp._carry_labels({"fires": [{"source": "a", "legacy_key": "k"}]}, tmp_path / "absent.json")
    assert stats == {"carried": 0, "fresh": 1, "orphaned": 0, "prior_labeled": 0}


def test_duplicate_keys_are_positional_not_broadcast(tmp_path):
    """Two identical responses in one session must not both inherit the FIRST label."""
    old = {"fires": [
        {"source": "s.jsonl", "legacy_key": "same", "label": "TP", "rationale": "first"},
        {"source": "s.jsonl", "legacy_key": "same", "label": "FP", "rationale": "second"},
    ]}
    path = tmp_path / "a.json"
    path.write_text(json.dumps(old))
    new = {"fires": [{"source": "s.jsonl", "legacy_key": "same"},
                     {"source": "s.jsonl", "legacy_key": "same"}]}
    stats = fp._carry_labels(new, path)
    assert stats["carried"] == 2
    assert [f["label"] for f in new["fires"]] == ["TP", "FP"], "labels were broadcast, not paired"


def test_unlabeled_prior_does_not_count_as_carried(tmp_path):
    old = {"fires": [{"source": "s.jsonl", "legacy_key": "k", "label": "unlabeled"}]}
    path = tmp_path / "a.json"
    path.write_text(json.dumps(old))
    new = {"fires": [{"source": "s.jsonl", "legacy_key": "k"}]}
    stats = fp._carry_labels(new, path)
    assert stats == {"carried": 0, "fresh": 1, "orphaned": 0, "prior_labeled": 0}
