#!/usr/bin/env python3
"""Shared relevance scoring for assumption-gate domain matching.

The scorer is intentionally lightweight and deterministic:
- lexical overlap on normalized tokens
- synonym expansion for common software domains
- optional external semantic score hook (cloud/tool command)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "about",
    "what", "when", "where", "which", "will", "would", "could", "should", "have",
    "has", "had", "are", "was", "were", "been", "being", "than", "then", "their",
    "there", "here", "review", "check", "analyze", "analysis", "code", "logic",
    "design", "best", "practice", "practices", "recommendation", "recommendations",
    "use", "using", "need", "does", "just", "very", "into", "over", "such",
}

SYNONYM_GROUPS = {
    "database": {"database", "datastore", "db", "postgresql", "postgres", "mysql", "mongodb", "redis", "sql", "schema", "migration"},
    "deployment": {"deployment", "deploy", "release", "rollout", "production", "staging", "runtime"},
    "security": {"security", "auth", "authentication", "authorization", "permission", "credential", "secret", "token", "vulnerability"},
    "frontend": {"frontend", "ui", "ux", "css", "html", "component", "react", "tailwind"},
    "backend": {"backend", "api", "endpoint", "service", "server", "worker"},
    "infrastructure": {"infrastructure", "kubernetes", "docker", "container", "terraform", "cloud"},
}

_SYNONYM_LOOKUP: Dict[str, Set[str]] = {}
for _group_terms in SYNONYM_GROUPS.values():
    expanded = set(_group_terms)
    for _term in _group_terms:
        _SYNONYM_LOOKUP[_term] = expanded


@dataclass
class RelevanceResult:
    score: float
    lexical_score: float
    semantic_score: Optional[float]
    direct_overlap_terms: List[str]
    expanded_overlap_terms: List[str]
    matched_groups: List[str]
    threshold: float

    @property
    def is_relevant(self) -> bool:
        return self.score >= self.threshold

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "lexical_score": round(self.lexical_score, 4),
            "semantic_score": None if self.semantic_score is None else round(self.semantic_score, 4),
            "direct_overlap_terms": self.direct_overlap_terms,
            "expanded_overlap_terms": self.expanded_overlap_terms,
            "matched_groups": self.matched_groups,
            "threshold": self.threshold,
            "is_relevant": self.is_relevant,
        }


def tokenize(text: str) -> Set[str]:
    if not text:
        return set()
    terms = set(re.findall(r"\b[a-z][a-z0-9_-]{2,}\b", text.lower()))
    return {term for term in terms if term not in STOPWORDS}


def expand_terms(terms: Iterable[str]) -> Set[str]:
    expanded: Set[str] = set()
    for term in terms:
        expanded.add(term)
        expanded.update(_SYNONYM_LOOKUP.get(term, {term}))
    return expanded


def _matching_groups(terms_a: Set[str], terms_b: Set[str]) -> List[str]:
    matched = []
    for group_name, group_terms in SYNONYM_GROUPS.items():
        if (terms_a & group_terms) and (terms_b & group_terms):
            matched.append(group_name)
    return sorted(matched)


def _jaccard(lhs: Set[str], rhs: Set[str]) -> float:
    union = lhs | rhs
    if not union:
        return 0.0
    return len(lhs & rhs) / len(union)


def _direct_overlap(lhs: Set[str], rhs: Set[str]) -> float:
    if not lhs:
        return 0.0
    return len(lhs & rhs) / len(lhs)


def _external_semantic_score(query: str, assumption_text: str) -> Optional[float]:
    """Optional plug-in hook for cloud semantic scoring.

    If ASSUMPTION_RELEVANCE_EMBED_CMD is set, the command is executed with JSON
    payload on stdin and must return a float in [0,1] on stdout.
    """
    cmd = os.environ.get("ASSUMPTION_RELEVANCE_EMBED_CMD", "").strip()
    if not cmd:
        return None

    try:
        proc = subprocess.run(
            shlex.split(cmd),
            input=json.dumps({"query": query, "assumption_text": assumption_text}),
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        if proc.returncode != 0:
            return None
        value = float(proc.stdout.strip())
        if value < 0:
            return 0.0
        if value > 1:
            return 1.0
        return value
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def score_relevance(
    query: str,
    claim: str,
    evidence: str = "",
    category: str = "",
    threshold: float = 0.18,
    semantic_weight: float = 0.2,
) -> RelevanceResult:
    query_terms = tokenize(query)
    assumption_terms = tokenize(" ".join([claim, evidence, category]))

    direct_overlap_terms = sorted(query_terms & assumption_terms)
    query_expanded = expand_terms(query_terms)
    assumption_expanded = expand_terms(assumption_terms)
    expanded_overlap_terms = sorted(query_expanded & assumption_expanded)

    direct = _direct_overlap(query_terms, assumption_terms)
    expanded_jaccard = _jaccard(query_expanded, assumption_expanded)
    lexical = (0.7 * direct) + (0.3 * expanded_jaccard)

    matched_groups = _matching_groups(query_terms, assumption_terms)
    if matched_groups:
        lexical = min(1.0, lexical + 0.08)

    semantic = _external_semantic_score(query, " ".join([claim, evidence, category]))
    if semantic is None:
        score = lexical
    else:
        score = ((1 - semantic_weight) * lexical) + (semantic_weight * semantic)

    return RelevanceResult(
        score=score,
        lexical_score=lexical,
        semantic_score=semantic,
        direct_overlap_terms=direct_overlap_terms,
        expanded_overlap_terms=expanded_overlap_terms,
        matched_groups=matched_groups,
        threshold=threshold,
    )


def filter_relevant_assumptions(
    query: str,
    invalid: List[Dict[str, Any]],
    unvalidated: List[Dict[str, Any]],
    threshold: float = 0.18,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    if not query:
        return invalid, unvalidated, "no_query"

    query_terms = tokenize(query)
    if len(query_terms) < 2:
        return invalid, unvalidated, "insufficient_query_terms"

    relevant_invalid: List[Dict[str, Any]] = []
    relevant_unvalidated: List[Dict[str, Any]] = []

    for assumption in invalid:
        result = score_relevance(
            query=query,
            claim=str(assumption.get("claim", "")),
            evidence=str(assumption.get("evidence", "")),
            category=str(assumption.get("category", "")),
            threshold=threshold,
        )
        if result.is_relevant:
            relevant_invalid.append(assumption)

    for assumption in unvalidated:
        result = score_relevance(
            query=query,
            claim=str(assumption.get("claim", "")),
            evidence=str(assumption.get("evidence", "")),
            category=str(assumption.get("category", "")),
            threshold=threshold,
        )
        if result.is_relevant:
            relevant_unvalidated.append(assumption)

    return relevant_invalid, relevant_unvalidated, "relevance_library"

