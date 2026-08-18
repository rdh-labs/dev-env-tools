#!/usr/bin/env python3
"""fp_measure.py — real-corpus false-positive measurement for evidence_gate scanners.

Realizes IDEA-10413 / the FP-substance-gate (ISSUE-3422). Produces the reproducible,
regex-bound FP-measurement artifact that evidence_gate's ``_apply_staged_escalation``
reads before promoting a NEW scanner from advisory to blocking (exit 2). The artifact is
the *substance* the gate thresholds on (``confirmed_fp == 0`` over the complete fire set),
NOT a presence-of-ritual marker — and it is re-runnable by any auditor, so fabricating it
costs strictly more than measuring honestly.

Usage:
    fp_measure.py <scanner_id> [--write-artifact] [--corpus DIR]
    fp_measure.py --list

What it does:
  1. Resolves the scanner's fire-predicate from SCANNER_PREDICATES (extend per new scanner).
  2. Replays it over the real session-transcript corpus (~1096 .jsonl files), extracting
     every assistant response that WOULD fire, verbatim.
  3. Computes a sha256 fingerprint of the scanner's exact pattern source (so a later regex
     change voids the artifact — the gate recomputes and compares).
  4. With --write-artifact, writes ~/.claude/logs/fp-gate/<scanner_id>.json with every fire
     (excerpt + label slot + rationale slot) for TP/FP labeling. ``confirmed_fp`` is derived
     from the labels; promotion requires it to be 0 over the COMPLETE fire set.

Labeling: this tool emits fires with ``label: "unlabeled"``. A reviewer (agent or user)
sets each to TP/FP with a rationale. The gate treats any unlabeled fire as not-yet-cleared
(promotion held), so an unlabeled artifact never silently admits promotion.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# CORPUS = THE RUNTIME-REACHABLE POPULATION. Two corrections, both measured.
#
# (1) It was `.../-home-ichardart-dev/*.jsonl` — NON-RECURSIVE and single-slug. That made a
#     NEGATIVE CLAIM unfalsifiable: a14c.json asserted "zero out-of-sample instances" and the
#     query could not have returned them.
# (2) The first fix over-corrected: it added `*/subagents/*.jsonl`. But evidence_gate.py is
#     wired ONLY to the `Stop` hook in settings.json — there is NO `SubagentStop` entry
#     (verified by parsing settings.json). Subagent transcripts are isSidechain=true and the
#     scanner NEVER evaluates them, so including them measures fires that cannot occur in
#     production, while STILL missing half the population that can: 1,428 of 2,908 top-level
#     transcripts = 49.1%, the largest omission being the `-home-ichardart` slug (1,390 files).
#
# So: ALL SLUGS, TOP-LEVEL ONLY. `confirmed_fp` then means "the FP rate this scanner will
# produce in production" — the only reading on which a promotion decision is sound.
# The sidechain population is a DIFFERENT question (how often the rule is wrong about agent
# output anywhere) and must be measured and labelled separately, not silently mixed in.
# Every artifact records `corpus_globs` and `sidechain_policy`, so which population produced
# a number is never again something a reader has to infer.
CORPUS_GLOBS = [str(Path.home() / ".claude/projects/*/*.jsonl")]
SIDECHAIN_POLICY = "exclude"   # runtime-reachable only; see above. "include" = behaviour study.


def _is_sidechain_path(path: str) -> bool:
    """True for a subagent transcript. ENFORCEMENT, not a label.

    `SIDECHAIN_POLICY` was originally written into the artifact and read by NOTHING — a
    mutant that kept the label saying "exclude" while scanning sidechain files anyway
    SURVIVED the whole suite. The artifact would have asserted a population it did not
    measure. That is the same "metadata is a CLAIM, not evidence" defect this change set
    fixed in evidence_gate's per-fire check, committed again here — in the very commit whose
    message says an instrument must record its own denominator. Recording it is not enough;
    the policy has to be the thing that actually selects the files."""
    return "/subagents/" in path.replace("\\", "/")
CORPUS_GLOB = os.pathsep.join(CORPUS_GLOBS)   # CLI default; split on os.pathsep when globbing
ARTIFACT_DIR = Path.home() / ".claude/logs/fp-gate"
SCHEMA_V = 2   # v2: fires carry matched-span evidence (was: head excerpt)

# For A101: import the real evidence_gate compiled regexes/function rather than hand-
# reproducing them (avoids the exact kind of drift risk the A101 implementation itself
# flagged for its own AUTH_REQUIRED_RE mirror). EVIDENCE_GATE_NO_LOG is set before the
# import so any accidental module-level side effect can't write to production scanner
# logs (defensive; evidence_gate.py's own logging is call-time, not import-time).
os.environ.setdefault("EVIDENCE_GATE_NO_LOG", "1")
sys.path.insert(0, str(Path.home() / "dev/infrastructure/dev-env-config/claude/hooks/stop"))
import evidence_gate  # noqa: E402

# ── Scanner fire-predicates ──────────────────────────────────────────────────
# Each entry maps a scanner_id to (predicate, pattern_sources, prefilter). ``predicate(text)``
# returns True if the scanner would fire on an assistant response ``text``. ``pattern_sources``
# is the list of raw regex pattern strings whose sha256 forms the fingerprint the gate binds to.
# ``prefilter`` is a LOWERCASED substring (or None) that is a NECESSARY condition of firing, checked
# as ``prefilter in raw.lower()`` — files lacking it are skipped without JSON-parsing (a large speedup
# on the ~1096-file corpus; the measurement otherwise exceeds 180s on 6GB WSL2). It MUST be lowercase
# and a true necessary condition under the predicate's matching (A97's regex requires the literal,
# case-insensitive, single-space "anomaly analysis", so that is the prefilter). A case-SENSITIVE
# substring is WRONG when the predicate is case-insensitive; an anchored re.M regex prefilter is
# correct but too slow at corpus scale (timed out >280s) — the lowercased substring is correct AND fast.
#
# EXTENSION POINT: when a new blocking scanner is added to evidence_gate's FP_GATE coverage,
# add its predicate here (import its compiled regexes from evidence_gate, or reproduce them
# verbatim as the A97 seed below does). Keeping the pattern_sources identical to the live
# scanner is what makes the fingerprint meaningful.

# Seed/reference entry: the A97 candidate (IDEA-10427) — the cautionary tale. Its measured
# precision was ~0%, so it must NOT promote. Reproduced verbatim from a97_fp_test.py so a
# re-run reproduces the known result (regression anchor for this tool).
_A97_ANOMALY_RE = re.compile(r"^##\s*Anomaly Analysis\b", re.M | re.I)
_A97_FENCE_RE = re.compile(r"```[\s\S]*?```")
_A97_SELF_DETECT_RE = re.compile(
    r"(?i)\b(?:self[-\s]?detected?|self[-\s]?initiated|I\s+noticed\s+this\b|"
    r"I\s+caught\s+this\b|I\s+detected\s+this\b|I\s+identified\s+this\s+myself\b|"
    r"I\s+flagged\s+this\s+myself\b|proactively\s+detected?|"
    r"detected\s+without\s+(?:user|prompting)|initiated\s+by\s+(?:the\s+)?agent)\b")
_A97_USER_ATTRIB_RE = re.compile(
    r"(?i)\b(?:you\s+(?:caught|flagged|pointed\s+out|spotted|named|surfaced|raised)|"
    r"as\s+you\s+(?:noted|observed|pointed\s+out|flagged|caught)|"
    r"you\s+had\s+to\s+(?:ask|prompt|name|escalate|point|flag)|"
    r"user[-\s]?prompted|user[-\s]?flagged|user[-\s]?caught|user[-\s]?raised|user[-\s]?named|"
    r"required\s+(?:the\s+)?user\s+to|after\s+you\s+(?:flagged|named|pointed|caught))\b")
_A97_DETECT_FAIL_RE = re.compile(
    r"(?i)\b(?:detection[-\s]?failure|failed\s+to\s+detect|failure\s+to\s+detect|"
    r"detect\w*\s+fail\w*|meta[-\s]?anomaly|detection[-\s]?gap|detection[-\s]?origin|"
    r"detection\s+was\s+user[-\s]?prompted)\b")


def _a97_section(text: str) -> str | None:
    m = _A97_ANOMALY_RE.search(text)
    if not m:
        return None
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return text[m.start():end]


def _a97_fires(text: str) -> bool:
    sec = _a97_section(_A97_FENCE_RE.sub("", text))
    if not sec:
        return False
    if not _A97_SELF_DETECT_RE.search(sec):
        return False
    if not _A97_USER_ATTRIB_RE.search(sec):
        return False
    if _A97_DETECT_FAIL_RE.search(sec):
        return False
    return True


def _a101_fires(text: str) -> bool:
    """Predicate for A101 (check_you_generic_deferred_action) — calls the REAL evidence_gate
    function directly rather than reproducing its logic, so this measurement is exactly
    faithful to what the live scanner does (2026-07-30, Dart R1rtzzHY30zb)."""
    return bool(evidence_gate.check_you_generic_deferred_action(text))


# ── A14c (session 2783153d, 2026-08-18) ──────────────────────────────────────
# CLAUDE.md's `You:` table defines `Nothing …` as its OWN category, not a value that may
# follow a token. So `[action] Nothing` asserts both that a concrete step is required of the
# user and that none is. This is an exact string match on a mutually-exclusive-category
# violation defined in the workspace's own documentation — a logical invariant, not a
# heuristic. Measured on one session (134 You: lines): 15 fires, 0 FP by hand-label.
# This corpus replay is what tests whether that 0 holds at scale.
#
# The companion fuzzy check (a low-obligation token on a decision-shaped line) measured ~30%
# FP on the same corpus and is DELIBERATELY NOT REGISTERED HERE — only the exact match is a
# promotion candidate.
# IMPORT the live predicate from ~/bin/you-token-check rather than hand-reproducing it.
# 2026-08-18: I hand-copied it, then changed the tool (added "none"/"no action" with a word
# boundary) and NOT this copy — so the artifact measured a predicate the tool no longer
# implemented, and the sha256 fingerprint could not detect it because the fingerprint binds
# the regexes LISTED here, not the tool's semantics. This file's own a101 note warns against
# exactly this ("import the real compiled regexes ... avoids the exact kind of drift risk").
import importlib.machinery as _imach
import importlib.util as _ilu

_YTC_PATH = Path.home() / "bin" / "you-token-check"
if not _YTC_PATH.is_file():
    raise SystemExit(f"fp_measure: a14c predicate source missing: {_YTC_PATH}")
_spec = _ilu.spec_from_loader(
    "you_token_check", _imach.SourceFileLoader("you_token_check", str(_YTC_PATH))
)
_ytc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_ytc)


def _a14c_fires(text: str) -> bool:
    """True iff any You: value opens with a token and is immediately negated.

    Delegates to the SHIPPED checker so the two can never diverge again.
    """
    for raw_val in evidence_gate.YOU_FIELD_VALUE_RE.findall(text):
        if any(f[0] == "TOKEN-PLUS-NOTHING" for f in _ytc.check_line(raw_val)):
            return True
    return False


# ── Evidence extraction (2026-08-18, session 2783153d) ───────────────────────
# DEFECT THIS FIXES. The artifact stored ``text.strip()[:600]`` -- a HEAD excerpt -- while
# every registered scanner matches on the response TAIL (CLAUDE.md makes `You:` the LAST
# line by contract) or on a mid-body section. The stored evidence therefore provably could
# not contain the thing that fired, so NO label in the artifact was verifiable FROM the
# artifact. Measured at the moment of discovery: a101 held 95 reviewer labels of which only
# 3 excerpts contained a `You:` line at all, and its live `decision: hold` (confirmed_fp=16)
# rested on them.
#
# This defeated the tool's OWN advertised property (see module docstring): "it is re-runnable
# by any auditor, so fabricating it costs strictly more than measuring honestly." An auditor
# cannot re-verify a label from evidence that omits the match. The claim was aspirational,
# not implemented.
#
# It also PROPAGATED downstream: qc_label_audit/rater_driver.py:87, verdict.py:93 and
# deterministic_anchor.py:41,56 all read ``fires[].excerpt`` -- so the multi-rater agreement
# machinery showed raters head excerpts as the text they were adjudicating.
#
# DESIGN. Each scanner registers an extractor returning the span(s) its predicate actually
# adjudicated. ``evidence_kind`` records WHICH guarantee the stored evidence carries, so an
# auditor never has to infer it:
#     "matched-span"    the precise value(s) that fired (strongest)
#     "scanned-region"  the region the predicate examined; the fired value is within it
# There is deliberately NO head-excerpt fallback. A scanner with no registered extractor
# RAISES rather than writing evidence-free fires -- fail-loud at the PRODUCER is what makes
# this structural rather than advisory, and it needs no change to evidence_gate.py.


def _norm(s: str) -> str:
    """Collapse all whitespace. Used identically when BUILDING an excerpt and when CHECKING
    the containment invariant, so the two can never disagree about newlines.

    Edge case that forced this: A97's evidence is a multi-line `## Anomaly Analysis` section.
    A naive ``.replace("\n", " ")`` on one side and a raw ``in`` test on the other made the
    invariant fail on valid input -- the check, not the data, was wrong."""
    return " ".join(s.split())


def _a14c_evidence(text: str) -> list[str]:
    """The exact `You:` value(s) that tripped TOKEN-PLUS-NOTHING, via the SHIPPED checker."""
    out = []
    for raw_val in evidence_gate.YOU_FIELD_VALUE_RE.findall(text):
        if any(f[0] == "TOKEN-PLUS-NOTHING" for f in _ytc.check_line(raw_val)):
            out.append(raw_val.strip())
    return out


def _a101_evidence(text: str) -> list[str]:
    """Every `You:` value in the TAIL region A101 scans.

    Deliberately the scanned REGION, not a re-implementation of A101's nine-clause exclusion
    chain. Reproducing that filter here would recreate precisely the drift this file's own
    a101 NOTE warns against -- and which actually bit a14c on 2026-08-18, when a hand-copied
    predicate silently diverged from the tool it was copied from. The fired value is
    guaranteed to be among these, which is what an auditor needs to adjudicate."""
    lines = text.splitlines()
    n = evidence_gate.TAIL_LINES
    tail = lines[-n:] if len(lines) > n else lines
    return [m.strip() for m in evidence_gate.YOU_FIELD_VALUE_RE.findall("\n".join(tail))]


def _a97_evidence(text: str) -> list[str]:
    """The `## Anomaly Analysis` section A97 adjudicated -- fences stripped, exactly as the
    predicate does, so the auditor sees the same text the predicate saw."""
    sec = _a97_section(_A97_FENCE_RE.sub("", text))
    return [sec.strip()] if sec and sec.strip() else []


# scanner_id -> (extractor, evidence_kind). A scanner absent here CANNOT be measured.
EVIDENCE_EXTRACTORS: dict[str, tuple[Callable[[str], list[str]], str]] = {
    "a14c": (_a14c_evidence, "matched-span"),
    "a101": (_a101_evidence, "scanned-region"),
    "A97": (_a97_evidence, "matched-span"),
}

EXCERPT_CAP = 600
# IMPORTED, not mirrored. An earlier version kept this as a literal "so this tool stays
# runnable if the hook is absent" — that justification was FALSE: this module does a bare
# top-level `import evidence_gate` with no try/except, so fp_measure already cannot run
# without the hook on disk. Hand-copying bought nothing and cost a real drift (the
# vacuous-truth guard below went missing from the copy). Found by /simplify.
EVIDENCE_KIND_VALUES = evidence_gate.FP_EVIDENCE_KINDS


def _excerpt_with_evidence(matched: list[str], cap: int = EXCERPT_CAP) -> str:
    """Build the backward-compatible ``excerpt`` -- but one that CONTAINS the match.

    Kept under the original key because three live consumers read it:
    qc_label_audit/rater_driver.py:87 and verdict.py:93 show this text to the RATERS (so it
    is the text a label is actually formed on), and deterministic_anchor.py:41,56 hashes it.
    Changing the key would have broken all three; changing the CONTENT fixes all three."""
    return _norm(" || ".join(matched))[:cap]


def _assert_evidence_invariant(art: dict) -> None:
    """Every fire's excerpt must contain the first 120 normalised chars of a matched span.

    NOT full-span containment — the excerpt is capped, so a longer span is legitimately
    truncated and only its prefix can be verified. The earlier wording ("contains at least one
    of its own matched spans") overclaimed, and that overclaim had also leaked into the
    artifact's own evidence_contract field, which human auditors read while labelling.

    This is the FALSIFIABLE form of the property the module docstring claims. It runs at
    write time -- not as a comment -- so a future refactor that reintroduces a head excerpt
    fails loudly here instead of silently resuming production of unauditable labels."""
    for i, f in enumerate(art.get("fires", [])):
        matched = [m for m in (f.get("matched") or []) if m and m.strip()]
        if not matched:
            raise RuntimeError(
                f"fire {i} ({f.get('source')}): no matched evidence stored -- refusing to "
                f"write an unauditable fire")
        exc = f.get("excerpt", "")
        # Compare on a PREFIX: the excerpt is capped, so a span longer than the cap is
        # legitimately truncated. The prefix is what survives, and it is what proves the
        # excerpt is built from the match rather than from the head of the response.
        if not any(_norm(m)[:120] in exc for m in matched):
            raise RuntimeError(
                f"fire {i} ({f.get('source')}): excerpt contains none of its matched spans "
                f"-- the head-excerpt defect has REGRESSED")


SCANNER_PREDICATES: dict[str, tuple[Callable[[str], bool], list[re.Pattern], str | None]] = {
    # lowercase key — see the a101 NOTE below; _fp_streams / _fp_artifact_path use lowercase ids.
    "a14c": (
        _a14c_fires,
        # Fingerprint binds the LIVE tool's regexes, so editing the tool voids the artifact.
        [evidence_gate.YOU_FIELD_VALUE_RE, _ytc._NEGATION_RE, _ytc.YOU_RE],
        "you:",  # a You: field is a NECESSARY condition of firing
    ),
    "A97": (
        _a97_fires,
        # Every regex whose source+flags determine firing — sha256'd into the artifact fingerprint.
        [_A97_ANOMALY_RE, _A97_FENCE_RE, _A97_SELF_DETECT_RE, _A97_USER_ATTRIB_RE, _A97_DETECT_FAIL_RE],
        "anomaly analysis",   # lowercased necessary-condition substring (predicate requires case-insensitive "anomaly analysis")
    ),
    # NOTE: registered lowercase "a101" (not "A101") — evidence_gate.py's _fp_streams dict
    # (and _fp_artifact_path, which the runtime FP-substance gate calls with that exact key)
    # uses lowercase scanner ids, so the artifact filename this tool writes MUST match. The
    # A97 entry's uppercase key was never exercised against a live _fp_streams lookup (A97
    # was measured, found ~0% precision, and deliberately never wired into the blocking
    # stream) — so this case mismatch risk was latent, not previously hit. Flagging as a
    # separate finding, not fixing A97's casing here (out of scope, A97 is inert by design).
    "a101": (
        _a101_fires,
        [
            evidence_gate.YOU_FIELD_VALUE_RE,
            evidence_gate._A101_SAY_THE_WORD_RE,
            evidence_gate._A70_S11_CITE_RE,
            evidence_gate.A11_STATUS_MARKER_RE,
            evidence_gate._A101_CHOICE_OFFER_RE,
            evidence_gate._A101_AUTH_REQUIRED_RE,
            # Added 2026-07-31 with the exclusions they implement. EVERY regex the
            # predicate consults must be listed here or the fingerprint cannot
            # detect that a stored artifact has gone stale: the first revision of
            # these exclusions changed the fire count 131 -> 95 while the
            # fingerprint stayed byte-identical, which is exactly the silent-stale
            # case the fingerprint exists to prevent.
            evidence_gate._A101_RECOMMENDATION_RE,
            evidence_gate._A101_EXTRA_AUTH_RE,
            evidence_gate._A101_PUSH_RE,
            evidence_gate._A101_CLAUSE_SPLIT_RE,
        ],
        None,  # no single necessary substring covers all 3 trigger phrases; full scan
    ),
}


# ── Corpus walk ──────────────────────────────────────────────────────────────
def _assistant_texts_from_raw(raw: str) -> tuple[list[str], int]:
    """Return (assistant-message text bodies, json-parse-failure count) from a transcript's raw JSONL.

    Well-formed .jsonl is one object per line; a non-zero failure count signals embedded-newline /
    pretty-printed records whose text would be silently missed — surfaced in the artifact so the
    "complete fire set" claim is auditable rather than silently incomplete.
    """
    texts: list[str] = []
    failures = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            failures += 1
            continue
        msg = obj.get("message") or obj
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append("".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ))
    return texts, failures


def _fingerprint(patterns: list[re.Pattern]) -> str:
    """sha256 over each regex's source AND flags — a flags-only change must also void the artifact."""
    h = hashlib.sha256()
    for p in patterns:
        h.update(p.pattern.encode("utf-8"))
        h.update(f"|flags={p.flags}\x00".encode("utf-8"))
    return "sha256:" + h.hexdigest()


def measure(scanner_id: str, corpus_glob: str = CORPUS_GLOB) -> dict:
    """Replay scanner_id's predicate over the corpus; return the artifact dict (unlabeled)."""
    if scanner_id not in SCANNER_PREDICATES:
        raise ValueError(
            f"No predicate registered for {scanner_id}. Add it to SCANNER_PREDICATES "
            f"(import its regexes from evidence_gate). Registered: {sorted(SCANNER_PREDICATES)}"
        )
    predicate, fingerprint_patterns, prefilter = SCANNER_PREDICATES[scanner_id]
    if scanner_id not in EVIDENCE_EXTRACTORS:
        # No silent head-excerpt fallback. An artifact whose evidence cannot show the match
        # is worse than no artifact: it LOOKS like substance and admits promotion through a
        # gate that only counts labels. Refuse at the producer.
        raise ValueError(
            f"{scanner_id}: no evidence extractor registered. Add one to EVIDENCE_EXTRACTORS "
            f"-- fp_measure will not write fires whose stored evidence cannot contain the "
            f"match (2026-08-18 head-excerpt defect). Registered: "
            f"{sorted(EVIDENCE_EXTRACTORS)}")
    extractor, kind = EVIDENCE_EXTRACTORS[scanner_id]
    # PREFILTER AS A COMPILED CASE-INSENSITIVE REGEX, not `prefilter not in raw.lower()`.
    # MEASURED 2026-08-18 on the largest real corpus file (77.5 MB): `.lower()` peaked at
    # 1.24-1.54 GB TRANSIENT, because it materialises a full second copy of the string that
    # stays alive alongside `raw`. The compiled IGNORECASE search adds 0.00 MB — it never
    # materialises a copy. Full-corpus read+lower+substring measured 188.9s over 8,447 files
    # / 6.64 GB. This box is 16 GB with an 8 GB WSL2 cap and was at 2 GB/4 GB swap with 80 MB
    # free during the measurement, and ~80 corpus files exceed 10 MB — so this was a live
    # swap-thrash risk, not a hypothetical. Identical semantics: same necessary condition,
    # same case-insensitivity. Compiled ONCE here, outside the per-file loop.
    prefilter_re = re.compile(re.escape(prefilter), re.IGNORECASE) if prefilter else None
    # corpus_glob may carry SEVERAL patterns joined by os.pathsep (see CORPUS_GLOBS). A single
    # pattern still works, so an explicit --corpus is unaffected.
    files = sorted({f for pat in str(corpus_glob).split(os.pathsep) if pat
                    for f in glob.glob(pat)})
    if SIDECHAIN_POLICY == "exclude":
        # The policy SELECTS the files. An explicit --corpus that points at sidechains is
        # filtered here too, so the artifact's sidechain_policy field can never describe a
        # population the run did not actually measure.
        files = [f for f in files if not _is_sidechain_path(f)]
    total_scanned = 0
    files_scanned = 0
    parse_failures = 0
    fires: list[dict] = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        if prefilter_re is not None and not prefilter_re.search(raw):
            continue   # necessary-condition skip (case-insensitive substring) — no JSON parse
        files_scanned += 1
        texts, failed = _assistant_texts_from_raw(raw)
        parse_failures += failed
        for text in texts:
            if not text:
                continue
            total_scanned += 1
            if predicate(text):
                matched = [m for m in extractor(text) if m and m.strip()]
                if not matched:
                    # Predicate fired, extractor found nothing: the two DISAGREE. That is a
                    # bug in one of them and exactly the silent-drift class that produced
                    # this defect. Never store an evidence-free fire; fail the whole run.
                    raise RuntimeError(
                        f"{scanner_id}: predicate fired but the evidence extractor returned "
                        f"nothing for a response in {os.path.basename(fp)} -- extractor and "
                        f"predicate have DRIFTED. Refusing to write unauditable evidence.")
                fires.append({
                    "matched": matched,
                    "evidence_kind": kind,
                    "excerpt": _excerpt_with_evidence(matched),
                    # PRIMARY join key: a digest of the FULL response text. The previous key
                    # was the first 200 chars of the pre-fix head excerpt, which COLLIDES —
                    # responses routinely share an opening, and two colliding fires would
                    # silently swap reviewer labels. External prior art (in-toto attestation
                    # v1): bind a label to a digest of the exact record, "otherwise a rule
                    # edit silently invalidates every prior label with no detectable break".
                    "record_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    # Pre-fix head excerpt, retained ONLY so labels on v1/v2 artifacts written
                    # BEFORE record_digest existed can still be joined. Remove once no
                    # artifact predating this field remains.
                    "legacy_key": text.strip()[:600].replace("\n", " ")[:200],
                    "source": os.path.basename(fp),
                    "label": "unlabeled",   # reviewer sets TP / FP
                    "rationale": "",
                })
    art = {
        "scanner_id": scanner_id,
        "schema_v": SCHEMA_V,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "regex_fingerprint": _fingerprint(fingerprint_patterns),
        "corpus_ref": "claude-projects-jsonl",
        # Recorded so a coverage change is DETECTABLE. The v1 artifacts were measured with a
        # non-recursive glob that excluded 8,400 subagent transcripts; nothing in them said so,
        # which is how "zero out-of-sample instances" got published as a finding.
        "corpus_globs": [pat for pat in str(corpus_glob).split(os.pathsep) if pat],
        # WHICH POPULATION produced this number. evidence_gate runs on `Stop` only, so
        # sidechain (subagent) transcripts are not runtime-reachable; mixing them into
        # confirmed_fp makes a promotion threshold answer a question nobody asked.
        "sidechain_policy": SIDECHAIN_POLICY,
        "corpus_files_total": len(files),
        "corpus_files_scanned": files_scanned,
        "corpus_parse_failures": parse_failures,
        "corpus_size_responses": total_scanned,
        "fires_total": len(fires),
        "fires_labeled": 0,
        "confirmed_fp": None,         # None = not yet derived; set to count of FP labels once every fire is labeled (must be 0 to promote)
        "decision": "pending-labeling",
        "harness": "fp_measure.py",
        "evidence_kind": kind,
        "evidence_contract": (
            "every fires[].excerpt contains the first 120 normalised characters of at least "
            "one of that fire's matched spans; enforced by _assert_evidence_invariant at "
            f"write time. NOTE THE BOUND: the excerpt is capped at {EXCERPT_CAP} chars, so a "
            "span longer than 120 chars is verified by PREFIX, not in full. The complete "
            "span is always present verbatim in fires[].matched."),
        "falsifier": "a fire labeled TP that is actually a legitimate (non-violating) response",
        "fires": fires,
    }
    # Checked HERE, before any caller can persist it: a measurement that cannot evidence its
    # own fires must not become a file that a promotion gate reads.
    _assert_evidence_invariant(art)
    return art


def _carry_labels(new_art: dict, path: Path) -> dict:
    """Preserve reviewer labels across a re-measure. Returns a stats dict.

    WHY THIS EXISTS. ``main()``'s write path was a bare ``out.write_text(json.dumps(art))``,
    which DESTROYS every label in an existing artifact. a101 currently holds 95 hand-assigned
    labels and 16 confirmed FPs; re-running the tool would have silently erased them and
    reset the artifact to `pending-labeling`. Nothing warned. That is a second instance of
    the same family as the head-excerpt defect: an operation whose failure is invisible.

    Labels are re-attached by ``(source, legacy_key)`` -- the PRE-FIX head excerpt, which is
    still deterministically reproducible from the response text, and is therefore a valid
    join key across exactly this one schema change.

    Behaviour BRANCHES on whether the SOURCE artifact was auditable:
      v2 auditable source -> label carried intact, stamped ``label_provenance="carried-auditable"``
      anything else       -> label RESET to "unlabeled", stamped
                             ``label_provenance="reset-was-pre-evidence-fix"``, with the
                             reviewer's work preserved as ``prior_label``/``prior_rationale``

    Carrying a pre-fix label forward would relaunder the very defect this change fixes: the
    artifact would end up schema_v=2 with evidence_kind set, and admit promotion on labels
    formed against head excerpts. (This docstring previously described that REJECTED design —
    an unconditional carry stamped "pre-evidence-fix", a value no code path writes. Caught by
    /simplify: a reader trusting the docstring would believe something the code does not do.)
    """
    stats = {"carried": 0, "fresh": 0, "orphaned": 0, "prior_labeled": 0, "downgraded": 0}
    if not path.exists():
        stats["fresh"] = len(new_art.get("fires", []))
        return stats
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        # Fail LOUD. Silently proceeding would overwrite an unreadable-but-present artifact
        # and destroy labels we could not enumerate.
        raise RuntimeError(
            f"{path}: existing artifact unreadable ({type(e).__name__}: {e}) -- refusing to "
            f"overwrite it, because that would destroy reviewer labels that cannot be read "
            f"back first. Move it aside deliberately if you intend to discard it.") from e
    # Is the SOURCE artifact audit-clean? Derived from the source's own schema_v, NOT from
    # the code path — a cross-family review (2026-08-18) found that stamping provenance by
    # code path mislabels genuinely clean v2 labels as "pre-evidence-fix" on every re-measure.
    try:
        old_schema = int(old.get("schema_v", 1))
    except (TypeError, ValueError):
        old_schema = 1
    source_auditable = old_schema >= 2 and old.get("evidence_kind") in EVIDENCE_KIND_VALUES

    # deque, not list: `pop(0)` on a list is O(n), so duplicate-key buckets degrade to O(n^2)
    # (MEASURED: 10k same-key items 122ms, 20k 369ms — clearly quadratic). popleft() is O(1)
    # and behaviourally identical. Free fix; today's max bucket is tiny, but the cost of
    # leaving it is unbounded and the cost of fixing it is one import.
    index: dict[tuple, deque] = {}
    for f in old.get("fires", []):
        if not isinstance(f, dict):
            continue
        if f.get("label") in ("TP", "FP"):
            stats["prior_labeled"] += 1
        for k in _join_keys(f):
            index.setdefault(k, deque()).append(f)
    for f in new_art.get("fires", []):
        bucket = None
        for k in _join_keys(f):
            b = index.get(k)
            if b:
                bucket = b
                break
        # Positional pop keeps duplicate-key fires (the same response text twice in one
        # session) deterministic rather than all inheriting the first label.
        if bucket:
            prior = bucket.popleft()
            # A fire is indexed under several keys; drop it from the others so it cannot be
            # consumed twice by a later fire matching on a different key.
            for k in _join_keys(prior):
                other = index.get(k)
                if other is not None and other is not bucket:
                    try: other.remove(prior)
                    except ValueError: pass
            if prior.get("label") in ("TP", "FP"):
                if source_auditable:
                    # v2 -> v2: the prior label was formed on evidence that CONTAINED the
                    # match. Carry it intact; it is audit-clean.
                    f["label"] = prior["label"]
                    f["rationale"] = prior.get("rationale", "")
                    f["label_provenance"] = "carried-auditable"
                    stats["carried"] += 1
                else:
                    # v1 -> v2: the prior label was formed on a HEAD EXCERPT that provably
                    # could not show the match. Carrying it would LAUNDER exactly what the
                    # consumer gate rejects: the artifact would end up schema_v=2 with
                    # evidence_kind set, and admit promotion on pre-fix labels. (Found by
                    # adversarial review 2026-08-18, reproduced with positive AND negative
                    # control.) The reviewer's WORK is preserved as prior_rationale so
                    # nothing is lost, but the LABEL resets — it must be re-confirmed
                    # against evidence that can actually be seen.
                    f["label"] = "unlabeled"
                    f["prior_label"] = prior["label"]
                    f["prior_rationale"] = prior.get("rationale", "")
                    f["label_provenance"] = "reset-was-pre-evidence-fix"
                    stats["downgraded"] += 1
                continue
        stats["fresh"] += 1
    stats["orphaned"] = sum(
        1 for b in index.values() for f in b if f.get("label") in ("TP", "FP"))
    return stats


def _summarize(art: dict) -> str:
    pct = 100 * art["fires_total"] / max(art["corpus_size_responses"], 1)
    return (
        f"scanner={art['scanner_id']}  corpus={art['corpus_size_responses']} responses  "
        f"would-fire={art['fires_total']} ({pct:.2f}%)\n"
        f"fingerprint={art['regex_fingerprint']}\n"
        f"decision={art['decision']} (label every fire TP/FP; promotion requires confirmed_fp==0)"
    )


# ── Finalize: re-derive fires_labeled + confirmed_fp from the inline fire labels ──────
# fp_measure writes confirmed_fp=None + fires_labeled=0 at creation; a reviewer then sets
# each fires[].label to TP/FP. NOTHING re-derived those two fields from the inline labels —
# the "finalize step" that BOTH this module's docstring ("label each fire ... then re-derive
# confirmed_fp") AND evidence_gate._fp_artifact_admits_promotion's docstring ("deferred until
# ... the same finalize step that applies labels — documented limitation, not silent") name
# as the missing link in the IDEA-10413 FP-substance promotion gate. This closes it: with the
# finalizer wired to a trigger (cron/SessionStart), a labeled artifact's confirmed_fp is
# derived automatically and the gate can admit — no manual re-derivation, no silent stall.
# Idempotent. FAIL-LOUD: raises on a missing/unreadable/malformed artifact (a finalize that
# cannot verify the labels must NOT write a count). 'unlabeled' and 'uncertain' both count as
# NOT-resolved, so promotion stays held until every fire is a definite TP/FP.
_RESOLVED_LABELS = frozenset({"TP", "FP"})
_SCANNER_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# Sidecars qc_label_audit/ writes alongside the artifacts, keyed by the FINAL dot-segment of
# the stem: "<gate>.verdict.json" (verdict.py), ".raters.json" (rater_driver.py),
# ".anchor.json" (deterministic_anchor.py). Listed EXPLICITLY rather than inferred from
# "has a dot", so that only these are silently skipped by finalize_all() and every other
# non-artifact stem is surfaced. Extend when a new companion type is added — a missing entry
# produces a visible `unrecognized` row, never silence.
_COMPANION_SUFFIXES = frozenset({"verdict", "raters", "anchor"})


def _is_known_companion(stem: str) -> bool:
    """True if ``stem`` is a recognised qc_label_audit sidecar (``<valid-id>.<suffix>``)."""
    base, _, suffix = stem.rpartition(".")
    return bool(base) and suffix in _COMPANION_SUFFIXES and bool(_SCANNER_ID_RE.fullmatch(base))


def _artifact_auditable(art: dict) -> bool:
    """Can an auditor re-verify this artifact's labels FROM the artifact?

    Mirrors evidence_gate._fp_artifact_admits_promotion's auditability half. A v1 artifact
    stored a HEAD excerpt while every scanner matches on the TAIL, so its labels — however
    carefully assigned — cannot be checked against the thing that fired."""
    raw = art.get("schema_v", 1)
    if isinstance(raw, bool) or not isinstance(raw, int):
        # `"²".isdigit()` is True but `int("²")` RAISES — a real uncaught crash in a function
        # the daily cron calls (cross-family review, gpt-5.6-sol, 2026-08-18; reproduced).
        # Parse defensively: any unparseable version is "pre-fix", never "new enough".
        try:
            raw = int(str(raw).strip())
        except (TypeError, ValueError):
            return False
    if raw < 2 or art.get("evidence_kind") not in EVIDENCE_KIND_VALUES:
        return False
    # THIRD check, previously omitted: the gate also validates PER FIRE that a non-blank
    # `matched` span exists. Mirroring only 2 of its 3 conditions made this monitor report
    # "auditable" for an artifact the real gate declines — a monitor that disagrees with the
    # thing it monitors (agent review, HIGH, 2026-08-18; reproduced live).
    fires = art.get("fires")
    if not isinstance(fires, list):
        return False
    # VACUOUS-TRUTH GUARD — mirrors evidence_gate.py. This was MISSING from the copy: an
    # artifact with `fires: []` and `fires_total: 95` passed the per-fire loop vacuously and
    # returned True while the real gate declined it. Masked in production because finalize()
    # normalises counts first, but the unit tests call this directly on raw dicts, and a
    # predicate whose docstring claims parity must actually have it. Found twice, independently.
    total = art.get("fires_total")
    if isinstance(total, int) and len(fires) != total:
        return False
    for f in fires:
        if not isinstance(f, dict):
            return False
        m = f.get("matched")
        if not isinstance(m, list) or not any(isinstance(x, str) and x.strip() for x in m):
            return False
    return True


def _artifact_admits(art: dict) -> bool:
    """Mirror of evidence_gate._fp_artifact_admits_promotion's admit conditions
    (evidence_gate.py:682-695) — the promotion bar this finalizer feeds. Used to detect the
    not-ready → ready TRANSITION so a cron --finalize-all notifies only on a genuine change."""
    total, labeled, cfp = art.get("fires_total"), art.get("fires_labeled"), art.get("confirmed_fp")
    return bool(isinstance(total, int) and isinstance(labeled, int) and total > 0
                and labeled == total
                and isinstance(cfp, int) and not isinstance(cfp, bool) and cfp == 0
                # AUDITABILITY IS PART OF THE BAR, not a display annotation. Without this,
                # relabelling a101's 16 FPs to TP — the documented next step — produced
                # newly_ready=True on an artifact the real gate DECLINES, printing
                # "ready-for-promotion ... [UNAUDITABLE]" in one line and firing a
                # high-priority "Ready for blocking promotion" push pointing the wrong way.
                # Reproduced live by an independent code review, 2026-08-18. The SUMMARY
                # marker was added for this in an earlier commit; the two ACTIONS it should
                # have gated — this predicate and _notify_ready — were missed.
                and _artifact_auditable(art))


def finalize(scanner_id: str) -> dict:
    """Re-derive fires_labeled + confirmed_fp from the artifact's inline fire labels, write
    them back, and update ``decision``. Returns a summary dict (``newly_ready`` is the
    not-ready→ready TRANSITION, not the steady state). Raises (fail-loud) on an invalid id /
    missing / unreadable / malformed artifact — never writes a count it could not verify."""
    if not _SCANNER_ID_RE.fullmatch(scanner_id):
        raise ValueError(
            f"invalid scanner_id {scanner_id!r} (expected [A-Za-z0-9_-]+; refusing path traversal)")
    path = ARTIFACT_DIR / f"{scanner_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no FP artifact for {scanner_id} at {path} "
            f"(run fp_measure.py {scanner_id} --write-artifact first)")
    art = json.loads(path.read_text())  # JSONDecodeError propagates → fail-loud
    # Positive identification, independent of the caller's file selection. finalize_all()'s
    # _SCANNER_ID_RE filter rejects DOTTED companion stems, but a dot-free sidecar
    # (e.g. "A13-raters.json") would pass it, and if it happened to carry a `fires` list it
    # would be silently rewritten. Every real FP artifact self-identifies via scanner_id;
    # companion sidecars never carry that field.
    if art.get("scanner_id") != scanner_id:
        raise ValueError(
            f"{path}: scanner_id={art.get('scanner_id')!r} does not match stem "
            f"{scanner_id!r} — not this scanner's artifact (companion sidecar, backup, or "
            f"a copied/renamed file); refusing to derive labels into it")
    fires = art.get("fires")
    if not isinstance(fires, list):
        raise ValueError(f"{path}: 'fires' is not a list — artifact malformed")
    if any(not isinstance(f, dict) for f in fires):
        raise ValueError(f"{path}: one or more fire entries are not objects — artifact malformed")
    declared = art.get("fires_total")
    if isinstance(declared, int) and declared != len(fires):
        raise ValueError(
            f"{path}: fires_total={declared} but len(fires)={len(fires)} — artifact malformed")
    was_ready = _artifact_admits(art)  # prior state (before overwrite) for transition detection
    # Computed ONCE. `_artifact_auditable` is an O(fires) scan and was being recomputed up to
    # four times per finalize() on a clean artifact (twice inside _artifact_admits, twice
    # directly) — ~4x the per-fire cost in exactly the ready-to-promote case that matters most.
    _auditable_now = _artifact_auditable(art)
    # A label key absent OR non-string (e.g. JSON null) is "unlabeled", not an unknown label.
    labels = [lab if isinstance((lab := f.get("label")), str) else "unlabeled" for f in fires]
    total = len(fires)
    resolved = sum(1 for lab in labels if lab in _RESOLVED_LABELS)
    fp_count = sum(1 for lab in labels if lab == "FP")
    unknown = sorted({lab for lab in labels
                      if lab not in _RESOLVED_LABELS and lab not in ("unlabeled", "uncertain")})
    complete = total > 0 and resolved == total
    art["fires_total"] = total
    art["fires_labeled"] = resolved
    # confirmed_fp is an int ONLY when every fire is resolved; None otherwise keeps the gate
    # held (evidence_gate requires confirmed_fp == int 0 AND fires_labeled == fires_total).
    art["confirmed_fp"] = fp_count if complete else None
    if complete and fp_count == 0 and not _auditable_now:
        # Self-contradiction guard: "ready-for-promotion" next to "[UNAUDITABLE]" told the
        # reader two opposite things on one line.
        art["decision"] = ("hold: confirmed_fp==0 and fully labeled, but the artifact is "
                           "PRE-EVIDENCE-FIX — its labels cannot be re-verified from it. "
                           "Re-run fp_measure.py <id> --write-artifact, then re-label.")
    elif complete and fp_count == 0:
        art["decision"] = "ready-for-promotion (confirmed_fp==0, fully labeled)"
    elif complete:
        art["decision"] = f"hold: confirmed_fp={fp_count} (FP present in fire set)"
    else:
        art["decision"] = f"pending-labeling ({resolved}/{total} resolved)"
    art["finalized_at"] = datetime.now(timezone.utc).isoformat()
    # Atomic: write to a sibling temp then rename. Two processes read-modify-write these
    # files — the 08:30 cron and an interactive qc_label_audit/verdict.py run, which itself
    # writes the artifact and then calls finalize() on it. A bare write_text truncates before
    # it writes, so a reader landing mid-write on the 808KB artifact gets a JSONDecodeError,
    # and nothing in this call chain retries. os.replace is atomic within one filesystem.
    # The .json.tmp<pid> suffix cannot be picked up by finalize_all()'s "*.json" glob.
    _atomic_write_text(path, json.dumps(art, indent=2))
    return {
        "scanner_id": scanner_id, "fires_total": total, "fires_labeled": resolved,
        "confirmed_fp": art["confirmed_fp"], "decision": art["decision"],
        "newly_ready": _artifact_admits(art) and not was_ready, "unknown_labels": unknown,
        # Surfaced so the DAILY job reports the real health signal. Without this the cron
        # prints "hold: confirmed_fp=16" for an artifact whose 95 labels were formed on
        # evidence that could not show the match — a number that looks like a measurement
        # and is not one. Measured 2026-08-18: 4 of 5 artifacts, 155 labels.
        "auditable": _auditable_now,
    }


def finalize_all() -> list[dict]:
    """Finalize every artifact in ARTIFACT_DIR. A per-artifact error is CAUGHT and returned
    as an ``error`` row (fail-loud per artifact: a broken one is reported, never silently
    skipped, and does not abort the batch).

    Companion sidecars written by qc_label_audit (``<gate>.verdict.json`` /
    ``.raters.json`` / ``.anchor.json``) and timestamped backups share this directory. They
    are NOT artifacts. Selection reuses ``_SCANNER_ID_RE`` — the SAME predicate ``finalize()``
    enforces — rather than a suffix denylist, so the filter can never drift out of sync with
    the validator and needs no edit when a fourth companion type appears (a denylist fails
    OPEN on every future addition, and the companion writers in qc_label_audit/ have no
    reason to know this module exists).

    A misrouted companion is never a corrupted artifact: the id check runs before any path
    is derived, so getting this wrong is an alerting concern, not a data-integrity one."""
    results: list[dict] = []
    if not ARTIFACT_DIR.exists():
        return results
    for p in sorted(ARTIFACT_DIR.glob("*.json")):
        if not _SCANNER_ID_RE.fullmatch(p.stem):
            if _is_known_companion(p.stem):
                continue  # expected sidecar — silent by design, the ONLY silent case
            # Unknown non-artifact stem: a typo'd/renamed/copied artifact ("A13 .json",
            # "A13(1).json") must NOT vanish. Silently skipping it would trade the noise
            # this filter removes for blindness. Reported as a distinct `unrecognized` row (not
            # `error`) so a caller can exit 0 on expected sidecars while still surfacing an
            # unrecognised file.
            results.append({"scanner_id": p.stem, "status": "unrecognized",
                            "reason": "stem is not a valid scanner id and not a known "
                                       "companion suffix — renamed, copied, or typo'd?"})
            continue
        try:
            results.append({**finalize(p.stem), "status": "ok"})
        except Exception as e:  # noqa: BLE001 — report every failure, never silent
            results.append({"scanner_id": p.stem, "status": "error",
                            "error": f"{type(e).__name__}: {e}"})
    return results


def _notify_ready(ready_ids: list[str]) -> tuple[int, str]:
    """Push when scanner(s) become promotion-ready. Returns (n_owed, status).

    Returns a STATUS rather than None because `newly_ready` is a ONE-SHOT transition:
    `finalize()` computes `was_ready` from the artifact BEFORE overwriting it, so the
    not-ready -> ready edge occurs exactly once and is then consumed forever. A push that
    failed on that single run used to be lost with no retry and no effect on the exit code —
    the PRIMARY signal of the whole promotion system was the one without a delivery
    guarantee, while the secondary unauditable alert had one. Found by an architecture
    review, 2026-08-18; it is the same defect as the unauditable path's, sitting one
    function above the fix for it.

    Never raises — a notify failure must not abort finalize — and the failure is PRINTED,
    returned as a status, and (via the caller) reflected in the exit code."""
    if not ready_ids:
        return 0, "no-new"
    import subprocess
    notify = Path.home() / "bin" / "notify.sh"
    if not notify.exists():
        print(f"[finalize] notify.sh absent — ready scanners NOT pushed: {ready_ids}", file=sys.stderr)
        return len(ready_ids), "notifier-absent"
    try:
        result = subprocess.run(
            [str(notify), "Scanner FP-gate",
             f"Ready for blocking promotion: {', '.join(ready_ids)}",
             "--priority", "high", "--channel", "auto"],
            timeout=20, check=False)
        if result.returncode != 0:
            print(f"[finalize] notify.sh exited {result.returncode} — push NOT confirmed for "
                  f"ready scanners: {ready_ids}", file=sys.stderr)
            return len(ready_ids), "send-failed"
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[finalize] notify failed ({e}) — ready scanners: {ready_ids}", file=sys.stderr)
        return len(ready_ids), "send-failed"
    return len(ready_ids), "ok"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via a sibling temp + os.replace, removing the temp if the rename fails.

    Three sites in this file hand-rolled temp+rename and none of them cleaned up: a failed
    os.replace left a `.tmp<pid>` sibling behind forever. Found by the atomicity test written
    for ONE of those sites — fixing only that site would have been the instance-not-invariant
    error this session already made once. os.replace is atomic within one filesystem, which is
    what makes a mid-write reader see either the old file or the new one, never a truncation."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass          # best-effort cleanup; the original file is intact either way
        raise


def _join_keys(fire: dict) -> list[tuple]:
    """ALL keys this fire can be matched by, most-specific first.

    A single preferred key is WRONG across the v1->v2 boundary and produced a total
    migration failure: a v1 artifact predates `record_digest`, so its fires only carry the
    legacy head-excerpt key, while newly-measured fires prefer the sha256 key. The two
    namespaces can never meet, so every prior label orphaned — 95 of 95 on a101, caught by
    the orphan warning rather than silently losing them.

    Returning BOTH lets the lookup try the digest (exact, collision-free) and fall back to the
    legacy key only when the stored side has no digest to offer."""
    keys = []
    d = fire.get("record_digest")
    if isinstance(d, str) and d:
        keys.append((fire.get("source"), "sha256", d))
    legacy = (fire.get("legacy_key") or fire.get("excerpt") or "")[:200]
    keys.append((fire.get("source"), "legacy", legacy))
    return keys


def _join_key(fire: dict) -> tuple:
    """Key used to carry a reviewer label from an old artifact onto a re-measured fire.

    PREFERS `record_digest` — a sha256 of the full response text. Falls back to the pre-fix
    head-excerpt prefix ONLY for artifacts written before that field existed.

    Why the digest is primary: the head-excerpt key is the first 200 characters of the
    response, and responses routinely share an opening ("Let me check...", a tool preamble).
    Two colliding fires would have their labels silently swapped by the positional pairing
    below — a mis-assigned TP/FP on a promotion decision, with nothing to detect it. Flagged
    as the highest-risk area by two independent reviews. External prior art (in-toto
    attestation v1) states the general rule: bind a label to a digest of the exact record.

    The digest is namespaced so a digest-keyed fire can never collide with a legacy-keyed one
    that happens to hash to the same string."""
    d = fire.get("record_digest")
    if isinstance(d, str) and d:
        return (fire.get("source"), "sha256", d)
    return (fire.get("source"), "legacy",
            (fire.get("legacy_key") or fire.get("excerpt") or "")[:200])


def _at_risk_ids(rows: list[dict]) -> set[str]:
    """Scanner ids that are UNAUDITABLE and already carry labels — the integrity-incident set.

    One source of truth. This predicate was written twice — inside the notifier and again for
    the SUMMARY count — with nothing enforcing that they agree. `_norm`'s docstring in this
    same file states exactly that principle for the excerpt/invariant pair; it had not been
    applied here."""
    return {str(r.get("scanner_id")) for r in rows
            if isinstance(r, dict) and not r.get("auditable", True)
            and isinstance(r.get("fires_labeled"), int) and r["fires_labeled"] > 0}


def _unauditable_state_path() -> Path:
    return Path.home() / ".claude" / "state" / "fp-gate-unauditable-seen.json"


def _notify_unauditable(rows: list[dict]) -> tuple[int, str]:
    """Alert ONCE when an artifact is first observed unauditable WITH labels on it.
    Returns (n_new, status) where status is one of ok / no-new / send-failed / notifier-absent.

    WHY NOT "only when it would promote" (the first design, rejected by review):
    that gated the alert on `confirmed_fp > 0` holding the artifact — but confirmed_fp was
    DERIVED FROM THE VERY LABELS whose integrity is in doubt. Using an unverifiable number to
    decide the unverifiability is not worth alerting about is CIRCULAR. (gpt-5.6-sol, HIGH,
    2026-08-18.) So the trigger is now the integrity fact itself: labels exist on evidence
    that cannot show the match.

    WHY IT IS STILL NOT ALERT-FATIGUE: state-transition, not level. An id is pushed once and
    recorded; repeats are suppressed until it leaves the set. This workspace measured 4,618
    advisory fires with ~0 effect — a DAILY push about a known backlog would be that. A
    one-time push about a newly discovered integrity incident is not.

    Whole body is guarded: this runs inside cron and must never raise."""
    try:
        at_risk = sorted(_at_risk_ids(rows))
        sp = _unauditable_state_path()
        try:
            seen = set(json.loads(sp.read_text()))
        except (OSError, ValueError, TypeError):
            seen = set()
        new_ids = [i for i in at_risk if i not in seen]
        if not new_ids:
            return 0, "no-new"

        def _persist() -> None:
            """Record ONLY after a confirmed send. Persisting first (the earlier design)
            permanently and silently dropped an artifact's one-time alert whenever notify.sh
            was absent or failed — verified with a two-run repro: no retry on the second run
            (agent review, MEDIUM, 2026-08-18). Retrying is the safer failure mode: if the
            notifier is permanently broken the run also exits non-zero, so the cron wrapper
            alerts on THAT rather than the condition being lost."""
            try:
                sp.parent.mkdir(parents=True, exist_ok=True)
                # Atomic, matching finalize() and the artifact write. A crash mid-write here
                # corrupts the alert-dedup state — the failure class already guarded
                # everywhere else in this file. This write had been left out of that discipline.
                _atomic_write_text(sp, json.dumps(sorted(set(at_risk) | seen)))
            except OSError as e:
                print(f"[finalize-all] could not persist unauditable state ({e}) — "
                      f"alert will repeat next run", file=sys.stderr)
        shown = new_ids[:10]
        more = f" (+{len(new_ids)-10} more)" if len(new_ids) > 10 else ""
        msg = (f"{', '.join(shown)}{more} carry reviewer labels on PRE-EVIDENCE-FIX evidence "
               f"(head excerpts that cannot show what matched). Their confirmed_fp is not "
               f"re-verifiable. Re-run: fp_measure.py <id> --write-artifact, then re-label.")
        notify = Path.home() / "bin" / "notify.sh"
        if not notify.exists():
            print(f"[finalize-all] notify.sh absent — NOT pushed (will retry next run): {msg}",
                  file=sys.stderr)
            return len(new_ids), "notifier-absent"
        import subprocess
        r = subprocess.run([str(notify), "FP-gate: unauditable artifacts carry labels",
                            msg, "--priority", "high", "--channel", "auto"],
                           timeout=20, check=False)
        if r.returncode != 0:
            print(f"[finalize-all] notify.sh exited {r.returncode} — push NOT confirmed: "
                  f"{new_ids}", file=sys.stderr)
            return len(new_ids), "send-failed"
        _persist()          # confirmed delivered — only now suppress repeats
        return len(new_ids), "ok"
    except Exception as e:  # noqa: BLE001 — cron safety; the status string carries the failure
        print(f"[finalize-all] unauditable-notify failed ({type(e).__name__}: {e})",
              file=sys.stderr)
        return 0, "send-failed"


def _run_finalize_all() -> int:
    """CLI entry for --finalize-all: finalize every artifact, print a summary, notify on
    newly-ready, and exit NON-ZERO if any artifact errored (so a cron wrapper surfaces it —
    no silent failure)."""
    results = finalize_all()
    if not results:
        print(f"[finalize-all] no FP artifacts in {ARTIFACT_DIR}")
        return 0
    errors = [r for r in results if r["status"] == "error"]
    unrecognized = [r for r in results if r["status"] == "unrecognized"]
    finalized = [r for r in results if r["status"] == "ok"]
    ready = [r["scanner_id"] for r in results if r.get("newly_ready")]
    for r in results:
        if r["status"] == "error":
            print(f"  ERROR {r['scanner_id']}: {r['error']}", file=sys.stderr)
        elif r["status"] == "unrecognized":
            print(f"  UNRECOGNIZED {r['scanner_id']}: {r['reason']}", file=sys.stderr)
        else:
            extra = f"  [unknown labels: {r['unknown_labels']}]" if r["unknown_labels"] else ""
            if not r.get("auditable", True):
                extra += "  [UNAUDITABLE: pre-evidence-fix artifact — labels cannot be " \
                         "re-verified from it; re-run fp_measure.py <id> --write-artifact]"
            print(f"  {r['scanner_id']}: {r['fires_labeled']}/{r['fires_total']} resolved, "
                  f"confirmed_fp={r['confirmed_fp']} — {r['decision']}{extra}")
    n_ready, ready_status = _notify_ready(ready)
    print(f"[finalize-all] READY-NOTIFY owed={n_ready} status={ready_status} "
          f"at={datetime.now(timezone.utc).isoformat()}", flush=True)
    # Machine-readable, single line, every run — counts by category so a zero exit still
    # carries the numbers and silence is never the success signal. Distinct prefix (not
    # `tail -1`) because stderr is unbuffered while stdout is block-buffered, so ERROR/UNRECOGNIZED
    # lines can physically precede this one in the cron log. flush=True so a SIGTERM'd run
    # still emits it (block-buffered stdout is otherwise lost when a signal cuts a run).
    unauditable = [r["scanner_id"] for r in finalized if not r.get("auditable", True)]
    n_at_risk = len(_at_risk_ids(finalized))   # name matches the printed `at_risk=` label
    # Machine-readable and emitted EVERY run, so "no unauditable artifacts" is an observed
    # zero rather than an absence of output. Silence must never be the success signal.
    print(f"[finalize-all] SUMMARY finalized={len(finalized)} ready={len(ready)} "
          f"errors={len(errors)} unrecognized={len(unrecognized)} "
          f"unauditable={len(unauditable)}"
          + (f" ({','.join(sorted(unauditable))})" if unauditable else "")
          + f" at_risk={n_at_risk}"
          + f" at={datetime.now(timezone.utc).isoformat()}", flush=True)
    # SUMMARY is emitted and FLUSHED before the notify call, which can block up to 20s: a
    # SIGTERM during that window must not cost the whole run's record. (Moving notify ahead of
    # it — a previous reviewer's suggestion, applied and then caught by the next leg — traded
    # a lost notify-status for a lost SUMMARY, which is strictly worse.) The notify OUTCOME
    # therefore rides its own second line; `at_risk=N` above already states an alert was owed,
    # so a missing NOTIFY line is itself readable as "owed but never reported".
    n_new, notify_status = _notify_unauditable(finalized)
    print(f"[finalize-all] NOTIFY owed={n_at_risk} new={n_new} status={notify_status} "
          f"at={datetime.now(timezone.utc).isoformat()}", flush=True)
    # Exit code means "the tool failed" — the sole meaning a cron `|| notify.sh` wrapper can
    # carry, and what this job's hardcoded alert message already asserts. An unrecognised
    # stem is a real signal but NOT a tool failure, so it rides the summary line, not the
    # exit code: routing it here would make the constant-title cron alert fire on every
    # unrecognised stem, indistinguishable from a genuine tool failure.
    # A high-priority alert that silently failed to send is a FAILURE of this job: the cron
    # wrapper's `|| notify.sh` fires on non-zero exit and is the only consumed signal, so a
    # best-effort push whose failure left the exit code at 0 guaranteed nothing (review HIGH,
    # 2026-08-18). An owed-but-unconfirmed push now surfaces.
    # An OWED-but-unconfirmed push is a failure of this job: the cron wrapper's `||` is the
    # only consumed signal. Covers BOTH pushes now — the ready transition is one-shot, so a
    # silently-dropped ready alert can never be retried.
    if ready_status in ("send-failed", "notifier-absent") and n_ready:
        print(f"[finalize-all] exiting non-zero: {n_ready} scanner(s) became promotion-READY "
              f"but the push could not be confirmed (status={ready_status}) — this transition "
              f"is one-shot and will NOT recur", file=sys.stderr)
        return 1
    if notify_status in ("send-failed", "notifier-absent") and n_new:
        print(f"[finalize-all] exiting non-zero: {n_new} at-risk artifact(s) were owed a push "
              f"that could not be confirmed (status={notify_status})", file=sys.stderr)
        return 1
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-corpus FP measurement for evidence_gate scanners.")
    ap.add_argument("scanner_id", nargs="?", help="e.g. A97")
    ap.add_argument("--write-artifact", action="store_true",
                    help=f"write {ARTIFACT_DIR}/<scanner_id>.json")
    ap.add_argument("--corpus", default=CORPUS_GLOB, help="corpus glob (default: session transcripts)")
    ap.add_argument("--list", action="store_true", help="list registered scanner predicates")
    ap.add_argument("--finalize", action="store_true",
                    help="re-derive fires_labeled + confirmed_fp from the artifact's inline "
                         "labels for <scanner_id> and write them back (the FP-gate finalize step)")
    ap.add_argument("--finalize-all", action="store_true",
                    help="finalize every artifact in the fp-gate dir (cron/SessionStart entry); "
                         "exits non-zero if any artifact errored")
    args = ap.parse_args()

    if args.list:
        print("Registered scanner predicates:", ", ".join(sorted(SCANNER_PREDICATES)) or "(none)")
        return 0
    if args.finalize_all:
        return _run_finalize_all()
    if args.finalize:
        if not args.scanner_id:
            ap.error("--finalize requires a scanner_id")
        try:
            summary = finalize(args.scanner_id)
        except Exception as e:  # fail-loud: clear message + non-zero exit, never silent
            print(f"finalize error ({args.scanner_id}): {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        print(f"[finalize] {summary['scanner_id']}: "
              f"{summary['fires_labeled']}/{summary['fires_total']} resolved, "
              f"confirmed_fp={summary['confirmed_fp']} — {summary['decision']}")
        if summary["unknown_labels"]:
            print(f"  WARNING unknown labels present (treated as unresolved): "
                  f"{summary['unknown_labels']}", file=sys.stderr)
        _n, _st = _notify_ready([summary["scanner_id"]] if summary["newly_ready"] else [])
        if _st in ("send-failed", "notifier-absent") and _n:
            print(f"[finalize] exiting non-zero: promotion-READY push not confirmed "
                  f"(status={_st}) — one-shot transition", file=sys.stderr)
            return 2
        return 0
    if not args.scanner_id:
        ap.error("scanner_id is required (or use --list)")

    try:
        art = measure(args.scanner_id, args.corpus)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if art["corpus_size_responses"] == 0:
        print(f"WARNING: corpus matched 0 assistant responses (glob={args.corpus!r}; "
              f"prefilter may have excluded every file) — measurement is not meaningful.",
              file=sys.stderr)
    print(_summarize(art))
    if args.write_artifact:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        out = ARTIFACT_DIR / f"{args.scanner_id}.json"
        try:
            stats = _carry_labels(art, out)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        art["label_carry"] = stats
        # Atomic, matching finalize(): a reader landing mid-write on a large artifact must
        # not get a JSONDecodeError, and nothing in this chain retries.
        _atomic_write_text(out, json.dumps(art, indent=2))
        print(f"\nArtifact written: {out}")
        print(f"  labels: {stats['carried']} carried forward (of {stats['prior_labeled']} "
              f"prior), {stats['fresh']} unlabeled, {stats['orphaned']} ORPHANED, "
              f"{stats['downgraded']} RESET (pre-evidence-fix)")
        if stats["downgraded"]:
            print(f"  NOTE {stats['downgraded']} label(s) were formed on pre-fix HEAD "
                  f"excerpts and have been RESET to unlabeled. Their prior label and "
                  f"rationale are preserved per-fire as prior_label / prior_rationale. "
                  f"Promotion stays held until they are re-confirmed against evidence that "
                  f"contains the match.", file=sys.stderr)
        if stats["orphaned"]:
            # Loud: an orphan means a previously-labeled fire no longer reproduces, i.e. the
            # predicate changed or the corpus lost a file. Either is a real event.
            print(f"  WARNING {stats['orphaned']} previously-labeled fire(s) did not "
                  f"reproduce — predicate change or corpus drift; investigate before "
                  f"trusting this measurement.", file=sys.stderr)
        print("  → label each fire TP/FP, set rationale, then --finalize to re-derive confirmed_fp.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
