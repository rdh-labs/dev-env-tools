#!/usr/bin/env python3
"""
Backfill §8 trigger-vocabulary telemetry from session transcripts.

IDEA-10022 (2026-04-25). Iterates ~/.claude/projects/-home-ichardart-dev/*.jsonl,
extracts assistant text, applies A5_EXTENDED_TRIGGER_RE + _A33_ANOMALY_ACK_RE +
A5_EXTENDED_PROXIMITY_RE from evidence_gate.py, writes to
~/.claude/logs/anomaly-detection-backfill.jsonl (separate from live log to avoid
contamination of in-flight telemetry).

Idempotency key: (session_id, turn_index) — re-runs do not double-count.
Existing backfill rows with the same key are skipped on subsequent runs.

Usage:
  python3 backfill-section8-vocabulary.py --limit 30           # 30 most-recent
  python3 backfill-section8-vocabulary.py --limit 0            # all sessions
  python3 backfill-section8-vocabulary.py --dry-run            # count only

Companion to evidence_gate.py A5-Extended observational scanner.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

EVIDENCE_GATE_PATH = Path.home() / "dev/infrastructure/dev-env-config/claude/hooks/stop/evidence_gate.py"
SESSIONS_DIR = Path.home() / ".claude/projects/-home-ichardart-dev"
BACKFILL_LOG = Path.home() / ".claude/logs/anomaly-detection-backfill.jsonl"


def load_evidence_gate_symbols():
    spec = importlib.util.spec_from_file_location("eg", EVIDENCE_GATE_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return (
        m.A5_EXTENDED_TRIGGER_RE,
        m.A5_EXTENDED_PROXIMITY_RE,
        m._A5_FENCE_RE,
        m._A33_ANOMALY_ACK_RE,
        m.ANOMALY_GOV_CAPTURE_RE,
    )


def load_existing_keys() -> set[tuple[str, int]]:
    """Idempotency: read backfill log, return set of (session_id, turn_index) keys."""
    if not BACKFILL_LOG.exists():
        return set()
    keys = set()
    with open(BACKFILL_LOG) as f:
        for line in f:
            try:
                e = json.loads(line)
                key = (e.get("session_id", ""), e.get("turn_index", -1))
                if key[0] and key[1] >= 0:
                    keys.add(key)
            except json.JSONDecodeError:
                continue
    return keys


def extract_assistant_turns(jsonl_path: Path):
    """Yield (turn_index, text, timestamp) for each assistant text turn."""
    turn_idx = 0
    with open(jsonl_path) as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Schema: {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}, ...}
            # OR top-level message with role
            msg = event.get("message") or {}
            role = event.get("type") or msg.get("role")
            if role != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if not text_parts:
                continue
            text = "\n".join(text_parts)
            timestamp = event.get("timestamp") or msg.get("timestamp") or ""
            yield turn_idx, text, timestamp
            turn_idx += 1


def scan_turn(text: str, regexes) -> list[dict]:
    trigger_re, proximity_re, fence_re, ack_re, gov_re = regexes
    text_stripped = fence_re.sub("", text or "")
    matches = list(trigger_re.finditer(text_stripped))
    if not matches:
        return []
    ack_present = bool(ack_re.search(text_stripped))
    rows = []
    for m in matches:
        start = m.start()
        window = text_stripped[max(0, start - 200):start + 200]
        if not proximity_re.search(window):
            continue  # third-party-attributed finding
        gov_window = text_stripped[start:start + 200]
        gov_found = bool(gov_re.search(gov_window))
        snippet = text_stripped[max(0, start - 30):start + 80]
        rows.append({
            "phrase_matched": m.group().lower().strip(),
            "ack_present": ack_present,
            "governance_capture_found": gov_found,
            "snippet": snippet[:120],
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30,
                    help="Number of most-recent sessions to scan (0 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report counts only; do not write to backfill log")
    args = ap.parse_args()

    if not EVIDENCE_GATE_PATH.exists():
        print(f"ERROR: evidence_gate.py not at {EVIDENCE_GATE_PATH}", file=sys.stderr)
        return 2
    if not SESSIONS_DIR.exists():
        print(f"ERROR: sessions dir not at {SESSIONS_DIR}", file=sys.stderr)
        return 2

    regexes = load_evidence_gate_symbols()
    existing_keys = load_existing_keys()

    sessions = sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if args.limit > 0:
        sessions = sessions[:args.limit]

    BACKFILL_LOG.parent.mkdir(parents=True, exist_ok=True)
    new_rows = 0
    skipped_dup = 0
    sessions_with_hits = 0

    # Per-turn buffered writes: accumulate all matches for a turn in memory,
    # write atomically at turn boundary. Interrupted runs lose only the in-flight
    # turn (re-runs find turn_idx absent from existing_keys → re-scan from scratch).
    # Without buffering, partial writes for a turn would mark the key as "seen"
    # and permanently drop the un-written matches. (codex-ask MEDIUM finding 2026-04-25)
    out_f = None if args.dry_run else open(BACKFILL_LOG, "a")
    try:
        for jsonl_path in sessions:
            session_id = jsonl_path.stem
            had_hit = False
            for turn_idx, text, timestamp in extract_assistant_turns(jsonl_path):
                key = (session_id, turn_idx)
                if key in existing_keys:
                    skipped_dup += 1
                    continue
                rows = scan_turn(text, regexes)
                if not rows:
                    continue
                # Buffer this turn's entries, then write+flush atomically.
                turn_buffer = []
                for row in rows:
                    turn_buffer.append({
                        "timestamp": timestamp or datetime.now().isoformat(),
                        "pattern_matched": "anomaly_extended_trigger",
                        "session_id": session_id,
                        "turn_index": turn_idx,
                        "backfill": True,
                        **row,
                    })
                if out_f:
                    out_f.write("".join(json.dumps(e) + "\n" for e in turn_buffer))
                    out_f.flush()
                new_rows += len(turn_buffer)
                had_hit = True
            if had_hit:
                sessions_with_hits += 1
    finally:
        if out_f:
            out_f.close()

    print(f"Backfill complete:")
    print(f"  Sessions scanned: {len(sessions)}")
    print(f"  Sessions with hits: {sessions_with_hits}")
    print(f"  New rows written: {new_rows}")
    print(f"  Idempotency skips: {skipped_dup}")
    print(f"  Output: {BACKFILL_LOG} (dry-run)" if args.dry_run else f"  Output: {BACKFILL_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
