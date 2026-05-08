#!/usr/bin/env python3
"""
agent-token-budget baseline analyzer (IDEA-10081 Step 2).

Read-only summary-statistics tool over ~/.claude/logs/agent-token-budget.jsonl.
Produces the comparator metrics for IDEA-10081 §4.2.1 Step 4 decision gate
(2-week post-deploy of Arch-1):

  - breach rate (advisory + warn + block)
  - block rate
  - transcript-share distribution in blocks (mean / median / p90)
  - fire rate per unique session
  - component breakdown (mean tokens by source) for blocked entries

Usage:
  python3 agent_token_budget_baseline.py
  python3 agent_token_budget_baseline.py --snapshot
  python3 agent_token_budget_baseline.py --since 2026-04-24T00:00:00
  python3 agent_token_budget_baseline.py --compare baseline.json

Source: ~/dev/share/IDEA-10081-design-2026-05-04.md §4.2.1 Step 2.
Plan: ~/.claude/plans/frolicking-growing-scone.md Edit 4.
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".claude" / "logs" / "agent-token-budget.jsonl"
SNAPSHOT_DIR = Path.home() / ".claude" / "logs"

BREACH_ACTIONS = {"advisory", "warn", "block"}
BLOCK_ACTIONS = {"block"}


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        print(f"ERROR: invalid ISO-8601 date: {s!r} ({exc})", file=sys.stderr)
        sys.exit(2)


def load_entries(path: Path, since: datetime | None, until: datetime | None) -> list[dict]:
    entries: list[dict] = []
    with path.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = entry.get("timestamp")
            if since or until:
                if not ts:
                    continue
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if since and when < since:
                    continue
                if until and when > until:
                    continue
            entries.append(entry)
    return entries


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def summarize(entries: list[dict]) -> dict:
    total = len(entries)
    breaches = [e for e in entries if e.get("action") in BREACH_ACTIONS]
    blocks = [e for e in entries if e.get("action") in BLOCK_ACTIONS]

    sessions = {e.get("session_id") for e in entries if e.get("session_id")}
    block_sessions = {e.get("session_id") for e in blocks if e.get("session_id")}

    transcript_shares: list[float] = []
    component_tokens = {
        "prompt_tokens": [],
        "claude_md_tokens": [],
        "memory_md_tokens": [],
        "transcript_tokens": [],
        "mcp_skills_overhead": [],
    }
    totals_in_blocks: list[int] = []

    for e in blocks:
        tot = e.get("total_estimated_tokens", 0)
        trans = e.get("transcript_tokens", 0)
        if tot > 0:
            transcript_shares.append(trans / tot)
            totals_in_blocks.append(tot)
        for k in component_tokens:
            v = e.get(k)
            if isinstance(v, (int, float)):
                component_tokens[k].append(float(v))

    def safe_mean(vs: list[float]) -> float:
        return statistics.mean(vs) if vs else 0.0

    def safe_median(vs: list[float]) -> float:
        return statistics.median(vs) if vs else 0.0

    summary = {
        "log_path": str(LOG_PATH),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "total_dispatches": total,
            "unique_sessions": len(sessions),
        },
        "rates": {
            "breach_rate": len(breaches) / total if total else 0.0,
            "block_rate": len(blocks) / total if total else 0.0,
            "fire_rate_per_session": len(blocks) / len(sessions) if sessions else 0.0,
            "block_sessions": len(block_sessions),
        },
        "transcript_share_in_blocks": {
            "mean": safe_mean(transcript_shares),
            "median": safe_median(transcript_shares),
            "p90": percentile(transcript_shares, 0.9),
            "n": len(transcript_shares),
            "mean_pct": round(safe_mean(transcript_shares) * 100, 1),
            "median_pct": round(safe_median(transcript_shares) * 100, 1),
            "p90_pct": round(percentile(transcript_shares, 0.9) * 100, 1),
        },
        "block_component_means": {k: safe_mean(v) for k, v in component_tokens.items()},
        "block_total_estimated_tokens_mean": safe_mean(totals_in_blocks),
        "counts": {
            "total": total,
            "breaches": len(breaches),
            "blocks": len(blocks),
        },
    }
    return summary


def render_step4_verdict(current: dict, baseline: dict) -> dict:
    """Apply IDEA-10081 §4.2.1 Step 4 decision gate."""
    cur_share = current["transcript_share_in_blocks"]["mean"]
    base_share = baseline["transcript_share_in_blocks"]["mean"]
    transcript_savings = (base_share - cur_share) / base_share if base_share else 0.0

    cur_fire = current["rates"]["fire_rate_per_session"]
    base_fire = baseline["rates"]["fire_rate_per_session"]
    fire_drop = (base_fire - cur_fire) / base_fire if base_fire else 0.0

    if transcript_savings >= 0.10 and fire_drop >= 0.30:
        verdict = "Arch-3 with REDUCED scope (direct-risk-signal triggers only)"
    elif transcript_savings < 0.10:
        verdict = "Arch-3 with BROADER scope (direct + cumulative-pattern triggers)"
    elif transcript_savings > 0.25 and current["rates"]["block_rate"] < 0.10:
        verdict = (
            "Arch-3 with REDUCED scope (HIGH_RISK direct-signal still required; "
            "NEVER defer entirely per R1 v2)"
        )
    else:
        verdict = "Arch-3 with BROADER scope (default)"

    return {
        "transcript_savings": transcript_savings,
        "fire_rate_drop": fire_drop,
        "step4_verdict": verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=str, default=None, help="ISO-8601 lower bound")
    parser.add_argument("--until", type=str, default=None, help="ISO-8601 upper bound")
    parser.add_argument("--snapshot", action="store_true", help="Save snapshot to ~/.claude/logs/")
    parser.add_argument("--compare", type=str, default=None, help="Path to baseline JSON for delta")
    parser.add_argument("--log", type=str, default=str(LOG_PATH), help="Override log path")
    args = parser.parse_args()

    log_path = Path(args.log).expanduser()
    if not log_path.exists():
        print(f"ERROR: log not found: {log_path}", file=sys.stderr)
        return 2

    since = parse_iso(args.since)
    until = parse_iso(args.until)
    entries = load_entries(log_path, since, until)
    summary = summarize(entries)

    output: dict = {"summary": summary}

    if args.compare:
        baseline_path = Path(args.compare).expanduser()
        if not baseline_path.exists():
            print(f"ERROR: baseline not found: {baseline_path}", file=sys.stderr)
            return 2
        with baseline_path.open("r") as f:
            baseline = json.load(f).get("summary", {})
        output["delta"] = render_step4_verdict(summary, baseline)

    print(json.dumps(output, indent=2, default=str))

    if args.snapshot:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        out_path = SNAPSHOT_DIR / f"agent-token-budget-baseline-{ts}.json"
        with out_path.open("w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\nSnapshot written: {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
