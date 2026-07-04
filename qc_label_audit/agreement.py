#!/usr/bin/env python3
"""agreement.py — inter-rater agreement + interval math for the QC label audit.

Hand-rolled (no scipy — heavy on 6GB WSL2; and no reusable kappa exists in the ecosystem).
Binary categories {TP, FP}; pairs where either rater is non-{TP,FP} are excluded from
kappa/%agreement (reported separately as n_uncertain by the caller).
"""
from __future__ import annotations

import math

_BINARY = ("TP", "FP")


def pct_agreement(pairs: list[tuple[str, str]]) -> float | None:
    """Fraction of resolved pairs where the two labels match."""
    res = [(a, b) for a, b in pairs if a in _BINARY and b in _BINARY]
    if not res:
        return None
    return sum(1 for a, b in res if a == b) / len(res)


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa over resolved binary pairs. None if undefined (n=0 or pe==1)."""
    res = [(a, b) for a, b in pairs if a in _BINARY and b in _BINARY]
    n = len(res)
    if n == 0:
        return None
    po = sum(1 for a, b in res if a == b) / n
    pa = {c: sum(1 for a, _ in res if a == c) / n for c in _BINARY}
    pb = {c: sum(1 for _, b in res if b == c) / n for c in _BINARY}
    pe = sum(pa[c] * pb[c] for c in _BINARY)
    if abs(1 - pe) < 1e-12:
        return None  # undefined (e.g. both raters all one label)
    return (po - pe) / (1 - pe)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score 95% CI for a proportion k/n. None if n==0."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def concordance(consensus: list[str], anchor: list[str]) -> dict:
    """LLM-consensus vs deterministic-anchor agreement, only where anchor is TP/FP."""
    pairs = [(c, a) for c, a in zip(consensus, anchor)
             if a in _BINARY and c in _BINARY]
    if not pairs:
        return {"n_checkable": 0, "concordant": 0, "rate": None}
    agree = sum(1 for c, a in pairs if c == a)
    return {"n_checkable": len(pairs), "concordant": agree, "rate": agree / len(pairs)}
