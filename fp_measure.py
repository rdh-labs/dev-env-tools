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
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

CORPUS_GLOB = str(Path.home() / ".claude/projects/-home-ichardart-dev/*.jsonl")
ARTIFACT_DIR = Path.home() / ".claude/logs/fp-gate"
SCHEMA_V = 1

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


SCANNER_PREDICATES: dict[str, tuple[Callable[[str], bool], list[re.Pattern], str | None]] = {
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
    files = sorted(glob.glob(corpus_glob))
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
        if prefilter is not None and prefilter not in raw.lower():
            continue   # necessary-condition skip (case-insensitive substring) — no JSON parse
        files_scanned += 1
        texts, failed = _assistant_texts_from_raw(raw)
        parse_failures += failed
        for text in texts:
            if not text:
                continue
            total_scanned += 1
            if predicate(text):
                excerpt = text.strip()[:600].replace("\n", " ")
                fires.append({
                    "excerpt": excerpt,
                    "source": os.path.basename(fp),
                    "label": "unlabeled",   # reviewer sets TP / FP
                    "rationale": "",
                })
    return {
        "scanner_id": scanner_id,
        "schema_v": SCHEMA_V,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "regex_fingerprint": _fingerprint(fingerprint_patterns),
        "corpus_ref": "claude-projects-jsonl",
        "corpus_files_total": len(files),
        "corpus_files_scanned": files_scanned,
        "corpus_parse_failures": parse_failures,
        "corpus_size_responses": total_scanned,
        "fires_total": len(fires),
        "fires_labeled": 0,
        "confirmed_fp": None,         # None = not yet derived; set to count of FP labels once every fire is labeled (must be 0 to promote)
        "decision": "pending-labeling",
        "harness": "fp_measure.py",
        "falsifier": "a fire labeled TP that is actually a legitimate (non-violating) response",
        "fires": fires,
    }


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
# produces a visible `skipped` row, never silence.
_COMPANION_SUFFIXES = frozenset({"verdict", "raters", "anchor"})


def _is_known_companion(stem: str) -> bool:
    """True if ``stem`` is a recognised qc_label_audit sidecar (``<valid-id>.<suffix>``)."""
    base, _, suffix = stem.rpartition(".")
    return bool(base) and suffix in _COMPANION_SUFFIXES and bool(_SCANNER_ID_RE.fullmatch(base))


def _artifact_admits(art: dict) -> bool:
    """Mirror of evidence_gate._fp_artifact_admits_promotion's admit conditions
    (evidence_gate.py:682-695) — the promotion bar this finalizer feeds. Used to detect the
    not-ready → ready TRANSITION so a cron --finalize-all notifies only on a genuine change."""
    total, labeled, cfp = art.get("fires_total"), art.get("fires_labeled"), art.get("confirmed_fp")
    return bool(isinstance(total, int) and isinstance(labeled, int) and total > 0
                and labeled == total
                and isinstance(cfp, int) and not isinstance(cfp, bool) and cfp == 0)


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
    # would be silently rewritten. Every real artifact self-identifies (verified 2026-08-04:
    # A13/A97/a101/b123 all carry scanner_id == stem; all 6 companions carry no scanner_id).
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
    if complete and fp_count == 0:
        art["decision"] = "ready-for-promotion (confirmed_fp==0, fully labeled)"
    elif complete:
        art["decision"] = f"hold: confirmed_fp={fp_count} (FP present in fire set)"
    else:
        art["decision"] = f"pending-labeling ({resolved}/{total} resolved)"
    art["finalized_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(art, indent=2))
    return {
        "scanner_id": scanner_id, "fires_total": total, "fires_labeled": resolved,
        "confirmed_fp": art["confirmed_fp"], "decision": art["decision"],
        "newly_ready": _artifact_admits(art) and not was_ready, "unknown_labels": unknown,
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

    Before this filter the glob ingested all 6 companions, each raising 'invalid scanner_id'
    — 6 of 10 rows per run were phantoms, the batch exited non-zero every day, and the
    cron's ``|| notify.sh`` fired 48 times over 24 days with one identical body. No companion
    was ever corrupted (the id check precedes the path computation), so this is alarm
    hygiene, not data integrity. (2026-08-04)"""
    results: list[dict] = []
    if not ARTIFACT_DIR.exists():
        return results
    for p in sorted(ARTIFACT_DIR.glob("*.json")):
        if not _SCANNER_ID_RE.fullmatch(p.stem):
            if _is_known_companion(p.stem):
                continue  # expected sidecar — silent by design, the ONLY silent case
            # Unknown non-artifact stem: a typo'd/renamed/copied artifact ("A13 .json",
            # "A13(1).json") must NOT vanish. Skipping it silently would restore the very
            # failure this filter exists to remove, inverted: noise -> blindness. Reported
            # as a distinct `skipped` row (not `error`) so a caller can exit 0 on expected
            # sidecars while still surfacing an unrecognised file. (Sonnet /ship, 2026-08-04)
            results.append({"scanner_id": p.stem,
                            "skipped": "stem is not a valid scanner id and not a known "
                                       "companion suffix — renamed, copied, or typo'd?"})
            continue
        try:
            results.append(finalize(p.stem))
        except Exception as e:  # noqa: BLE001 — report every failure, never silent
            results.append({"scanner_id": p.stem, "error": f"{type(e).__name__}: {e}"})
    return results


def _notify_ready(ready_ids: list[str]) -> None:
    """Best-effort push when scanner(s) become promotion-ready. Never raises (a notify
    failure must not abort finalize); the failure is PRINTED (not silent)."""
    if not ready_ids:
        return
    import subprocess
    notify = Path.home() / "bin" / "notify.sh"
    if not notify.exists():
        print(f"[finalize] notify.sh absent — ready scanners NOT pushed: {ready_ids}", file=sys.stderr)
        return
    try:
        result = subprocess.run(
            [str(notify), "Scanner FP-gate",
             f"Ready for blocking promotion: {', '.join(ready_ids)}",
             "--priority", "high", "--channel", "auto"],
            timeout=20, check=False)
        if result.returncode != 0:
            print(f"[finalize] notify.sh exited {result.returncode} — push NOT confirmed for "
                  f"ready scanners: {ready_ids}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[finalize] notify failed ({e}) — ready scanners: {ready_ids}", file=sys.stderr)


def _run_finalize_all() -> int:
    """CLI entry for --finalize-all: finalize every artifact, print a summary, notify on
    newly-ready, and exit NON-ZERO if any artifact errored (so a cron wrapper surfaces it —
    no silent failure)."""
    results = finalize_all()
    if not results:
        print(f"[finalize-all] no FP artifacts in {ARTIFACT_DIR}")
        return 0
    errors = [r for r in results if "error" in r]
    skipped = [r for r in results if "skipped" in r]
    finalized = [r for r in results if "error" not in r and "skipped" not in r]
    ready = [r["scanner_id"] for r in results if r.get("newly_ready")]
    for r in results:
        if "error" in r:
            print(f"  ERROR {r['scanner_id']}: {r['error']}", file=sys.stderr)
        elif "skipped" in r:
            print(f"  SKIPPED {r['scanner_id']}: {r['skipped']}", file=sys.stderr)
        else:
            extra = f"  [unknown labels: {r['unknown_labels']}]" if r["unknown_labels"] else ""
            print(f"  {r['scanner_id']}: {r['fires_labeled']}/{r['fires_total']} resolved, "
                  f"confirmed_fp={r['confirmed_fp']} — {r['decision']}{extra}")
    _notify_ready(ready)
    # Machine-readable, single line, every run — counts by category so a zero exit still
    # carries the numbers and silence is never the success signal. Distinct prefix (not
    # `tail -1`) because stderr is unbuffered while stdout is block-buffered, so ERROR/SKIPPED
    # lines can physically precede this one in the cron log. flush=True so a SIGTERM'd run
    # still emits it (buffered-stdout-lost-on-SIGTERM was measured this session).
    print(f"[finalize-all] SUMMARY finalized={len(finalized)} ready={len(ready)} "
          f"errors={len(errors)} skipped={len(skipped)} "
          f"at={datetime.now(timezone.utc).isoformat()}", flush=True)
    # Exit code means "the tool failed" — the sole meaning a cron `|| notify.sh` wrapper can
    # carry, and what this job's hardcoded alert message already asserts. An unrecognised
    # stem is a real signal but NOT a tool failure, so it rides the summary line, not the
    # exit code: routing it here would make the constant-title cron alert fire daily and
    # forever, rebuilding the 48-alert failure this change removes.
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
        _notify_ready([summary["scanner_id"]] if summary["newly_ready"] else [])
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
        out.write_text(json.dumps(art, indent=2))
        print(f"\nArtifact written: {out}\n  → label each fire TP/FP, set rationale, then re-derive confirmed_fp.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
