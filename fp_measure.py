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

# ── Scanner fire-predicates ──────────────────────────────────────────────────
# Each entry maps a scanner_id to (predicate, pattern_sources, prefilter). ``predicate(text)``
# returns True if the scanner would fire on an assistant response ``text``. ``pattern_sources``
# is the list of raw regex pattern strings whose sha256 forms the fingerprint the gate binds to.
# ``prefilter`` is a cheap REQUIRED substring (or None): files whose raw text lacks it are
# skipped without JSON-parsing — a large speedup on the ~1096-file corpus (a measurement that
# otherwise exceeds 180s on 6GB WSL2). The prefilter MUST be a necessary condition of firing
# (the predicate can only match text containing it), else it would drop true fires.
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


SCANNER_PREDICATES: dict[str, tuple[Callable[[str], bool], list[str], str | None]] = {
    "A97": (
        _a97_fires,
        [_A97_ANOMALY_RE.pattern, _A97_SELF_DETECT_RE.pattern,
         _A97_USER_ATTRIB_RE.pattern, _A97_DETECT_FAIL_RE.pattern],
        "## Anomaly Analysis",   # prefilter: A97 can only fire inside an Anomaly Analysis section
    ),
}


# ── Corpus walk ──────────────────────────────────────────────────────────────
def _assistant_texts_from_raw(raw: str):
    """Yield each assistant-message text body from a pre-read JSONL string (one transcript file)."""
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        msg = obj.get("message") or obj
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            yield "".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )


def _fingerprint(pattern_sources: list[str]) -> str:
    h = hashlib.sha256()
    for p in pattern_sources:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return "sha256:" + h.hexdigest()


def measure(scanner_id: str, corpus_glob: str = CORPUS_GLOB) -> dict:
    """Replay scanner_id's predicate over the corpus; return the artifact dict (unlabeled)."""
    if scanner_id not in SCANNER_PREDICATES:
        raise SystemExit(
            f"No predicate registered for {scanner_id}. Add it to SCANNER_PREDICATES "
            f"(import its regexes from evidence_gate). Registered: {sorted(SCANNER_PREDICATES)}"
        )
    predicate, pattern_sources, prefilter = SCANNER_PREDICATES[scanner_id]
    files = sorted(glob.glob(corpus_glob))
    total_scanned = 0
    files_scanned = 0
    fires: list[dict] = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except OSError:
            continue
        if prefilter and prefilter not in raw:
            continue   # cheap necessary-condition skip — no JSON parse
        files_scanned += 1
        for text in _assistant_texts_from_raw(raw):
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
        "regex_fingerprint": _fingerprint(pattern_sources),
        "corpus_ref": "claude-projects-jsonl",
        "corpus_files_total": len(files),
        "corpus_files_prefiltered": files_scanned,
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Real-corpus FP measurement for evidence_gate scanners.")
    ap.add_argument("scanner_id", nargs="?", help="e.g. A97")
    ap.add_argument("--write-artifact", action="store_true",
                    help=f"write {ARTIFACT_DIR}/<scanner_id>.json")
    ap.add_argument("--corpus", default=CORPUS_GLOB, help="corpus glob (default: session transcripts)")
    ap.add_argument("--list", action="store_true", help="list registered scanner predicates")
    args = ap.parse_args()

    if args.list:
        print("Registered scanner predicates:", ", ".join(sorted(SCANNER_PREDICATES)) or "(none)")
        return 0
    if not args.scanner_id:
        ap.error("scanner_id is required (or use --list)")

    art = measure(args.scanner_id, args.corpus)
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
