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
    assert stats["downgraded"] == 0, "a v2 source is auditable — nothing should be reset"
    assert art2["fires"][0]["label"] == "TP"
    assert art2["fires"][0]["rationale"] == "adjudicated by hand"
    # The source here was written by fp.measure(), so it IS schema_v=2 with matched-span
    # evidence — audit-clean. (Before 2026-08-18 this asserted "pre-evidence-fix", which
    # encoded the laundering bug: it stamped EVERY carried label as pre-fix regardless of
    # the source, and nothing read the stamp anyway.)
    assert art2["fires"][0]["label_provenance"] == "carried-auditable"


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
    assert stats == {"carried": 0, "fresh": 1, "orphaned": 0, "prior_labeled": 0, "downgraded": 0}


def test_duplicate_keys_are_positional_not_broadcast(tmp_path):
    """Two identical responses in one session must not both inherit the FIRST label."""
    # Source must be AUDITABLE (v2), else the labels are correctly reset before pairing can
    # even be observed — the property under test here is pairing, not provenance.
    old = {"schema_v": 2, "evidence_kind": "matched-span", "fires": [
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
    old = {"schema_v": 2, "evidence_kind": "matched-span",
           "fires": [{"source": "s.jsonl", "legacy_key": "k", "label": "unlabeled"}]}
    path = tmp_path / "a.json"
    path.write_text(json.dumps(old))
    new = {"fires": [{"source": "s.jsonl", "legacy_key": "k"}]}
    stats = fp._carry_labels(new, path)
    assert stats == {"carried": 0, "fresh": 1, "orphaned": 0, "prior_labeled": 0, "downgraded": 0}


# ── label laundering: the CRITICAL an adversarial review found (2026-08-18) ──
# _carry_labels originally carried v1 labels forward stamped "pre-evidence-fix" — and
# NOTHING read that field. So `re-measure v1 -> carry -> finalize` produced a schema_v=2
# artifact with evidence_kind set, and the gate ADMITTED on labels every one of which was
# formed on a head excerpt. The migration walked around the consumer check it was paired
# with. These tests exist so that hole cannot silently reopen.

def _prior(schema_v, kind, label="TP"):
    a = {"schema_v": schema_v, "fires": [{"matched": ["[action] Nothing"],
         "excerpt": "[action] Nothing", "legacy_key": "K", "source": "s.jsonl",
         "label": label, "rationale": "hand-adjudicated"}]}
    if kind is not None:
        a["evidence_kind"] = kind
    return a


def _fresh():
    return {"fires": [{"matched": ["[action] Nothing"], "excerpt": "[action] Nothing",
                       "legacy_key": "K", "source": "s.jsonl"}]}


@pytest.mark.parametrize("schema_v,kind", [
    (1, None), (1, "matched-span"), (2, "head-excerpt"), (2, None), ("two", "matched-span"),
])
def test_prefix_labels_are_reset_not_carried(tmp_path, schema_v, kind):
    """A label formed on evidence that could not show the match must NOT survive into a v2
    artifact — that is laundering. The reviewer's WORK is preserved; the LABEL resets."""
    path = tmp_path / "a.json"
    path.write_text(json.dumps(_prior(schema_v, kind)))
    new = _fresh()
    stats = fp._carry_labels(new, path)
    f = new["fires"][0]
    assert f["label"] == "unlabeled", "a pre-fix label was carried into a v2 artifact"
    assert f["label_provenance"] == "reset-was-pre-evidence-fix"
    assert f["prior_label"] == "TP" and f["prior_rationale"] == "hand-adjudicated", \
        "reviewer work must be preserved, not discarded"
    assert stats["downgraded"] == 1 and stats["carried"] == 0


@pytest.mark.parametrize("kind", ["matched-span", "scanned-region"])
def test_auditable_labels_still_carry(tmp_path, kind):
    """NEGATIVE CONTROL for the test above: without this, a _carry_labels that reset
    EVERYTHING would pass. A v2 source's labels are audit-clean and must survive."""
    path = tmp_path / "a.json"
    path.write_text(json.dumps(_prior(2, kind)))
    new = _fresh()
    stats = fp._carry_labels(new, path)
    assert new["fires"][0]["label"] == "TP"
    assert new["fires"][0]["label_provenance"] == "carried-auditable"
    assert stats["carried"] == 1 and stats["downgraded"] == 0


def test_provenance_comes_from_source_not_code_path(tmp_path):
    """Re-measuring an ALREADY-v2 artifact must not stamp its clean labels 'pre-evidence-fix'.
    Provenance is a property of the SOURCE artifact, not of which function ran."""
    path = tmp_path / "a.json"
    path.write_text(json.dumps(_prior(2, "matched-span")))
    new = _fresh()
    fp._carry_labels(new, path)
    assert "pre-evidence-fix" not in new["fires"][0]["label_provenance"]


def test_evidence_kind_allowlist_is_the_gates_own_object():
    """EVIDENCE_KIND_VALUES is now IMPORTED from evidence_gate, not hand-mirrored, so identity
    is the assertion — not equality. (It was a literal, justified as "so this tool runs
    without the hook"; that was false — the module hard-imports evidence_gate at top level.)
    This test now guards the IMPORT: if the gate renames FP_EVIDENCE_KINDS, this fails loudly
    rather than the producer silently emitting a kind the consumer rejects."""
    import importlib.util as _i, pathlib as _p, os as _o
    _o.environ["EVIDENCE_GATE_NO_LOG"] = "1"
    egp = _p.Path.home()/".claude/hooks/stop/evidence_gate.py"
    if not egp.exists():
        pytest.skip("evidence_gate.py not deployed")
    _s = _i.spec_from_file_location("eg_check", egp)
    eg = _i.module_from_spec(_s); _s.loader.exec_module(eg)
    # IDENTITY against fp_measure's OWN imported module — that is what proves the value came
    # from the gate rather than from a re-introduced literal. (A first version compared against
    # the freshly-loaded `eg` below and failed: a separate module load creates a separate
    # frozenset object, so `is` was the wrong relation across instances. Wrong expectation in
    # the probe, not a defect in the code — caught by the test failing.)
    assert fp.EVIDENCE_KIND_VALUES is fp.evidence_gate.FP_EVIDENCE_KINDS, \
        "producer no longer shares the consumer's allowlist object — a hand-copy has returned"
    # VALUE equality against an independently-loaded gate: catches a stale bytecode/rename skew.
    assert fp.EVIDENCE_KIND_VALUES == eg.FP_EVIDENCE_KINDS
    assert "matched-span" in fp.EVIDENCE_KIND_VALUES


# ── self-healing: the MONITORING path must report the real health signal ─────
# The daily cron (`fp_measure.py --finalize-all`, 08:30) printed "hold: confirmed_fp=16"
# for a101 without ever noting that the 16 was computed from 95 labels formed on head
# excerpts. Measured 2026-08-18: 4 of 5 artifacts unauditable, 155 labels affected. A
# monitor that reports a wrong health signal is worse than no monitor.

_EV = [{"matched": ["[action] Nothing"], "excerpt": "[action] Nothing", "label": "TP"}]


@pytest.mark.parametrize("art,expected", [
    ({"schema_v": 2, "evidence_kind": "matched-span", "fires": _EV}, True),
    ({"schema_v": 2, "evidence_kind": "scanned-region", "fires": _EV}, True),
    ({"schema_v": "2", "evidence_kind": "matched-span", "fires": _EV}, True),
    ({"schema_v": 3, "evidence_kind": "matched-span", "fires": _EV}, True),
    ({"schema_v": 1, "evidence_kind": "matched-span", "fires": _EV}, False),
    ({"schema_v": 2, "evidence_kind": "head-excerpt", "fires": _EV}, False),
    ({"schema_v": 2, "fires": _EV}, False),
    ({}, False),
    ({"schema_v": 2.9, "evidence_kind": "matched-span", "fires": _EV}, False),
    ({"schema_v": True, "evidence_kind": "matched-span", "fires": _EV}, False),
    # THIRD condition, added 2026-08-18: metadata may CLAIM auditability while the fires
    # carry none. The monitor previously mirrored only 2 of the gate's 3 checks and so
    # reported "auditable" for artifacts the gate declined — a monitor disagreeing with the
    # thing it monitors (agent review, HIGH; reproduced live).
    ({"schema_v": 2, "evidence_kind": "matched-span",
      "fires": [{"excerpt": "head only", "label": "TP"}]}, False),
    ({"schema_v": 2, "evidence_kind": "matched-span",
      "fires": [{"matched": ["   "], "label": "TP"}]}, False),
    ({"schema_v": 2, "evidence_kind": "matched-span", "fires": "not-a-list"}, False),
])
def test_auditability_predicate_both_polarities(art, expected):
    """Mirrors ALL THREE of evidence_gate's auditability checks. Parametrized over BOTH
    polarities so a predicate that always returned False could not pass."""
    assert fp._artifact_auditable(art) is expected


def test_monitor_and_gate_agree_on_the_metadata_claim_case():
    """PARITY TEST. The monitor must not report healthy what the gate declines. This exact
    artifact shape (metadata claims matched-span, fires carry head excerpts) is the one that
    diverged."""
    import importlib.util as _i, os as _o, json as _j, tempfile as _t
    _o.environ["EVIDENCE_GATE_NO_LOG"] = "1"
    egp = Path.home()/".claude/hooks/stop/evidence_gate.py"
    if not egp.exists():
        pytest.skip("evidence_gate.py not deployed")
    _s = _i.spec_from_file_location("eg_parity", egp)
    eg = _i.module_from_spec(_s); _s.loader.exec_module(eg)
    d = Path(_t.mkdtemp()); eg.FP_GATE_DIR = d
    art = {"schema_v": 2, "evidence_kind": "matched-span", "fires_total": 1,
           "fires_labeled": 1, "confirmed_fp": 0,
           "fires": [{"excerpt": "head only", "label": "TP"}]}
    (d / "zz.json").write_text(_j.dumps(art))
    assert fp._artifact_auditable(art) is False
    assert eg._fp_artifact_admits_promotion("zz")[0] is False


def _notify_env(monkeypatch, tmp_path, notifier_exists=True):
    """Deterministic notification harness.

    REVIEW DEFECT THIS FIXES (HIGH, gpt-5.6-sol 2026-08-18): the previous helper mocked only
    `subprocess.run`, while production first checks `Path.home()/"bin"/"notify.sh"` exists.
    The positive test therefore passed ONLY on a machine that happens to have that file — in
    clean CI it would record no call and fail. A test whose result depends on the developer's
    home directory is not a test."""
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    (home / ".claude" / "state").mkdir(parents=True)
    if notifier_exists:
        n = home / "bin" / "notify.sh"
        n.write_text("#!/bin/sh\nexit 0\n")
        n.chmod(0o755)
    monkeypatch.setattr(fp.Path, "home", staticmethod(lambda: home))
    calls = []
    class _R:
        returncode = 0
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (calls.append(a[0]), _R())[1])
    return calls


def test_notify_fires_on_unauditable_with_labels(monkeypatch, tmp_path):
    """THE trigger. Note it does NOT depend on confirmed_fp: gating on confirmed_fp>0 was
    CIRCULAR — that number is derived from the very labels whose integrity is in doubt."""
    calls = _notify_env(monkeypatch, tmp_path)
    n, status = fp._notify_unauditable([
        {"scanner_id": "a101", "auditable": False, "fires_total": 95, "fires_labeled": 95,
         "confirmed_fp": 16}])
    assert n == 1 and status == "ok" and len(calls) == 1


def test_notify_is_state_transition_not_level(monkeypatch, tmp_path):
    """ALERT-FATIGUE GUARD: the SECOND run over the same artifact must stay silent."""
    calls = _notify_env(monkeypatch, tmp_path)
    rows = [{"scanner_id": "a101", "auditable": False, "fires_total": 95, "fires_labeled": 95,
             "confirmed_fp": 16}]
    assert fp._notify_unauditable(rows) == (1, "ok")
    calls.clear()
    n, status = fp._notify_unauditable(rows)
    assert (n, status) == (0, "no-new") and calls == []


def test_notify_fires_again_for_a_newly_seen_artifact(monkeypatch, tmp_path):
    """NEGATIVE CONTROL for the suppression: dedupe must not become permanent silence."""
    calls = _notify_env(monkeypatch, tmp_path)
    fp._notify_unauditable([{"scanner_id": "a101", "auditable": False, "fires_total": 5,
                             "fires_labeled": 5, "confirmed_fp": 0}])
    calls.clear()
    n, status = fp._notify_unauditable([
        {"scanner_id": "a101", "auditable": False, "fires_total": 5, "fires_labeled": 5,
         "confirmed_fp": 0},
        {"scanner_id": "b123", "auditable": False, "fires_total": 60, "fires_labeled": 60,
         "confirmed_fp": 16}])
    assert n == 1 and status == "ok" and len(calls) == 1


@pytest.mark.parametrize("row", [
    {"scanner_id": "zz", "auditable": True,  "fires_total": 5, "fires_labeled": 5},   # auditable
    {"scanner_id": "zz", "auditable": False, "fires_total": 5, "fires_labeled": 0},   # no labels yet
])
def test_notify_stays_silent_when_there_is_no_integrity_problem(monkeypatch, tmp_path, row):
    calls = _notify_env(monkeypatch, tmp_path)
    n, status = fp._notify_unauditable([row])
    assert (n, status) == (0, "no-new") and calls == []


def test_notify_reports_absent_notifier_rather_than_vanishing(monkeypatch, tmp_path, capsys):
    """No silent failure: an owed push that cannot be sent must be REPORTED and must return a
    status the caller can couple to the exit code."""
    _notify_env(monkeypatch, tmp_path, notifier_exists=False)
    n, status = fp._notify_unauditable([
        {"scanner_id": "zz", "auditable": False, "fires_total": 1, "fires_labeled": 1}])
    assert n == 1 and status == "notifier-absent"
    assert "notify.sh absent" in capsys.readouterr().err


def test_notify_never_raises_on_malformed_rows(monkeypatch, tmp_path):
    """Runs inside cron. A row missing scanner_id, a non-dict, a None — none may raise."""
    _notify_env(monkeypatch, tmp_path)
    for rows in ([{"auditable": False, "fires_labeled": 3}], ["not-a-dict"], [None], [{}]):
        n, status = fp._notify_unauditable(rows)
        assert isinstance(n, int) and isinstance(status, str)


def test_auditable_predicate_survives_unicode_digit(monkeypatch):
    """`"²".isdigit()` is True but `int("²")` RAISES. This crashed _artifact_auditable, which
    the daily cron calls. Found by cross-family review; reproduced before fixing."""
    assert fp._artifact_auditable({"schema_v": "²", "evidence_kind": "matched-span"}) is False


# ── the false "ready for promotion" push (CRITICAL, independent review 2026-08-18) ──
# `finalize()` computed newly_ready from _artifact_admits(), which did NOT consult
# auditability, and `_notify_ready` pushes on newly_ready alone. Relabelling a101's 16 FPs to
# TP — the DOCUMENTED NEXT STEP — therefore produced newly_ready=True on an artifact the real
# gate declines, printing "ready-for-promotion ... [UNAUDITABLE]" on one line and firing a
# high-priority push pointing the wrong way. The SUMMARY display marker had been added; the
# two ACTIONS it should have gated had not.

def _v1_ready_shaped(tmp_path):
    """A v1 artifact whose LABELS clear every OLD bar, but which is UNFINALIZED.

    `fires_labeled=0 / confirmed_fp=None` is load-bearing, not incidental. `newly_ready` is
    the not-ready -> ready TRANSITION: if the fixture is written already-final, `was_ready`
    is True and the transition never evaluates, so the test passes whether or not
    auditability gates it. The first version of this test did exactly that and a mutation
    run proved it VACUOUS — reverting the fix it exists to guard passed the whole suite."""
    art = {"scanner_id": "zz", "schema_v": 1, "fires_total": 2, "fires_labeled": 0,
           "confirmed_fp": None, "decision": "pending-labeling",
           "fires": [{"excerpt": "head only", "label": "TP", "rationale": "r"},
                     {"excerpt": "head only", "label": "TP", "rationale": "r"}]}
    (tmp_path / "zz.json").write_text(json.dumps(art))
    return art


def test_unauditable_artifact_never_reports_newly_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "ARTIFACT_DIR", tmp_path)
    _v1_ready_shaped(tmp_path)
    r = fp.finalize("zz")
    assert r["newly_ready"] is False, "would fire a 'Ready for blocking promotion' push"
    assert r["auditable"] is False


def test_unauditable_artifact_decision_is_not_self_contradictory(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "ARTIFACT_DIR", tmp_path)
    _v1_ready_shaped(tmp_path)
    fp.finalize("zz")
    decision = json.loads((tmp_path / "zz.json").read_text())["decision"]
    assert "ready-for-promotion" not in decision
    assert "PRE-EVIDENCE-FIX" in decision


def test_auditable_artifact_STILL_reports_newly_ready(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: without this, gating newly_ready on auditability could simply have
    disabled the ready path entirely and both tests above would still pass."""
    monkeypatch.setattr(fp, "ARTIFACT_DIR", tmp_path)
    # `newly_ready` is the not-ready -> ready TRANSITION, not the steady state. Start from
    # UNFINALIZED counts (fires_labeled=0, confirmed_fp=None) so finalize() produces the
    # transition. My first version of this test set them already-final and asserted True —
    # a wrong expectation baked into the probe, caught by this control failing.
    art = {"scanner_id": "zz", "schema_v": 2, "evidence_kind": "matched-span",
           "fires_total": 2, "fires_labeled": 0, "confirmed_fp": None, "decision": "x",
           "fires": [{"matched": ["[action] Nothing"], "excerpt": "[action] Nothing",
                      "label": "TP", "rationale": "r"} for _ in range(2)]}
    (tmp_path / "zz.json").write_text(json.dumps(art))
    r = fp.finalize("zz")
    assert r["newly_ready"] is True and r["auditable"] is True
    assert "ready-for-promotion" in json.loads((tmp_path / "zz.json").read_text())["decision"]


# ── the vacuous-truth guard that had gone MISSING from the mirror (/simplify, 2 legs) ──
@pytest.mark.parametrize("declared,actual", [(95, 0), (5, 1), (2, 5)])
def test_auditable_predicate_rejects_fire_count_mismatch(declared, actual):
    """`fires: []` with `fires_total: 95` passed the per-fire loop VACUOUSLY and returned
    True, while evidence_gate declined the same artifact. The monitor disagreed with the gate
    it claims to mirror. Masked in production (finalize() normalises counts first) but live
    for any direct caller — which the tests themselves are."""
    art = {"schema_v": 2, "evidence_kind": "matched-span", "fires_total": declared,
           "fires": [{"matched": ["[action] Nothing"], "label": "TP"} for _ in range(actual)]}
    assert fp._artifact_auditable(art) is False


def test_auditable_predicate_accepts_consistent_counts():
    """NEGATIVE CONTROL: the guard must not become a blanket deny."""
    art = {"schema_v": 2, "evidence_kind": "matched-span", "fires_total": 3,
           "fires": [{"matched": ["[action] Nothing"], "label": "TP"} for _ in range(3)]}
    assert fp._artifact_auditable(art) is True


def test_auditable_predicate_tolerates_absent_fires_total():
    """Direct callers (tests, ad-hoc audits) often pass a dict with no counts. Absent is not
    a mismatch — only a PRESENT-and-wrong count rejects."""
    art = {"schema_v": 2, "evidence_kind": "matched-span",
           "fires": [{"matched": ["[action] Nothing"], "label": "TP"}]}
    assert fp._artifact_auditable(art) is True


def test_at_risk_ids_is_one_source_of_truth():
    """The notifier and the SUMMARY count both derive from this helper; they were duplicated
    predicates before, with nothing enforcing agreement."""
    rows = [{"scanner_id": "a", "auditable": False, "fires_labeled": 5},   # at risk
            {"scanner_id": "b", "auditable": False, "fires_labeled": 0},   # no labels yet
            {"scanner_id": "c", "auditable": True,  "fires_labeled": 5},   # auditable
            "not-a-dict", None, {}]                                        # malformed
    assert fp._at_risk_ids(rows) == {"a"}


def test_seen_state_write_is_atomic(monkeypatch, tmp_path):
    """The dedup-state write must go to a temp file and be os.replace'd into position.

    BEHAVIOURAL, not structural: make os.replace fail, then assert the ORIGINAL file is
    byte-for-byte unchanged. That can only hold if the payload was written somewhere else
    first. A plain `write_text` truncates in place, so the original would be destroyed.

    A mutation run flagged this path as the one surviving mutant — a plain write_text passed
    the whole suite. Every other write in fp_measure.py already uses temp+rename; this one had
    been left out of that discipline, and nothing noticed because nothing tested it."""
    calls = _notify_env(monkeypatch, tmp_path)
    state = fp._unauditable_state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(["pre-existing"]))
    before = state.read_bytes()

    import os as _os
    monkeypatch.setattr(_os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    # Must not raise — the whole notifier is cron-safe — and must not corrupt the state file.
    n, status = fp._notify_unauditable(
        [{"scanner_id": "zz", "auditable": False, "fires_total": 1, "fires_labeled": 1}])
    assert isinstance(n, int) and isinstance(status, str)
    assert state.read_bytes() == before, \
        "a failed rename destroyed the existing state — the write was not atomic"
    assert not list(state.parent.glob(f"{state.name}.tmp*")), "temp file left behind"


# ── the READY push had no delivery guarantee (architecture review, HIGH) ──────
# `newly_ready` is a ONE-SHOT transition: finalize() reads was_ready BEFORE overwriting the
# artifact, so the not-ready -> ready edge happens once and is consumed. _notify_ready
# returned None, its caller ignored the outcome, and the non-zero-exit clause covered only
# the unauditable path — so a failed push on that single run vanished and the job exited 0.
# The PRIMARY signal of the promotion system was the one without a guarantee.

def test_notify_ready_reports_a_status(monkeypatch, tmp_path):
    _notify_env(monkeypatch, tmp_path)
    assert fp._notify_ready(["zz"]) == (1, "ok")


def test_notify_ready_reports_absent_notifier(monkeypatch, tmp_path, capsys):
    _notify_env(monkeypatch, tmp_path, notifier_exists=False)
    n, status = fp._notify_ready(["zz"])
    assert (n, status) == (1, "notifier-absent")
    assert "NOT pushed" in capsys.readouterr().err


def test_notify_ready_reports_send_failure(monkeypatch, tmp_path):
    _notify_env(monkeypatch, tmp_path)
    import subprocess
    class _Fail:
        returncode = 1
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Fail())
    assert fp._notify_ready(["zz"]) == (1, "send-failed")


def test_notify_ready_empty_is_not_a_failure(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: nothing owed must NOT look like a failed delivery, or every clean
    run would exit non-zero and the cron alert would fire daily."""
    _notify_env(monkeypatch, tmp_path)
    assert fp._notify_ready([]) == (0, "no-new")


def test_notify_ready_never_raises(monkeypatch, tmp_path):
    _notify_env(monkeypatch, tmp_path)
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    n, status = fp._notify_ready(["zz"])
    assert (n, status) == (1, "send-failed")


# ── the corpus must be the RUNTIME-REACHABLE population (architecture review, W2) ──
def test_corpus_covers_all_slugs_not_just_one():
    """The original glob was single-slug (1,428 of 2,908 top-level transcripts = 49.1%),
    which is how a NEGATIVE claim became unfalsifiable by construction."""
    assert any("projects/*/" in g for g in fp.CORPUS_GLOBS), \
        "corpus is slug-scoped again — a single-slug denominator cannot support a negative claim"


def test_corpus_excludes_sidechains_because_the_hook_never_sees_them():
    """evidence_gate is wired to `Stop` ONLY — no SubagentStop — so subagent (isSidechain)
    transcripts are not runtime-reachable. Including them makes confirmed_fp answer a
    different question than the promotion decision asks."""
    assert fp.SIDECHAIN_POLICY == "exclude"
    assert not any("subagents" in g for g in fp.CORPUS_GLOBS), \
        "sidechain transcripts are back in the corpus — confirmed_fp would mix two populations"


def test_artifact_records_which_population_produced_it(tmp_path):
    """The denominator must be IN the artifact. Not recording it is how '14.5% coverage' got
    read as 'the corpus'."""
    _jsonl(tmp_path, "s.jsonl", _HEAD + _TAIL_FIRES)
    art = fp.measure("a14c", str(tmp_path / "*.jsonl"))
    assert art["sidechain_policy"] == "exclude"
    assert art["corpus_globs"] and isinstance(art["corpus_globs"], list)
    assert art["corpus_files_total"] >= art["corpus_files_scanned"] >= 0


def test_sidechain_policy_is_ENFORCED_not_merely_declared(tmp_path):
    """BEHAVIOURAL. A mutation run proved the earlier version was a label only: a mutant that
    kept sidechain_policy="exclude" while scanning sidechain files SURVIVED the whole suite,
    so the artifact could assert a population it had not measured. This test hands measure()
    an explicit corpus containing a sidechain path and asserts the fire is NOT counted."""
    top = tmp_path / "proj"
    side = top / "subagents"
    side.mkdir(parents=True)
    _jsonl(top, "main.jsonl", _HEAD + _TAIL_FIRES)       # reachable  -> counts
    _jsonl(side, "agent.jsonl", _HEAD + _TAIL_FIRES)     # sidechain  -> must NOT count
    # Two explicit globs joined by os.pathsep — the top-level one AND the sidechain one, so
    # the corpus argument genuinely offers the sidechain file and the POLICY must reject it.
    art = fp.measure("a14c", str(top / "*.jsonl") + os.pathsep + str(side / "*.jsonl"))
    assert art["fires_total"] == 1, "a sidechain fire was counted despite policy=exclude"
    assert all("/subagents/" not in f["source"] for f in art["fires"])
    assert art["sidechain_policy"] == "exclude"


def test_is_sidechain_path_both_polarities():
    assert fp._is_sidechain_path("/home/x/.claude/projects/slug/subagents/agent-a1.jsonl") is True
    assert fp._is_sidechain_path("/home/x/.claude/projects/slug/session.jsonl") is False
