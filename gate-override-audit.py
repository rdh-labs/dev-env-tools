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


def build_alerts(override_summary: Dict[str, Any], bypass_summary: Dict[str, Any]) -> List[str]:
    alerts: List[str] = []

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
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    days = max(args.days, 1)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    overrides_all = read_jsonl(args.overrides.expanduser())
    bypasses_all = read_jsonl(args.bypasses.expanduser())
    overrides_window = in_window(overrides_all, cutoff)
    bypasses_window = in_window(bypasses_all, cutoff)

    override_summary = summarize_overrides(overrides_all, overrides_window)
    bypass_summary = summarize_bypasses(bypasses_all, bypasses_window)
    alerts = build_alerts(override_summary, bypass_summary)

    report = {
        "generated_at": now.isoformat(),
        "window_days": days,
        "cutoff_utc": cutoff.isoformat(),
        "files": {
            "overrides": str(args.overrides.expanduser()),
            "bypasses": str(args.bypasses.expanduser()),
        },
        "overrides": override_summary,
        "bypasses": bypass_summary,
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
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

