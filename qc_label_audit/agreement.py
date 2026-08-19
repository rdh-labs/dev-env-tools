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


# Krippendorff's lowest conceivable bound for drawing even TENTATIVE conclusions from
# labelled data; below it, data is conventionally discarded rather than reported. Chosen
# over Landis-Koch's 0.61 "substantial" band deliberately -- see admissibility() -- because
# what is being drawn here IS a conclusion from labelled data, which is exactly the case
# Krippendorff's bound governs, whereas Landis-Koch is a descriptive vocabulary for kappa.
KAPPA_FLOOR = 0.667


def admissibility(pairs: list[tuple[str, str]], floor: float = KAPPA_FLOOR) -> dict:
    """Is this rater set reliable enough to compute a rate from -- and if not, WHY?

    WHY A DIAGNOSIS AND NOT JUST A THRESHOLD. verdict.py's own header records DEC-320 /
    Dart qcL10RSMREjW: admissible reliability requires "a rubric-based κ bar + a DIAGNOSED
    low-κ cause, not a bare Landis-Koch κ>=0.6 veto". A bare threshold tells you the number
    is untrustworthy and nothing about what to do next, so it gets overridden or ignored.
    This returns the cause alongside the verdict.

    THE DIAGNOSIS THAT MATTERS is whether disagreement is SYMMETRIC or DIRECTIONAL:
      directional -- one rater systematically calls TP where the other calls FP. That is a
        rubric/anchoring defect: the two raters are applying different thresholds, and
        sharpening the rubric or fixing the prompt can genuinely fix it.
      symmetric  -- disagreements scatter both ways. That is noise, and rubric iteration
        will NOT fix it; it means the task as posed is underdetermined for these raters.
    Conflating the two is why "sharpen the rubric" keeps getting proposed for corpora where
    it cannot work.

    THREE bands, not two, and the middle one exists because forcing a binary call
    over-claimed on the first real corpus tried. Bands by |skew|: >=0.50 DIRECTIONAL (about
    3:1 or worse one way), 0.25-0.50 MIXED, <0.25 SYMMETRIC. a101 measured on its actual
    per-fire labels lands at skew 0.385 (16 vs 36 of 52 disagreements, kappa 0.1057, n=123)
    -- a genuine 2.25:1 lean with a third running the other way. Calling that "symmetric,
    rubric cannot help" would have been as wrong as calling it cleanly directional. MIXED
    says what is actually known: rubric work may move it partway, and betting the audit on
    that alone is unwarranted.
    """
    res = [(a, b) for a, b in pairs if a in _BINARY and b in _BINARY]
    n = len(res)
    # GATES ON SCOTT'S PI, not Cohen's kappa: 0.667 is Krippendorff's bound, and for two
    # raters on nominal data Krippendorff's alpha IS Scott's pi. Cohen credits rater bias as
    # agreement and is therefore lenient against this threshold (+0.0257 on a101). Cohen is
    # still REPORTED, because every prior artifact in this repo records kappa and dropping it
    # would silently break comparability with them.
    k = scott_pi(pairs)
    k_cohen = cohen_kappa(pairs)
    disagreements = [(a, b) for a, b in res if a != b]
    a_tp_b_fp = sum(1 for a, b in disagreements if a == "TP")
    a_fp_b_tp = sum(1 for a, b in disagreements if a == "FP")
    d = len(disagreements)
    # Directionality: 1.0 = every disagreement runs the same way, 0.0 = perfectly balanced.
    skew = abs(a_tp_b_fp - a_fp_b_tp) / d if d else None
    # `band` is a discrete key, not just a word inside `cause`. Two independent review legs
    # flagged the prose-only version: a caller wanting to branch on DIRECTIONAL vs SYMMETRIC
    # had to substring-match an English sentence, which is the stringly-typed pattern this
    # workspace keeps getting burned by. `cause` stays for humans; `band` is for code.
    band = None
    if k is None:
        cause = "kappa undefined (n=0, or one rater used a single label throughout)"
        band = "UNDEFINED"
    elif k >= floor:
        cause = None
        band = "ADMISSIBLE"
    elif skew is None:
        cause = f"kappa {k:.3f} below floor; no disagreements to diagnose"
        band = "NO-DISAGREEMENTS"
    elif skew >= 0.50:                                   # >= 3:1 one way
        band = "DIRECTIONAL"
        cause = (f"DIRECTIONAL disagreement: {a_tp_b_fp} vs {a_fp_b_tp} of {d} (skew "
                 f"{skew:.2f}). The raters are applying DIFFERENT THRESHOLDS, not guessing. "
                 f"Rubric/prompt work can plausibly move this above the floor.")
    elif skew >= 0.25:                                   # roughly 5:3 .. 3:1
        band = "MIXED"
        cause = (f"MIXED disagreement: {a_tp_b_fp} vs {a_fp_b_tp} of {d} (skew {skew:.2f}). "
                 f"A real lean, but a substantial minority runs the other way. Rubric work "
                 f"may move this PARTWAY; do NOT assume it alone clears the floor.")
    else:
        band = "SYMMETRIC"
        cause = (f"SYMMETRIC disagreement: {a_tp_b_fp} vs {a_fp_b_tp} of {d} (skew "
                 f"{skew:.2f}). Scatter in both directions -- the task as posed is "
                 f"underdetermined for these raters. Rubric iteration will NOT fix this; "
                 f"the item definition or the rater set has to change.")
    # `band` is RETURNED, not merely assigned. The first version of this change set the
    # variable in two of five branches and never added it to this dict, so the key did not
    # exist at all -- and all 11 controls stayed green, because none of them touched it.
    # Caught the moment a control was written for it. An addition no test asserts is
    # indistinguishable from an addition that was never made.
    return {"admissible": bool(k is not None and k >= floor),
            # `kappa` keeps its name for artifact compatibility but now carries SCOTT'S PI,
            # the statistic the floor is defined for. `cohen_kappa` is reported alongside so
            # the difference is visible rather than silently swapped under a reader.
            "kappa": k, "statistic": "scott_pi", "cohen_kappa": k_cohen, "floor": floor,
            "band": band, "n_resolved": n, "n_disagreements": d, "skew": skew,
            "a_tp_b_fp": a_tp_b_fp, "a_fp_b_tp": a_fp_b_tp, "cause": cause}


def scott_pi(pairs: list[tuple[str, str]]) -> float | None:
    """Scott's pi over resolved binary pairs. None if undefined.

    WHY THIS EXISTS ALONGSIDE cohen_kappa, and it is a correctness fix, not a nicety.
    The 0.667 floor is KRIPPENDORFF'S bound. For two raters on nominal data Krippendorff's
    alpha is (asymptotically) SCOTT'S PI, not Cohen's kappa. The two differ in how they
    compute chance agreement: Cohen uses each rater's OWN marginals, Scott uses the POOLED
    marginals. Cohen therefore treats systematic rater BIAS as if it were agreement, which
    makes it the more LENIENT statistic -- so gating Krippendorff's threshold on Cohen's
    kappa lets data through that the threshold was never meant to admit.

    Measured on the a101 corpus (n=123, po=0.5772):
        Cohen kappa = 0.1057   (pe 0.5273)
        Scott pi    = 0.0800   (pe 0.5405)   <- what 0.667 actually governs
        Cohen is +0.0257 more lenient.
    Not enough to flip a101, which fails either way. Enough to flip a borderline corpus,
    which is the only place a floor matters. Found by an independent architecture review;
    the numbers above were reproduced before the change was made.
    """
    res = [(a, b) for a, b in pairs if a in _BINARY and b in _BINARY]
    n = len(res)
    if n == 0:
        return None
    po = sum(1 for a, b in res if a == b) / n
    # POOLED marginals -- the single distinction from cohen_kappa.
    pooled = {c: (sum(1 for a, _ in res if a == c) + sum(1 for _, b in res if b == c)) / (2 * n)
              for c in _BINARY}
    pe = sum(p * p for p in pooled.values())
    if abs(1 - pe) < 1e-12:
        return None
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
