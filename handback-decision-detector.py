#!/usr/bin/env python3
"""Detect a DECISION handed to the user without a recommendation. (user request 2026-08-13)

WHY THIS EXISTS — the inversion, located at source.

`escalation_gate.is_incomplete_recommendation` is a COMPLETENESS check. Its GATE A
requires ALL THREE scaffolding markers -- "(Recommended)", "Objective:", "Options" --
to be PRESENT before it fires, behind a cheap bail:

    if not ("recommended" in tl and "objective" in tl and "option" in tl):
        return False, []

So it catches a HALF-BUILT recommendation and is structurally blind to a MISSING one.
A response that hands over a decision with no scaffolding at all exits on the first
check. Measured consequence: one agent handed the user bare decisions in six
consecutive closes and tripped nothing, because it never emitted scaffolding.

The user's framing, which is the fix: the trigger belongs on the HANDBACK, not on the
recommendation. Whenever a response implicitly or explicitly hands something back,
assess whether it is in fact a request for authorization, instructions, or a choice --
and if so, the multi-item recommendation is owed.

This is deliberately NOT wired into any gate. evidence_gate.py:90-106 requires a
zero-confirmed-FP artifact before any new blocking scanner exists. This script IS that
artifact: run it over the historical corpus, hand-adjudicate the hits, and only then
decide whether it earns advisory or blocking tier.

Usage:
  handback-decision-detector.py --text-file RESPONSE.txt      # one response
  handback-decision-detector.py --corpus [--limit N] [--sample N] [--json]
"""
import argparse
import json
import re
import sys
from pathlib import Path

TRANSCRIPT_DIRS = [
    Path.home() / ".claude" / "projects" / "-home-ichardart-dev",
    Path.home() / ".claude" / "projects" / "-home-ichardart",
]

# --- The You: line -----------------------------------------------------------
# Anchored to line start; tolerate bold. Captures to end of line only: a decision
# stated on the You: line is the unit of interest, and scanning further would pull
# in unrelated trailing prose.
YOU_LINE_RE = re.compile(r"(?im)^\s*(?:\*{2}\s*)?You:\*{0,2}\s*(.+)$")

# --- Explicit no-handback: the documented closures (CLAUDE.md tail convention) ---
NO_HANDBACK_RE = re.compile(
    r"(?i)^\s*nothing\b(?:\s*[—\-–]\s*(?:complete|agent handles next))?\s*\.?\s*$"
)

# --- Decision shape ----------------------------------------------------------
# Two families, deliberately separated: an AUTHORIZATION ask and a CHOICE ask.
# An INSTRUCTION ("run this command") is NOT a decision and must not fire -- that
# distinction is the main false-positive risk and is why these are narrow.
AUTHORIZE_RE = re.compile(
    r"(?i)\b(?:approve|authorriz|authoris|authorize|authorization|sign[- ]off"
    r"|give\s+the\s+go[- ]ahead|permission\s+to)\w*"
)
# MEASURED FP CLASS — do not remove without re-measuring. First corpus run fired on
# 45.26% of You:-bearing responses and 8 of 8 hand-adjudicated hits were this single
# class: "approve the Gmail tool permission prompt". Clicking a permission dialog is a
# MECHANICAL UI ACTION, not a decision, and owes no recommendation. The word "approve"
# alone cannot tell them apart; the object of the approval can.
TOOL_PERMISSION_RE = re.compile(
    r"(?i)(?:permission|connector|tool\s+prompt|allowlist|allow-list|settings\.json"
    r"|oauth|re-?auth|interactiv\w*|pre-?authoriz|grant\s+(?:the\s+)?\w*\s*(?:tool|scope|access)"
    r"|mcp__\w+|tool\s+access|when\s+prompted|in\s+the\s+terminal)"
)

CHOICE_RE = re.compile(
    r"(?i)(?:"
    r"\byour\s+(?:call|choice|decision|judg)\w*"
    r"|\bgenuinely\s+yours\b|\byours\s+to\s+(?:make|decide|take|authorize|authorise)\b"
    r"|\b(?:decide|choose|pick)\s+(?:whether|which|between|if)\b"
    r"|\bwhether\s+to\b"
    r"|\b(?:nudge|keep|kill|drop|ship|revert)\s+or\s+(?:nudge|keep|kill|drop|ship|revert|proceed)\b"
    r"|\bdecisions?\s+(?:that\s+are\s+)?(?:genuinely\s+)?yours\b"
    r"|\bup\s+to\s+you\b|\bfor\s+you\s+to\s+decide\b"
    r")"
)

# --- Recommendation present? -------------------------------------------------
# Byte-identical to shared/recommendation_markers.REC_LABEL_RE. Duplicated rather
# than imported because this script must run outside the hooks package; a drift test
# belongs with it if this is ever promoted (see PROMOTION NOTE at the bottom).
REC_LABEL_RE = re.compile(r"(?i)\(\s*recommended\s*\)")
# A prose recommendation without the literal label still counts as *having thought it
# through* for the purpose of this check; the label contract is enforced elsewhere
# (escalation_gate). Firing on a present-but-unlabelled recommendation would duplicate
# that gate and manufacture FPs.
REC_PROSE_RE = re.compile(
    r"(?i)\bI\s+recommend\b|\brecommendation\b|\bI\s+would\s+(?:recommend|choose|take)\b"
)


def find_handback_decisions(text: str) -> list[dict]:
    """Return one record per You: line that hands over a DECISION with no recommendation.

    Fail-open: any error yields []. The caller treats [] as "clean", so a crash must
    never read as a finding -- the inverse would be a red-on-working detector.
    """
    try:
        if not text:
            return []
        has_rec = bool(REC_LABEL_RE.search(text) or REC_PROSE_RE.search(text))
        out = []
        for m in YOU_LINE_RE.finditer(text):
            you = m.group(1).strip()
            if NO_HANDBACK_RE.match(you):
                continue
            if TOOL_PERMISSION_RE.search(you):
                continue          # clicking a permission dialog is not a decision
            kinds = []
            if AUTHORIZE_RE.search(you):
                kinds.append("authorization")
            if CHOICE_RE.search(you):
                kinds.append("choice")
            if not kinds:
                continue          # an instruction or a status line, not a decision
            if has_rec:
                continue          # a recommendation exists somewhere in the response
            out.append({"you_line": you[:240], "kinds": kinds})
        return out
    except Exception:
        return []


def iter_assistant_texts(limit_files=None, min_words=60):
    """Yield (session_file, text) for assistant messages across both transcript dirs.

    BOTH directories -- a prior measurement in this workspace was inflated ~80% by
    reading only one of them.
    """
    files = []
    for d in TRANSCRIPT_DIRS:
        if d.is_dir():
            files.extend(sorted(d.glob("*.jsonl")))
    if limit_files:
        files = files[-limit_files:]
    for f in files:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("type") != "assistant":
                        continue
                    content = obj.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        continue
                    t = " ".join(b.get("text", "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text")
                    if t and len(t.split()) >= min_words:
                        yield f.name, t
        except OSError:
            continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file")
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="most recent N transcripts")
    ap.add_argument("--sample", type=int, default=12, help="hits to print for adjudication")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.text_file:
        hits = find_handback_decisions(Path(a.text_file).read_text(encoding="utf-8"))
        print(json.dumps(hits, indent=2) if a.json else
              (f"{len(hits)} handback-decision(s) without a recommendation" +
               "".join(f"\n  [{'+'.join(h['kinds'])}] {h['you_line']}" for h in hits)))
        return 1 if hits else 0

    if not a.corpus:
        ap.print_help()
        return 2

    scanned = with_you = fired = 0
    samples = []
    for name, text in iter_assistant_texts(limit_files=a.limit):
        scanned += 1
        if not YOU_LINE_RE.search(text):
            continue
        with_you += 1
        hits = find_handback_decisions(text)
        if hits:
            fired += 1
            if len(samples) < a.sample:
                samples.append((name, hits[0]))

    print(f"assistant responses scanned : {scanned}")
    print(f"  with a You: line          : {with_you}")
    print(f"  FIRED (decision, no rec)  : {fired}"
          + (f"   ({100*fired/with_you:.2f}% of You:-bearing)" if with_you else ""))
    print("\nSAMPLE FOR HAND-ADJUDICATION — each must be judged individually.")
    print("A hit is a TRUE POSITIVE only if the You: line really asks the user to")
    print("authorize or choose AND the response offers no recommendation anywhere.")
    for name, h in samples:
        print(f"\n  {name[:8]}  [{'+'.join(h['kinds'])}]\n    {h['you_line']}")
    print("\nNOTE: this rate is NOT an FP rate. It is a FIRE rate. The FP rate requires")
    print("reading every sampled hit by hand. Do not promote this scanner on this number.")
    return 0


# ── MEASUREMENT, 2026-08-13 (this IS the zero-confirmed-FP artifact) ──────────
# Corpus: 197 assistant responses (>=60 words) across BOTH transcript dirs; 137 carry
# a You: line. Hand-adjudicated at every step -- the fire rate is not an FP rate.
#
#   v1  45.26% (62/137)  8/8 sampled were ONE class: "approve the Gmail tool
#                        permission prompt". A permission dialog is a mechanical UI
#                        action, not a decision. -> TOOL_PERMISSION_RE
#   v2   3.65%  (5/137)  hand-adjudicated 5/5: 3 FP (residual `mcp__` tool-name
#                        approvals), 2 TP. -> excluded mcp__/tool access/when prompted
#   v3   1.46%  (2/137)  both are the SAME two hits already adjudicated TRUE POSITIVE:
#                        "retry or accept NO_REPLY" and "archival is optional -- your
#                        call". ZERO CONFIRMED FALSE POSITIVES.
#
# Two-sided suite: 8 cases, all passing -- 3 must-fire (incl. the verbatim You: line
# from this session's own 6th failure) and 5 must-be-silent (permission, tool access,
# instruction, "Nothing", and a response that DOES carry a recommendation). The silent
# direction is the one that must be constructed, and both FP classes lived there.
#
# NOT PROMOTED. Wiring a scanner into the Stop hook stack is HIGH_RISK and is the
# user's authorization to give. This file is the evidence, not the enforcement.
# PROMOTION NOTE. Before this becomes advisory or blocking:
#   1. Hand-adjudicate a sample; record confirmed FPs (evidence_gate.py:90-106).
#   2. Add a drift test asserting REC_LABEL_RE here is byte-identical to
#      shared/recommendation_markers.REC_LABEL_RE.
#   3. Two-sided tests: it must FIRE on a bare handback and stay SILENT on an
#      instruction ("You: run X") and on "You: Nothing". The silent direction is the
#      one that has to be constructed, and construction is where the defects live.
if __name__ == "__main__":
    sys.exit(main())
