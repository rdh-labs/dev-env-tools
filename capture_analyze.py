#!/usr/bin/env python3
"""
Capture-Analyze Integration - Automated governance item extraction and capture.

Integrates governance_analyzer.py (pattern matching) and governance_file_editor.py
(file operations) to provide end-to-end governance item extraction and capture.
"""

import sys
import json
import time
import fcntl
from datetime import datetime, timezone
from typing import List, Dict
from pathlib import Path

# Import the modules we just created
from governance_analyzer import GovernanceAnalyzer, GovernanceItem
from governance_file_editor import GovernanceFileEditor, GovernanceFileError, IDCollisionError


# Metrics infrastructure
METRICS_DIR = Path.home() / ".metrics/capture-analyze"
METRICS_DIR.mkdir(parents=True, exist_ok=True)


def log_metric(metric_type: str, data: Dict) -> None:
    """
    Log metric to JSONL file.

    Args:
        metric_type: "usage", "performance", or "accuracy"
        data: Metric data dict
    """
    metric_file = METRICS_DIR / f"{metric_type}.jsonl"

    # Add timestamp if not present
    if "timestamp" not in data:
        data["timestamp"] = datetime.now(timezone.utc).isoformat()

    try:
        with open(metric_file, 'a') as f:
            # Acquire lock for append
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(data) + '\n')
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        # Fail-safe: don't crash on metrics failure
        print(f"⚠️  Metrics logging failed: {e}", file=sys.stderr)


class CaptureAnalyzer:
    """End-to-end governance item extraction and capture."""

    def __init__(self, scope: str = "global"):
        """
        Initialize analyzer.

        Args:
            scope: "global" or project path
        """
        self.analyzer = GovernanceAnalyzer()
        self.editor = GovernanceFileEditor(scope=scope)

    def analyze(self, text: str, format: str = "human") -> Dict:
        """
        Analyze text and extract governance items.

        Args:
            text: Input text
            format: "human" or "json"

        Returns:
            Dict with items and metadata
        """
        items = self.analyzer.analyze_text(text)

        if format == "json":
            return {
                "items": [
                    {
                        "type": item.type,
                        "text": item.text,
                        "context": item.context,
                        "score": item.score,
                        "confidence": item.confidence,
                        "suggested_category": self.analyzer.suggest_category(item),
                        "suggested_priority": self.analyzer.suggest_priority_or_severity(item),
                        "signals": item.signals
                    }
                    for item in items
                ],
                "count": len(items)
            }

        return {"items": items, "count": len(items)}

    def capture_idea(
        self,
        text: str,
        title: str,
        category: str,
        priority: str,
        description: str = "",
        **kwargs
    ) -> str:
        """
        Capture IDEA to IDEAS-BACKLOG.md.

        Args:
            text: Original extracted text
            title: Short title
            category: Category
            priority: HIGH/MEDIUM/LOW
            description: Full description (defaults to text if empty)
            **kwargs: Additional fields (why_needed, blocker, etc.)

        Returns:
            Created IDEA ID

        Raises:
            GovernanceFileError: On file operation failure
        """
        # Get next available ID
        idea_id = self.editor.get_next_id("IDEAS")

        # Use text as description if none provided
        if not description:
            description = text

        # Insert
        self.editor.insert_idea(
            idea_id=idea_id,
            title=title,
            category=category,
            priority=priority,
            description=description,
            source="Automated extraction via /capture-analyze",
            **kwargs
        )

        return idea_id

    def capture_issue(
        self,
        text: str,
        title: str,
        severity: str,
        category: str,
        description: str = "",
        impact: str = "",
        resolution: List[str] = None,
        **kwargs
    ) -> str:
        """
        Capture ISSUE to ISSUES-TRACKER.md.

        Args:
            text: Original extracted text
            title: Short title
            severity: CRITICAL/HIGH/MEDIUM/LOW
            category: Category
            description: Full description (defaults to text if empty)
            impact: What fails if not fixed
            resolution: Resolution steps
            **kwargs: Additional fields

        Returns:
            Created ISSUE ID

        Raises:
            GovernanceFileError: On file operation failure
        """
        # Get next available ID
        issue_id = self.editor.get_next_id("ISSUES")

        # Defaults
        if not description:
            description = text

        if not impact:
            impact = "See description"

        if not resolution:
            resolution = ["Investigate and resolve"]

        # Insert
        self.editor.insert_issue(
            issue_id=issue_id,
            title=title,
            severity=severity,
            category=category,
            description=description,
            impact=impact,
            resolution=resolution,
            source="Automated extraction via /capture-analyze",
            **kwargs
        )

        return issue_id

    def analyze_and_present(self, text: str) -> Dict:
        """
        Analyze text and format for user presentation.

        Args:
            text: Input text

        Returns:
            Dict with formatted items for display
        """
        # Performance tracking
        start_time = time.time()

        items = self.analyzer.analyze_text(text)

        presented = []

        for i, item in enumerate(items, 1):
            category = self.analyzer.suggest_category(item)
            priority = self.analyzer.suggest_priority_or_severity(item)

            # Generate title (first 60 chars of text)
            title = item.text[:60].strip()
            if len(item.text) > 60:
                title += "..."

            # Determine why this was classified as this type
            why = self._explain_classification(item)

            presented.append({
                "number": i,
                "type": item.type,
                "title": title,
                "text": item.text,
                "context": item.context,
                "score": item.score,
                "confidence": item.confidence,
                "suggested_category": category,
                "suggested_priority": priority,
                "signals": item.signals,
                "explanation": why
            })

        # Log performance metrics
        duration_ms = int((time.time() - start_time) * 1000)
        log_metric("performance", {
            "operation": "analyze_and_present",
            "duration_ms": duration_ms,
            "text_length": len(text),
            "items_extracted": len(presented),
            "types_found": list(set(item["type"] for item in presented))
        })

        # Log usage metrics
        log_metric("usage", {
            "text_length": len(text),
            "items_found": len(presented),
            "types": [item["type"] for item in presented],
            "avg_confidence": sum(item["confidence"] for item in presented) / len(presented) if presented else 0.0
        })

        return {
            "items": presented,
            "count": len(presented)
        }

    def _explain_classification(self, item: GovernanceItem) -> str:
        """Generate explanation for why item was classified as this type."""
        signals_desc = []

        for signal in item.signals[:3]:  # Show top 3 signals
            sig_type, sig_value = signal.split(':', 1)
            signals_desc.append(f'"{sig_value}"')

        signals_str = ", ".join(signals_desc)

        if item.type == "ISSUE":
            return f"Identified problem signals: {signals_str}"
        elif item.type == "IDEA":
            return f"Solution-oriented signals: {signals_str}"
        elif item.type == "DECISION":
            return f"Decision-making signals: {signals_str}"
        elif item.type == "LESSON":
            return f"Learning signals: {signals_str}"
        elif item.type == "TASK":
            return f"Action signals: {signals_str}"
        else:
            return f"Matched signals: {signals_str}"


def main():
    """CLI interface."""
    import argparse

    try:
        parser = argparse.ArgumentParser(
            description="Analyze text for governance items"
        )
        parser.add_argument('text', nargs='?', help='Text to analyze (or stdin)')
        parser.add_argument('--format', choices=['human', 'json'], default='human',
                           help='Output format')
        parser.add_argument('--present', action='store_true',
                           help='Format for user presentation')

        args = parser.parse_args()

        # Get text from args or stdin
        if args.text:
            text = args.text
        else:
            text = sys.stdin.read()

        if not text.strip():
            print("Error: No text provided", file=sys.stderr)
            sys.exit(1)

        analyzer = CaptureAnalyzer()

        if args.present:
            result = analyzer.analyze_and_present(text)

            if args.format == 'json':
                print(json.dumps(result, indent=2))
            else:
                print(f"🔍 Analyzed your input. Found {result['count']} potential governance items:\n")

                for item in result['items']:
                    print("━" * 60)
                    print(f"\n{item['number']}️⃣ {item['type']}: {item['title']}")
                    print(f"\n   📍 Signals: {', '.join(s.split(':')[1] for s in item['signals'][:3])}")
                    print(f"\n   📝 Text: {item['text']}")
                    print(f"\n   💡 Suggested Category: {item['suggested_category']}")
                    print(f"   🎯 Suggested Priority/Severity: {item['suggested_priority']}")
                    print(f"\n   Why {item['type']}? {item['explanation']}")
                    print(f"\n   Confidence: {item['confidence']:.0%}\n")

        else:
            result = analyzer.analyze(text, format=args.format)

            if args.format == 'json':
                print(json.dumps(result, indent=2))
            else:
                print(f"Found {result['count']} items:")
                for item in result['items']:
                    print(f"\n- {item.type}: {item.text[:80]}...")
                    print(f"  Score: {item.score}, Confidence: {item.confidence:.1%}")

    except GovernanceFileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
