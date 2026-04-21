#!/usr/bin/env python3
"""
IDEA-1295: Engram admissions-control telemetry scanner.
Finds behavioral anomaly classes with >= THRESHOLD observations and no structural governance filing.
Outputs JSON to stdout: list of ungoverned cluster dicts.
"""

import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".engram/engram.db"
DAYS = 30
THRESHOLD = 3
JACCARD_THRESHOLD = 0.20

STRUCTURAL_GATE_RE = re.compile(
    r"(?:IDEA|ISSUE)-\d+[^\n]{0,200}(?:scanner|gate|hook|enforcement|blocking|exit\s*2)",
    re.IGNORECASE,
)
GOV_TITLE_RE = re.compile(r"^\s*(?:IDEA|ISSUE|DEC|ADR)-\d+")

STOP_WORDS = {
    "fix", "fixed", "implement", "implemented", "resolve", "resolved",
    "add", "added", "apply", "applied", "ship", "shipped", "session",
    "recurrence", "recurrent", "instance", "via", "the", "a", "an",
    "in", "on", "for", "from", "with", "at", "by", "to", "of", "is",
    "was", "are", "this", "that", "and", "or", "not", "but", "also",
}


def normalize(title: str) -> str:
    t = re.sub(r"\b(?:IDEA|ISSUE|DEC|ADR)-\d+\b", "", title)
    t = re.sub(r"\bL-\d+\b", "", t)
    # Split hyphens so "escalation-as-completion" → individual words
    t = t.replace("-", " ")
    t = re.sub(r"[^\w\s]", " ", t)
    words = [
        w.lower() for w in t.split()
        if len(w) > 2 and w.lower() not in STOP_WORDS
    ]
    return " ".join(words[:6])


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if len(sa) < 2 or not sb:
        return 0.0
    intersection = sa & sb
    # Require >=2 shared words AND Jaccard above threshold
    if len(intersection) < 2:
        return 0.0
    return len(intersection) / len(sa | sb)


def query_observations() -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            """
            SELECT id, type, title, content, created_at
            FROM observations
            WHERE (
                type = 'bugfix'
                OR (
                    type = 'pattern'
                    AND title NOT GLOB 'IDEA-*'
                    AND title NOT GLOB 'ISSUE-*'
                    AND (
                        lower(title) LIKE '%anomal%'
                        OR lower(title) LIKE '%bypass%'
                        OR lower(title) LIKE '%escalation%'
                        OR lower(title) LIKE '%completion%'
                        OR lower(title) LIKE '%dismissed%'
                        OR lower(content) LIKE '%l2=%'
                        OR lower(content) LIKE '%§8%'
                    )
                )
            )
            AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            """,
            (f"-{DAYS} days",),
        ).fetchall()
    finally:
        conn.close()

    observations = []
    for obs_id, obs_type, title, content, created_at in rows:
        key = normalize(title)
        if not key or len(key.split()) < 2:
            continue
        observations.append(
            {
                "id": obs_id,
                "type": obs_type,
                "title": title,
                "key": key,
                "has_structural_gate": bool(STRUCTURAL_GATE_RE.search(content or "")),
                "is_governance_entry": bool(GOV_TITLE_RE.match(title)),
            }
        )
    return observations


def cluster_observations(observations: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    assigned: set[int] = set()
    for i, obs in enumerate(observations):
        if i in assigned:
            continue
        cluster = [obs]
        assigned.add(i)
        for j, other in enumerate(observations):
            if j in assigned:
                continue
            if jaccard(obs["key"], other["key"]) >= JACCARD_THRESHOLD:
                cluster.append(other)
                assigned.add(j)
        if len(cluster) >= THRESHOLD:
            clusters.append(cluster)
    return clusters


def find_ungoverned(clusters: list[list[dict]]) -> list[dict]:
    ungoverned = []
    for cluster in clusters:
        if any(o["has_structural_gate"] or o["is_governance_entry"] for o in cluster):
            continue
        ungoverned.append(
            {
                "class": cluster[0]["key"],
                "count": len(cluster),
                "obs_ids": [o["id"] for o in cluster[:5]],
                "titles": [o["title"][:70] for o in cluster[:3]],
            }
        )
    return ungoverned


def main() -> None:
    observations = query_observations()
    clusters = cluster_observations(observations)
    ungoverned = find_ungoverned(clusters)
    print(json.dumps(ungoverned))


if __name__ == "__main__":
    main()
