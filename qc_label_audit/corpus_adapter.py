#!/usr/bin/env python3
"""corpus_adapter.py — log→transcript join → rater-window → fp_measure-schema artifact.

Part of the one-time QC-gate label audit (Dart vCQTno99zsDK; plan
~/.claude/plans/1-label-audit-scalable-mitten.md). Produces the corpus that
`rater_driver.py` / `deterministic_anchor.py` label and `verdict.py` scores.

WHY log-sourced (not fp_measure predicate-replay): the target gates are STATEFUL —
A13 (`check_gate_block_sentinel`) reads /tmp/.../gate_blocks.jsonl; b123 reads git +
escalation state. fp_measure.py replays `predicate(text)->bool` and structurally
cannot reproduce them. But each gate ALREADY LOGGED its fires, so we don't reproduce
the predicate — we take the recorded fires and JOIN each to its raw transcript to
extract the window a rater needs.

Reuses fp_measure's artifact SCHEMA (so fp_measure.finalize() can re-derive
confirmed_fp from the labels we collect) and the ~/.claude/logs/fp-gate/ location.

Verified structural facts this is built against (probed 2026-07-03):
  * a13-catches-vs-friction.jsonl: 30 `event:"fire"` rows; fire `timestamp` is naive
    LOCAL time (PDT) — DO NOT compare it to transcript UTC. Each fire's `block_ids[]`
    carries a UTC `<gate>|<ISO-Z>` timestamp → that is the timezone-safe join key and
    it anchors the window on the actual prior block.
  * b123-gate.jsonl: `ts` is explicit +00:00 UTC; fires at close; 5 test fixtures
    (`test*`/`*e2e*`/`*xxx*`) + ~505 fires whose session transcript is absent.
  * transcripts (~/.claude/projects/-home-ichardart-dev/<session_id>.jsonl): one JSON
    object per line; main-thread message turns have `isSidechain==false`,
    `message.role in {assistant,user}`, top-level ISO-`Z` `timestamp`; `message.content`
    is a str OR a list of blocks {"type":"text","text":...}. Many non-message record
    types (attachment, mode, ai-title, ...) are skipped.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path.home() / ".claude/projects/-home-ichardart-dev"
ARTIFACT_DIR = Path.home() / ".claude/logs/fp-gate"
LOG_DIR = Path.home() / ".claude/logs"
SCHEMA_V = 1
MAX_EXCERPT_CHARS = 24000  # keep full window but cap runaway sessions
# Real Claude sessions are named by a v4 UUID; gate-log test/rollback fixtures
# (base, test-b123-xxx111, rb-b123-mute, ...) are NOT UUID-shaped. A substring
# match on "e2e"/"test" false-positives on real UUIDs (e.g. 5c52e2ec-...), so the
# discriminator is UUID-shape, not substring.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _drop(reason: str, detail: str = "") -> None:
    """Surface a dropped/adjusted fire — never silent (plan: log() drops)."""
    print(f"[drop] {reason}{(': ' + detail) if detail else ''}", file=sys.stderr)


def _iter_log(path: Path):
    """Yield parsed JSON records from a gate log, one per line. A malformed line is
    surfaced via _drop() and skipped — never a raw traceback that aborts the build."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                _drop("malformed log line", f"{path.name}: {line[:80]}")


def _is_test_fixture(session_id: str) -> bool:
    """A fire is a synthetic fixture iff its session_id is not UUID-shaped."""
    return _UUID_RE.fullmatch(session_id or "") is None


def _ts_epoch(s: str) -> float | None:
    """Parse an ISO timestamp to a UTC epoch. Returns None if unparseable.
    A NAIVE timestamp (no tz) is treated as UTC and flagged by the caller — we never
    silently assume a local offset for a join key."""
    if not isinstance(s, str) or not s.strip():
        return None
    txt = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _turn_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


_TRANSCRIPT_CACHE: dict[str, list[dict] | None] = {}


def parse_transcript(session_id: str) -> list[dict] | None:
    """Return ordered main-thread message turns for a session, or None if the
    transcript file is absent. Each turn: {idx, role, ts, text} (ts = UTC epoch or None).
    File order is chronological; idx preserves it. Memoized: one session that fires a gate
    N times (A13 thrash / multiple b123 closes) re-uses one ~2MB parse, and the None result
    doubles as the existence check the b123 filter would otherwise stat() separately."""
    if session_id in _TRANSCRIPT_CACHE:
        return _TRANSCRIPT_CACHE[session_id]
    path = PROJECT_DIR / f"{session_id}.jsonl"
    if not path.exists():
        _TRANSCRIPT_CACHE[session_id] = None
        return None
    turns: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("isSidechain") is True:  # exclude subagent turns
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ("assistant", "user"):
                continue
            turns.append({
                "idx": len(turns),
                "role": role,
                "ts": _ts_epoch(obj.get("timestamp", "")),
                "text": _turn_text(msg),
            })
    _TRANSCRIPT_CACHE[session_id] = turns
    return turns


def _window_text(turns: list[dict], lo: int, hi: int) -> str:
    """Render turns[lo:hi] as a readable window (role-tagged), capped."""
    parts = []
    for t in turns[lo:hi]:
        body = t["text"].strip()
        if not body:
            continue
        parts.append(f"[{t['role'].upper()}]\n{body}")
    out = "\n\n".join(parts)
    return out[:MAX_EXCERPT_CHARS]


# ── A13 ───────────────────────────────────────────────────────────────────────
def _a13_anchor_epoch(fire: dict) -> tuple[float | None, bool]:
    """Timezone-safe A13 join anchor = EARLIEST block_ids UTC timestamp. Fallback to
    the naive fire `timestamp` (flagged) only if no block_id parses. Returns (epoch,
    used_fallback)."""
    best = None
    for bid in fire.get("block_ids") or []:
        # format "<gate>|<ISO-Z>"
        if not isinstance(bid, str) or "|" not in bid:
            continue
        ep = _ts_epoch(bid.split("|", 1)[1])
        if ep is not None and (best is None or ep < best):
            best = ep
    if best is not None:
        return best, False
    return _ts_epoch(fire.get("timestamp", "")), True  # naive fallback (flagged)


def build_a13(n_turns: int) -> dict:
    """Census of all A13 fire events → windowed artifact."""
    log = LOG_DIR / "a13-catches-vs-friction.jsonl"
    fires_out: list[dict] = []
    n_missing = n_naive_ts = n_past_end = n_no_anchor = n_test = 0
    for rec in _iter_log(log):
            if rec.get("event") != "fire":
                continue
            sid = rec.get("session_id", "")
            if _is_test_fixture(sid):
                n_test += 1
                _drop("a13 test-fixture", sid)
                continue
            anchor, used_fallback = _a13_anchor_epoch(rec)
            if anchor is None:
                n_no_anchor += 1
                _drop("a13 no parseable anchor ts", sid)
                continue
            if used_fallback:
                n_naive_ts += 1
            turns = parse_transcript(sid)
            if turns is None:
                n_missing += 1
                _drop("a13 missing transcript", sid)
                continue
            # firing assistant turn = first assistant turn at/after the block anchor
            start = next((t["idx"] for t in turns
                          if t["role"] == "assistant" and t["ts"] is not None and t["ts"] >= anchor),
                         None)
            if start is None:  # anchor after last turn — take the final assistant turn
                a_idx = [t["idx"] for t in turns if t["role"] == "assistant"]
                if not a_idx:
                    n_missing += 1
                    _drop("a13 no assistant turns", sid)
                    continue
                start = a_idx[-1]
                n_past_end += 1
                _drop("a13 anchor past last turn — using final turn", sid)
            # window forward until n_turns assistant turns collected
            asst_seen = 0
            hi = start
            for j in range(start, len(turns)):
                if turns[j]["role"] == "assistant":
                    asst_seen += 1
                hi = j + 1
                if asst_seen >= n_turns:
                    break
            fires_out.append({
                "excerpt": _window_text(turns, start, hi),
                "source": f"{sid}#{rec.get('timestamp','')}",
                "label": "unlabeled",
                "rationale": "",
                "_meta": {
                    "block_ids": rec.get("block_ids"),
                    "repeat_fires": rec.get("repeat_fires"),
                    "pending": rec.get("pending"),
                    "window_turns": hi - start,
                    "used_fallback_anchor": used_fallback,
                },
            })
    art = _artifact("A13", fires_out,
                    falsifier="a fire labeled TP where the prior block was spurious and no real anomaly existed")
    art["corpus_notes"] = {"n_missing_transcript": n_missing,
                           "n_naive_ts_fallback": n_naive_ts, "n_anchor_past_end": n_past_end,
                           "n_no_anchor": n_no_anchor, "n_test_fixture": n_test,
                           "criterion": "within N=%d turns: AA written AND blocked action progressed" % n_turns}
    return art


# ── b123 ──────────────────────────────────────────────────────────────────────
# Substring markers matched against the b123 gate's free-text concern strings
# (format e.g. "OUT:6(unreconciled-contradictions)", "AN:1(B1-absent)"). If the gate's
# wording changes, rows fall to STRATUM_OTHER — corpus_notes.strata_available surfaces it.
STRATUM_DISHONEST, STRATUM_ABSENT, STRATUM_OUT, STRATUM_OTHER = (
    "AN-dishonest", "AN-absent", "OUT", "other")


def _concern_stratum(concerns: list) -> str:
    """Coarse stratum key for stratified sampling."""
    joined = " ".join(concerns or [])
    if "dishonest" in joined:
        return STRATUM_DISHONEST
    if "absent" in joined:
        return STRATUM_ABSENT
    if joined.startswith("OUT") or "unreconciled" in joined:
        return STRATUM_OUT
    return STRATUM_OTHER


def build_b123(sample_n: int, seed: int, trailing: int) -> dict:
    """Stratified sample of joinable b123 `adequate:false` fires → fire-ts-anchored window."""
    log = LOG_DIR / "b123-gate.jsonl"
    joinable: list[dict] = []
    n_missing = n_test = 0
    for rec in _iter_log(log):
            if rec.get("adequate") is not False:
                continue
            sid = rec.get("session_id", "")
            if _is_test_fixture(sid):
                n_test += 1
                continue
            if parse_transcript(sid) is None:  # cached; doubles as existence check
                n_missing += 1
                continue
            joinable.append(rec)
    # stratified sample
    strata: dict[str, list] = {}
    for r in joinable:
        strata.setdefault(_concern_stratum(r.get("concerns", [])), []).append(r)
    rng = random.Random(seed)
    picked: list[dict] = []
    # oversample non-OUT strata to >=15 where available, then fill remainder from OUT
    for key in ("AN-absent", "AN-dishonest", "other"):
        pool = strata.get(key, [])
        rng.shuffle(pool)
        picked.extend(pool[:min(15, len(pool))])
    remaining = max(0, sample_n - len(picked))
    out_pool = strata.get("OUT", [])
    rng.shuffle(out_pool)
    picked.extend(out_pool[:remaining])
    rng.shuffle(picked)

    fires_out: list[dict] = []
    for rec in picked:
        sid = rec["session_id"]
        turns = parse_transcript(sid)
        if turns is None:
            n_missing += 1
            continue
        a_idx = [t["idx"] for t in turns if t["role"] == "assistant"]
        if not a_idx:
            continue
        # Anchor on THIS fire's ts (b123 fires at Stop), not the transcript's physical end:
        # a session may continue after an advisory b123 block or fire multiple times, so the
        # true closing turn for this fire is the last assistant turn at/before its ts.
        fire_ep = _ts_epoch(rec.get("ts", ""))
        if fire_ep is not None:
            at_or_before = [i for i in a_idx if turns[i]["ts"] is not None and turns[i]["ts"] <= fire_ep]
            anchor_idx = at_or_before[-1] if at_or_before else a_idx[-1]
        else:
            anchor_idx = a_idx[-1]
        end = anchor_idx + 1
        upto = [i for i in a_idx if i <= anchor_idx]
        lo = upto[-min(trailing, len(upto))]
        fires_out.append({
            "excerpt": _window_text(turns, lo, end),
            "source": f"{sid}#{rec.get('ts','')}",
            "label": "unlabeled",
            "rationale": "",
            "_meta": {"concerns": rec.get("concerns"),
                      "stratum": _concern_stratum(rec.get("concerns", [])),
                      "outstanding_contradictions": rec.get("outstanding_contradictions")},
        })
    art = _artifact("b123", fires_out,
                    falsifier="a fire labeled TP where every named concern was in fact spurious")
    art["corpus_notes"] = {"n_joinable_total": len(joinable), "n_missing_transcript": n_missing,
                           "n_test_fixture": n_test, "sample_requested": sample_n,
                           "strata_available": {k: len(v) for k, v in strata.items()},
                           "handling": "EXPLORATORY — precision-of-flag; re-measures upstream contradiction detector; recency-biased"}
    return art


def _artifact(scanner_id: str, fires: list[dict], falsifier: str) -> dict:
    return {
        "scanner_id": scanner_id,
        "schema_v": SCHEMA_V,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "corpus_ref": "gate-log-join+transcript-window",
        "harness": "qc_label_audit/corpus_adapter.py",
        "fires_total": len(fires),
        "fires_labeled": 0,
        "confirmed_fp": None,
        "decision": "pending-labeling",
        "falsifier": falsifier,
        "fires": fires,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Log→transcript corpus adapter for the QC label audit.")
    ap.add_argument("--gate", required=True, choices=["A13", "b123"])
    ap.add_argument("--n-turns", type=int, default=6, help="A13: assistant turns after the block")
    ap.add_argument("--sample", type=int, default=60, help="b123: stratified sample size")
    ap.add_argument("--trailing", type=int, default=20, help="b123: trailing assistant turns")
    ap.add_argument("--seed", type=int, default=20260703, help="b123: sampling seed (reproducible)")
    ap.add_argument("--write", action="store_true", help=f"write {ARTIFACT_DIR}/<gate>.json")
    args = ap.parse_args()

    if args.gate == "A13":
        art = build_a13(args.n_turns)
    else:
        art = build_b123(args.sample, args.seed, args.trailing)

    notes = art.get("corpus_notes", {})
    print(f"gate={art['scanner_id']}  fires_total={art['fires_total']}")
    print(f"corpus_notes={json.dumps(notes)}")
    if art["fires_total"] == 0:
        print("WARNING: 0 fires produced — nothing to label.", file=sys.stderr)
    if args.write:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        out = ARTIFACT_DIR / f"{art['scanner_id']}.json"
        out.write_text(json.dumps(art, indent=2))
        print(f"\nArtifact written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
