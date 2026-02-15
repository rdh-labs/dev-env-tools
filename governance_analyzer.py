#!/usr/bin/env python3
"""
Governance Pattern Analyzer - Automated extraction of governance items.

Implements pattern-based analysis to extract IDEAS, ISSUES, DECISIONS, LESSONS,
and TASKS from unstructured text using keyword/phrase matching and confidence scoring.
"""

import re
from typing import Dict, List, Tuple
from dataclasses import dataclass


# Governance signal patterns (from capture-analyze skill spec)
SIGNALS = {
    "ISSUE": {
        "keywords": [
            "broken", "doesn't work", "failing", "not working",
            "bug", "error", "gap", "missing", "lacking",
            "unclear", "uncertain", "not sure if", "don't know if",
            "problem", "issue", "blocker", "stuck"
        ],
        "phrases": [
            "we can't rely on",
            "no way to",
            "doesn't ensure",
            "doesn't guarantee",
            "uncertain whether",
            "not sure if our"
        ]
    },

    "IDEA": {
        "keywords": [
            "could", "should build", "need a way", "would be good to",
            "propose", "suggest", "recommend", "consider",
            "what if", "we could create", "automate", "improve"
        ],
        "phrases": [
            "we need a way to",
            "we could build",
            "would be good to have",
            "we should create",
            "opportunity to",
            "could improve by"
        ]
    },

    "DECISION": {
        "keywords": [
            "should we", "decide", "choose between", "options",
            "adopt", "reject", "trial", "evaluate"
        ],
        "phrases": [
            "should we adopt",
            "choose between",
            "decide whether to",
            "options are",
            "either/or",
            "go with X or Y"
        ]
    },

    "LESSON": {
        "keywords": [
            "learned", "discovered", "found out", "realized",
            "turned out", "it appears", "observation"
        ],
        "phrases": [
            "we learned that",
            "discovered that",
            "found out that",
            "turned out that",
            "key insight:",
            "lesson learned:"
        ]
    },

    "TASK": {
        "keywords": [
            "need to", "must", "should", "TODO", "action item",
            "follow up", "contact", "reach out", "investigate"
        ],
        "phrases": [
            "need to contact",
            "must investigate",
            "should reach out",
            "follow up with",
            "next step:",
            "action:"
        ]
    }
}

# Scoring thresholds
THRESHOLD = 2  # Minimum score to be considered


@dataclass
class GovernanceItem:
    """Extracted governance item with metadata."""
    type: str  # ISSUE, IDEA, DECISION, LESSON, TASK
    text: str  # Original text
    context: str  # Surrounding context
    score: int  # Match score
    confidence: float  # Confidence (0.0-1.0)
    signals: List[str]  # Matched signals


class GovernanceAnalyzer:
    """Analyze text for governance items using pattern matching."""

    def __init__(self, threshold: int = THRESHOLD):
        """
        Initialize analyzer.

        Args:
            threshold: Minimum score for item detection
        """
        self.threshold = threshold

    def analyze_text(self, text: str) -> List[GovernanceItem]:
        """
        Extract governance items from text using pattern matching.

        Args:
            text: Input text to analyze

        Returns:
            List of potential governance items with scores
        """
        items = []
        sentences = self._split_into_sentences(text)

        for i, sentence in enumerate(sentences):
            sentence_lower = sentence.lower()

            # Score each sentence for each governance type
            scores = {}
            matched_signals = {}

            for item_type in ["ISSUE", "IDEA", "DECISION", "LESSON", "TASK"]:
                score, signals = self._score_sentence(sentence_lower, SIGNALS[item_type])
                scores[item_type] = score
                matched_signals[item_type] = signals

            # If any score > threshold, it's a potential item
            max_type = max(scores, key=scores.get)
            max_score = scores[max_type]

            if max_score >= self.threshold:
                items.append(GovernanceItem(
                    type=max_type,
                    text=sentence.strip(),
                    context=self._get_context(sentences, i),
                    score=max_score,
                    confidence=self._calculate_confidence(scores),
                    signals=matched_signals[max_type]
                ))

        # Merge adjacent sentences that are part of same item
        items = self._merge_related_items(items)

        # Deduplicate similar items
        items = self._deduplicate(items)

        return items

    def _split_into_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences.

        Args:
            text: Input text

        Returns:
            List of sentences
        """
        # Simple sentence splitting (can be improved with nltk)
        # Split on period, exclamation, question mark followed by space/newline
        sentences = re.split(r'[.!?]+\s+', text)

        # Remove empty sentences
        sentences = [s.strip() for s in sentences if s.strip()]

        return sentences

    def _score_sentence(self, sentence: str, signals: Dict[str, List[str]]) -> Tuple[int, List[str]]:
        """
        Score a sentence for a governance type.

        Args:
            sentence: Sentence (lowercased)
            signals: Signal patterns (keywords + phrases)

        Returns:
            (score, list of matched signals)
        """
        score = 0
        matched = []

        # Keyword matching (1 point each)
        for keyword in signals["keywords"]:
            if keyword in sentence:
                score += 1
                matched.append(f"keyword:{keyword}")

        # Phrase matching (2 points each, more specific)
        for phrase in signals["phrases"]:
            if phrase in sentence:
                score += 2
                matched.append(f"phrase:{phrase}")

        return score, matched

    def _get_context(self, sentences: List[str], index: int) -> str:
        """
        Get 1 sentence before and after for context.

        Args:
            sentences: All sentences
            index: Current sentence index

        Returns:
            Context string
        """
        context = []

        # Previous sentence
        if index > 0:
            context.append(sentences[index - 1])

        # Current sentence
        context.append(sentences[index])

        # Next sentence
        if index < len(sentences) - 1:
            context.append(sentences[index + 1])

        return " ".join(context)

    def _calculate_confidence(self, scores: Dict[str, int]) -> float:
        """
        Calculate confidence based on score distribution.

        High confidence: One type dominates (>70% of total)
        Medium confidence: Top type >50% of total
        Low confidence: Multiple types similar scores

        Args:
            scores: Scores for each type

        Returns:
            Confidence (0.0-1.0)
        """
        total = sum(scores.values())
        max_score = max(scores.values())

        if total == 0:
            return 0.0

        ratio = max_score / total

        if ratio > 0.7:
            return 0.9  # High confidence
        elif ratio > 0.5:
            return 0.7  # Medium confidence
        else:
            return 0.4  # Low confidence

    def _merge_related_items(self, items: List[GovernanceItem]) -> List[GovernanceItem]:
        """
        Merge adjacent sentences that are part of same item.

        Args:
            items: List of items

        Returns:
            Merged items
        """
        if len(items) <= 1:
            return items

        merged = []
        current = items[0]

        for next_item in items[1:]:
            # Merge if same type and text is continuation
            if (current.type == next_item.type and
                self._is_continuation(current.text, next_item.text)):
                # Merge text
                current.text = f"{current.text} {next_item.text}"
                # Update context to include both
                current.context = f"{current.context} {next_item.context}"
                # Combine scores
                current.score = max(current.score, next_item.score)
                # Combine signals
                current.signals.extend(next_item.signals)
            else:
                merged.append(current)
                current = next_item

        # Don't forget last item
        merged.append(current)

        return merged

    def _is_continuation(self, text1: str, text2: str) -> bool:
        """
        Check if text2 continues text1.

        Args:
            text1: First text
            text2: Second text

        Returns:
            True if continuation
        """
        # Simple heuristic: if text2 starts with lowercase or
        # text1 doesn't end with period, likely continuation
        if text2 and text2[0].islower():
            return True

        if text1 and not text1.rstrip().endswith('.'):
            return True

        return False

    def _deduplicate(self, items: List[GovernanceItem]) -> List[GovernanceItem]:
        """
        Remove duplicate items.

        Args:
            items: List of items

        Returns:
            Deduplicated items
        """
        seen_texts = set()
        unique = []

        for item in items:
            # Normalize text for comparison
            normalized = item.text.lower().strip()

            if normalized not in seen_texts:
                seen_texts.add(normalized)
                unique.append(item)

        return unique

    def suggest_category(self, item: GovernanceItem) -> str:
        """
        Suggest category based on item content.

        Args:
            item: Governance item

        Returns:
            Suggested category
        """
        text_lower = item.text.lower()

        # Category keywords
        categories = {
            "Automation": ["automate", "automatic", "scheduled", "cron"],
            "Architecture": ["architecture", "design", "pattern", "structure"],
            "Process": ["workflow", "process", "procedure", "method"],
            "Bug": ["bug", "broken", "error", "crash"],
            "Gap": ["missing", "lacking", "no way to", "doesn't have"],
            "Integration": ["integration", "connect", "sync", "link"],
            "Security": ["security", "credential", "auth", "permission"],
            "Performance": ["slow", "performance", "optimize", "faster"],
        }

        for category, keywords in categories.items():
            if any(kw in text_lower for kw in keywords):
                return category

        # Default by type
        if item.type == "ISSUE":
            return "Process"
        elif item.type == "IDEA":
            return "Automation"
        else:
            return "Process"

    def suggest_priority_or_severity(self, item: GovernanceItem) -> str:
        """
        Suggest priority (IDEAS) or severity (ISSUES).

        Args:
            item: Governance item

        Returns:
            HIGH/MEDIUM/LOW
        """
        text_lower = item.text.lower()

        # High priority/severity signals
        high_signals = [
            "critical", "blocking", "broken", "fails", "error",
            "urgent", "immediately", "must"
        ]

        # Low priority/severity signals
        low_signals = [
            "nice to have", "eventually", "minor", "small",
            "trivial", "cosmetic"
        ]

        if any(sig in text_lower for sig in high_signals):
            return "HIGH"
        elif any(sig in text_lower for sig in low_signals):
            return "LOW"
        else:
            return "MEDIUM"


def main():
    """CLI interface for testing."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: governance_analyzer.py <text>")
        sys.exit(1)

    text = " ".join(sys.argv[1:])

    analyzer = GovernanceAnalyzer()
    items = analyzer.analyze_text(text)

    print(f"🔍 Found {len(items)} potential governance items:\n")

    for i, item in enumerate(items, 1):
        category = analyzer.suggest_category(item)
        priority = analyzer.suggest_priority_or_severity(item)

        print(f"{i}. {item.type}: {item.text[:60]}...")
        print(f"   Score: {item.score}, Confidence: {item.confidence:.1%}")
        print(f"   Suggested: {category} / {priority}")
        print(f"   Signals: {', '.join(item.signals[:3])}")
        print()


if __name__ == '__main__':
    main()
