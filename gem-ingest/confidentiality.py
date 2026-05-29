#!/usr/bin/env python3
"""Content-based confidentiality gate for Gemini-Gems eval ingestion.

Phase A of the approved process design (plan v2.1, ~/.claude/plans/sorted-prancing-blum.md).

CORRECTED PER QC: topic is NOT a safe proxy for confidentiality. A tech eval can still
name a client, incident, stack fingerprint, or contract. Therefore every item is scanned
by CONTENT before any write.

Two HARD checks — any hit => QUARANTINE (never ingested, flagged for manual review):
  1. credential_scanner.scan_content  — secrets/keys, reused from ~/.claude/hooks/security
  2. client-name denylist             — Richard-supplied terms/regexes (~/.config/gem-ingest/denylist.txt)

SOFT signals — always reported (the FP/FN audit), and quarantine only on a genuinely-risky
combination (an SSN, or third-party PII appearing alongside dispute/legal language). Bare
mentions of words like "confidential" are common in tech/governance docs and do NOT quarantine
on their own, to keep false positives from blocking a legitimate tech-eval backlog.

FAIL-CLOSED: unlike the credential_scanner *hook* (which fails open so a scanner bug never
blocks tool use), a confidentiality *ingestion* gate must fail CLOSED. If the scanner cannot
load or raises, the item is QUARANTINED, not ingested — the safe default when the question is
"might this leak confidential content into a searchable store?"
"""
from __future__ import annotations

import html as _html
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

CLEAR = "CLEAR"
QUARANTINE = "QUARANTINE"

DEFAULT_DENYLIST = Path.home() / ".config/gem-ingest/denylist.txt"

# Richard's own addresses — not third-party PII; excluded from the email soft-signal.
_OWN_EMAILS = {"rhart@proactive-resolutions.com"}
_EXCLUDED_EMAIL_DOMAINS = {"example.com", "test.com", "example.org", "email.com"}

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# US-style phone numbers, deliberately conservative to avoid matching version strings / IDs.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_LEGAL_RE = re.compile(
    r"(?i)\b(?:plaintiff|defendant|litigation|settlement|grievance|arbitration|"
    r"privileged|claimant|respondent|deposition|subpoena|wrongful\s+dismissal)\b"
)


@dataclass
class Assessment:
    """Result of scanning one item's content."""

    verdict: str = CLEAR
    hard_reasons: List[str] = field(default_factory=list)   # credential / denylist (always quarantine)
    soft_signals: List[str] = field(default_factory=list)   # reported; may quarantine on risky combo
    scanner_ok: bool = True                                  # False => gate failed closed

    @property
    def all_reasons(self) -> List[str]:
        return list(self.hard_reasons) + list(self.soft_signals)


def _redact(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def strip_html(s: str) -> str:
    """Best-effort HTML -> plain text (Gemini JSON exports store answers as HTML).

    Uses only the linear ``<[^>]+>`` tag strip — no lazy quantifier or backreference — so a
    malformed/unclosed tag in an export cannot cause catastrophic backtracking (ReDoS) on the
    gate path. Script/style *content* (absent from Gemini answer HTML) would survive as text,
    which is harmless and still gets credential-scanned.
    """
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def load_denylist(path: Optional[Path] = None) -> List[tuple]:
    """Load the client-name denylist.

    Each non-empty, non-comment line is one term. Lines starting with ``re:`` are treated as
    regular expressions; all other lines are case-insensitive substring matches. Returns a list
    of ``(kind, compiled_or_lowered)`` tuples. A missing file yields an empty denylist (the
    credential + soft checks still run).
    """
    path = path or Path(os.environ.get("GEM_INGEST_DENYLIST", DEFAULT_DENYLIST))
    terms: List[tuple] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return terms
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            pat = line[3:].strip()
            try:
                terms.append(("regex", re.compile(pat, re.IGNORECASE)))
            except re.error:
                # A silently-skipped denylist rule is a silent security-rule failure — surface it.
                print(f"gem-ingest: WARNING skipping malformed denylist regex: {pat!r}",
                      file=sys.stderr)
                continue
        else:
            terms.append(("literal", line.lower()))
    return terms


def _load_credential_scanner():
    """Import and instantiate the shared CredentialScanner. Raises on failure (caller fails closed)."""
    hooks = str(Path.home() / ".claude/hooks")
    sec = str(Path.home() / ".claude/hooks/security")
    for p in (hooks, sec):
        if p not in sys.path:
            sys.path.insert(0, p)
    import credential_scanner as _cs  # raises ImportError if unavailable -> fail closed
    return _cs.CredentialScanner()


def _check_credentials(text: str, assessment: Assessment) -> None:
    try:
        scanner = _load_credential_scanner()
        has_creds, violations = scanner.scan_content(text)
    except Exception as exc:  # noqa: BLE001 — any failure must fail closed
        assessment.scanner_ok = False
        assessment.hard_reasons.append(f"credential-scanner-unavailable ({type(exc).__name__}) -> fail-closed")
        assessment.verdict = QUARANTINE
        return
    if has_creds:
        assessment.hard_reasons.extend(violations)
        assessment.verdict = QUARANTINE


def _check_denylist(text: str, denylist: List[tuple], assessment: Assessment) -> None:
    lowered = text.lower()
    for kind, term in denylist:
        if kind == "literal":
            if term in lowered:
                assessment.hard_reasons.append(f"denylist match: {_redact(term)}")
                assessment.verdict = QUARANTINE
        else:  # regex
            m = term.search(text)
            if m:
                assessment.hard_reasons.append(f"denylist regex: {_redact(m.group(0))}")
                assessment.verdict = QUARANTINE


def _collect_soft_signals(text: str) -> tuple:
    third_party_emails = sorted(
        {
            e
            for e in _EMAIL_RE.findall(text)
            if e.lower() not in _OWN_EMAILS
            and e.lower().rsplit("@", 1)[-1] not in _EXCLUDED_EMAIL_DOMAINS
        }
    )
    phones = _PHONE_RE.findall(text)
    ssns = _SSN_RE.findall(text)
    legal = sorted({m.lower() for m in _LEGAL_RE.findall(text)})
    return third_party_emails, phones, ssns, legal


def assess(
    text: str,
    source_name: str = "",
    denylist: Optional[List[tuple]] = None,
    strict: bool = False,
) -> Assessment:
    """Assess one item's content for confidentiality risk.

    ``strict`` (used for the first backlog pass, before the denylist is populated) lowers the
    soft-signal quarantine threshold to also catch third-party PII or dispute language on its own.
    """
    a = Assessment()
    if denylist is None:
        denylist = load_denylist()

    _check_credentials(text, a)
    _check_denylist(text, denylist, a)

    emails, phones, ssns, legal = _collect_soft_signals(text)
    if emails:
        a.soft_signals.append(f"third-party email(s): {len(emails)} (e.g. {_redact(emails[0])})")
    if phones:
        a.soft_signals.append(f"phone-number-like: {len(phones)}")
    if ssns:
        a.soft_signals.append(f"SSN-pattern: {len(ssns)}")
    if legal:
        a.soft_signals.append(f"dispute/legal terms: {', '.join(legal[:5])}")

    # Risky-combo soft quarantine (applies in all modes):
    #   an SSN, OR third-party PII appearing alongside >=2 distinct dispute/legal terms.
    if ssns or (emails and len(legal) >= 2):
        if a.verdict != QUARANTINE:
            a.hard_reasons.append("soft-signal risk combo (PII + dispute context)")
        a.verdict = QUARANTINE

    if strict and a.verdict != QUARANTINE:
        # Conservative first-pass: any third-party PII or any legal term flags for review.
        if emails or phones or legal:
            a.hard_reasons.append("strict-mode review flag (PII or legal term present)")
            a.verdict = QUARANTINE

    return a


if __name__ == "__main__":  # tiny manual smoke check
    sample = sys.stdin.read() if not sys.stdin.isatty() else "normal tech eval text"
    res = assess(sample, strict=False)
    print(f"verdict={res.verdict} scanner_ok={res.scanner_ok}")
    for r in res.all_reasons:
        print(f"  - {r}")
