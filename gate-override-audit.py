#!/usr/bin/env python3
"""Audit assumption-gate overrides and test bypass activity.

Produces a compact operational view for governance:
- override volume (window + all-time)
- reason coverage and top reasons
- repeated bypass patterns
- test-bypass source breakdown
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_OVERRIDES = Path.home() / "dev" / "infrastructure" / "multi-check" / "logs" / "gate-overrides.jsonl"
DEFAULT_BYPASSES = Path.home() / ".claude" / "assumption-registry" / "test-bypasses.jsonl"
# Declared QC skips. Same decision-plus-reason shape as the two sources above, which is
# why this belongs here rather than in a sibling script. The field has been logged since
# 2026-05-30; nothing consumed it until 2026-08-10.
DEFAULT_SKIPS = Path.home() / ".claude" / "logs" / "session-end-no-handoff-t5.jsonl"
DEFAULT_HANDOFFS = Path.home() / ".claude" / "logs" / "handoff.jsonl"


def parse_iso_timestamp(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    records: List[Dict[str, Any]] = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def in_window(records: Iterable[Dict[str, Any]], cutoff_utc: datetime) -> List[Dict[str, Any]]:
    scoped: List[Dict[str, Any]] = []
    for record in records:
        ts = parse_iso_timestamp(str(record.get("timestamp", "")))
        if ts is None:
            continue
        if ts >= cutoff_utc:
            scoped.append(record)
    return scoped


def normalize_reason(reason: str) -> str:
    normalized = " ".join(reason.strip().lower().split())
    return normalized


def summarize_overrides(all_records: List[Dict[str, Any]], window_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    reasons = Counter()
    questions = Counter()
    reasons_missing = 0
    reason_quality_warnings = 0
    daily = Counter()

    for record in window_records:
        ts = parse_iso_timestamp(str(record.get("timestamp", "")))
        if ts is not None:
            daily[str(ts.date())] += 1

        question = str(record.get("question", "")).strip()
        if question:
            questions[question[:120]] += 1

        reason = str(record.get("override_reason", "")).strip()
        if not reason:
            reasons_missing += 1
            continue
        normalized_reason = normalize_reason(reason)
        reasons[normalized_reason] += 1
        if len(normalized_reason) < 12 or normalized_reason in {"n/a", "none", "skip", "quick check"}:
            reason_quality_warnings += 1

    total = len(window_records)
    missing_rate = (reasons_missing / total) if total else 0.0
    low_quality_rate = (reason_quality_warnings / total) if total else 0.0

    return {
        "all_time_count": len(all_records),
        "window_count": total,
        "reasons_missing_count": reasons_missing,
        "reasons_missing_rate": round(missing_rate, 4),
        "low_quality_reason_count": reason_quality_warnings,
        "low_quality_reason_rate": round(low_quality_rate, 4),
        "top_reasons": reasons.most_common(8),
        "top_questions": questions.most_common(8),
        "daily_counts": dict(sorted(daily.items())),
    }


def summarize_bypasses(all_records: List[Dict[str, Any]], window_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_source = Counter()
    by_pattern = Counter()

    for record in window_records:
        by_source[str(record.get("source", "unknown"))] += 1
        if record.get("pattern_matched"):
            by_pattern[str(record.get("pattern_matched"))] += 1

    return {
        "all_time_count": len(all_records),
        "window_count": len(window_records),
        "by_source": dict(by_source),
        "top_patterns": by_pattern.most_common(8),
    }


def summarize_skips(
    all_records: List[Dict[str, Any]],
    window_records: List[Dict[str, Any]],
    handoff_times: Dict[str, datetime],
) -> Dict[str, Any]:
    """Reversal rate for DECLARED QC skips — the agent's track record on skip judgements.

    A declared skip is REVERSED when the session that declared it logged a handoff STRICTLY
    AFTER the earliest declaration. The ordering check is load-bearing, not decoration: a
    plain set intersection counts a session that logged a handoff mid-session and then ended
    without one as "reversed" backwards. Measured 2026-08-10: 4 of 42 naive matches were
    backwards, inflating the rate from the true 55.1% to 60.9%. The first version of this
    function shipped the naive intersection and its number reached three governance artifacts
    before a pre-ship review caught it.

    Reversal is not self-reversal: user prompting is a likely driver. That does not weaken
    the metric, it IS the metric — it measures whether the skip judgement survives contact
    with the user. Unlike the two summaries above this one needs a join, so it takes a
    {session_id: latest handoff timestamp} map.

    handoff_times is deliberately ALL-TIME, not window-scoped: a skip declared inside the
    window is often reversed by a handoff logged minutes later but outside a narrow cutoff,
    and window-scoping the join would understate reversals.
    """
    reasons = Counter()
    low_quality = 0

    def sessions_declaring(records: Iterable[Dict[str, Any]]) -> set:
        return {
            str(r.get("session_id"))
            for r in records
            if r.get("skip_declared") and r.get("session_id")
        }

    declared_all = sessions_declaring(all_records)
    declared_window = sessions_declaring(window_records)
    declaration_rows = sum(1 for r in all_records if r.get("skip_declared"))

    # Earliest declaration per session — the ordering reference for the join below.
    first_declaration: Dict[str, datetime] = {}
    for record in all_records:
        sid = str(record.get("session_id") or "")
        if not record.get("skip_declared") or not sid:
            continue
        ts = parse_iso_timestamp(str(record.get("timestamp", "")))
        if ts and (sid not in first_declaration or ts < first_declaration[sid]):
            first_declaration[sid] = ts

    for record in all_records:
        if not record.get("skip_declared"):
            continue
        reason = str(record.get("skip_reason", "")).strip()
        if not reason:
            continue
        normalized = normalize_reason(reason)
        reasons[normalized[:100]] += 1
        if len(normalized) < 12 or normalized in {"n/a", "none", "skip", "quick check"}:
            low_quality += 1

    reversed_all = {
        sid for sid in declared_all
        if sid in first_declaration
        and (ho := handoff_times.get(sid)) is not None
        and ho > first_declaration[sid]
    }
    # Naive intersection retained ONLY as a disclosed contrast, so the ordering correction
    # stays visible in the output rather than being silently absorbed.
    naive_all = declared_all & set(handoff_times)
    stood_all = declared_all - reversed_all
    rate = (len(reversed_all) / len(declared_all)) if declared_all else None
    # Skips arrive in streaks, not singles — a session that skips once usually skips again.
    per_session = (declaration_rows / len(declared_all)) if declared_all else None

    return {
        "all_time_advisory_rows": len(all_records),
        "window_advisory_rows": len(window_records),
        "sessions_declaring_all_time": len(declared_all),
        "sessions_declaring_window": len(declared_window),
        "declaration_rows": declaration_rows,
        "declarations_per_skipping_session": round(per_session, 2) if per_session is not None else None,
        "reversed_count": len(reversed_all),
        "naive_intersection_count": len(naive_all),
        "backwards_matches_excluded": len(naive_all) - len(reversed_all),
        "stood_count": len(stood_all),
        "reversal_rate": round(rate, 4) if rate is not None else None,
        "low_quality_reason_count": low_quality,
        "top_reasons": reasons.most_common(8),
    }


def build_alerts(
    override_summary: Dict[str, Any],
    bypass_summary: Dict[str, Any],
    skip_summary: Optional[Dict[str, Any]] = None,
) -> List[str]:
    alerts: List[str] = []

    # Skip alerts are computed FIRST and unconditionally. The override early-return below
    # would otherwise suppress them whenever a window happened to contain no overrides —
    # a check that cannot fire is not a check.
    if skip_summary and skip_summary.get("reversal_rate") is not None:
        rate = skip_summary["reversal_rate"]
        if rate > 0.50:
            alerts.append(
                f"Declared QC skips are reversed MORE OFTEN THAN NOT "
                f"({rate*100:.0f}% of {skip_summary['sessions_declaring_all_time']} "
                f"skip-declaring sessions later ran the QC anyway)."
            )
        per = skip_summary.get("declarations_per_skipping_session")
        if per and per >= 2.0:
            alerts.append(
                f"Skips arrive in streaks, not singles ({per} declarations per "
                f"skipping session) — the streak is the unit, not the instance."
            )

    if override_summary["window_count"] == 0:
        alerts.append("No gate overrides in window.")
        return alerts

    if override_summary["reasons_missing_rate"] > 0.20:
        alerts.append("High missing override reason rate (>20%).")
    if override_summary["low_quality_reason_rate"] > 0.20:
        alerts.append("High low-quality override reasons (>20%).")
    if override_summary["window_count"] >= 25:
        alerts.append("High override volume in window (>=25).")
    if bypass_summary["window_count"] == 0:
        alerts.append("No test bypasses in window (check test-query detection paths).")

    if not alerts:
        alerts.append("No alert thresholds triggered.")
    return alerts


def render_text_report(
    days: int,
    overrides_path: Path,
    bypasses_path: Path,
    cutoff_utc: datetime,
    override_summary: Dict[str, Any],
    bypass_summary: Dict[str, Any],
    alerts: List[str],
    skip_summary: Optional[Dict[str, Any]] = None,
) -> str:
    lines = []
    lines.append("ASSUMPTION GATE OVERRIDE AUDIT")
    lines.append("=" * 80)
    lines.append(f"Window: last {days} day(s), cutoff={cutoff_utc.isoformat()}")
    lines.append(f"Overrides file: {overrides_path}")
    lines.append(f"Bypasses file: {bypasses_path}")
    lines.append("")
    lines.append("OVERRIDE SUMMARY")
    lines.append("-" * 80)
    lines.append(f"Window overrides: {override_summary['window_count']}")
    lines.append(f"All-time overrides: {override_summary['all_time_count']}")
    lines.append(
        f"Missing reasons: {override_summary['reasons_missing_count']} "
        f"({override_summary['reasons_missing_rate']*100:.1f}%)"
    )
    lines.append(
        f"Low-quality reasons: {override_summary['low_quality_reason_count']} "
        f"({override_summary['low_quality_reason_rate']*100:.1f}%)"
    )
    lines.append("")
    lines.append("Top override reasons:")
    if override_summary["top_reasons"]:
        for reason, count in override_summary["top_reasons"]:
            lines.append(f"  - {count:>3} | {reason}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Top overridden queries:")
    if override_summary["top_questions"]:
        for query, count in override_summary["top_questions"]:
            lines.append(f"  - {count:>3} | {query}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("TEST BYPASS SUMMARY")
    lines.append("-" * 80)
    lines.append(f"Window test bypasses: {bypass_summary['window_count']}")
    lines.append(f"All-time test bypasses: {bypass_summary['all_time_count']}")
    lines.append("By source:")
    if bypass_summary["by_source"]:
        for source, count in sorted(bypass_summary["by_source"].items()):
            lines.append(f"  - {source}: {count}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Top test patterns:")
    if bypass_summary["top_patterns"]:
        for pattern, count in bypass_summary["top_patterns"]:
            lines.append(f"  - {count:>3} | {pattern}")
    else:
        lines.append("  - none")
    lines.append("")
    if skip_summary is not None:
        lines.append("DECLARED QC-SKIP TRACK RECORD (all-time)")
        lines.append("-" * 80)
        if skip_summary["sessions_declaring_all_time"] == 0:
            lines.append("  - no declared skips on record")
        else:
            lines.append(
                f"Sessions declaring a skip: {skip_summary['sessions_declaring_all_time']}"
                f"  (window: {skip_summary['sessions_declaring_window']})"
            )
            lines.append(
                f"Reversed (ran it anyway): {skip_summary['reversed_count']}"
                f"   Stood: {skip_summary['stood_count']}"
            )
            rate = skip_summary["reversal_rate"]
            lines.append(f"REVERSAL RATE: {rate*100:.1f}%" if rate is not None else "REVERSAL RATE: n/a")
            lines.append(
                f"  (ordering-checked. Naive intersection would say "
                f"{skip_summary['naive_intersection_count']}; "
                f"{skip_summary['backwards_matches_excluded']} backwards match(es) excluded)"
            )
            lines.append(
                f"Declarations per skipping session: "
                f"{skip_summary['declarations_per_skipping_session']}"
            )
            lines.append("")
            lines.append("Top stated skip reasons:")
            for reason, count in skip_summary["top_reasons"]:
                lines.append(f"  - {count:>3} | {reason}")
            lines.append("")
            lines.append(
                "  Reversal != self-reversal: user prompting is a likely driver. That is the"
            )
            lines.append(
                "  measurement, not a caveat — it is whether the skip judgement survives review."
            )
        lines.append("")
    lines.append("ALERTS")
    lines.append("-" * 80)
    for alert in alerts:
        lines.append(f"  - {alert}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit assumption gate override and bypass behavior")
    parser.add_argument("--days", type=int, default=7, help="Window size in days (default: 7)")
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES, help="Path to gate-overrides JSONL")
    parser.add_argument("--bypasses", type=Path, default=DEFAULT_BYPASSES, help="Path to test-bypasses JSONL")
    parser.add_argument("--skips", type=Path, default=DEFAULT_SKIPS, help="Path to declared-QC-skip JSONL")
    parser.add_argument("--handoffs", type=Path, default=DEFAULT_HANDOFFS, help="Path to handoff JSONL (join target)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    days = max(args.days, 1)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    overrides_all = read_jsonl(args.overrides.expanduser())
    bypasses_all = read_jsonl(args.bypasses.expanduser())
    overrides_window = in_window(overrides_all, cutoff)
    bypasses_window = in_window(bypasses_all, cutoff)

    skips_all = read_jsonl(args.skips.expanduser())
    skips_window = in_window(skips_all, cutoff)
    # Latest handoff timestamp per session — the join needs ordering, not membership.
    handoff_times: Dict[str, datetime] = {}
    for record in read_jsonl(args.handoffs.expanduser()):
        sid = str(record.get("session_id") or "")
        ts = parse_iso_timestamp(str(record.get("timestamp", "")))
        if sid and ts and (sid not in handoff_times or ts > handoff_times[sid]):
            handoff_times[sid] = ts

    override_summary = summarize_overrides(overrides_all, overrides_window)
    bypass_summary = summarize_bypasses(bypasses_all, bypasses_window)
    skip_summary = summarize_skips(skips_all, skips_window, handoff_times)
    alerts = build_alerts(override_summary, bypass_summary, skip_summary)

    report = {
        "generated_at": now.isoformat(),
        "window_days": days,
        "cutoff_utc": cutoff.isoformat(),
        "files": {
            "overrides": str(args.overrides.expanduser()),
            "bypasses": str(args.bypasses.expanduser()),
            "skips": str(args.skips.expanduser()),
            "handoffs": str(args.handoffs.expanduser()),
        },
        "overrides": override_summary,
        "bypasses": bypass_summary,
        "declared_qc_skips": skip_summary,
        "alerts": alerts,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            render_text_report(
                days=days,
                overrides_path=args.overrides.expanduser(),
                bypasses_path=args.bypasses.expanduser(),
                cutoff_utc=cutoff,
                override_summary=override_summary,
                bypass_summary=bypass_summary,
                alerts=alerts,
                skip_summary=skip_summary,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

