#!/usr/bin/env python3
"""Measure how much of a session's QC the USER performed, from the session transcript.

WHY THIS EXISTS. On 2026-08-11 a session was measured: 109 user messages, of which 31 demanded
the gaps #1/#2/#3 schema, 22 declared "THIS IS AN ANOMALY", 12 quoted the agent's own Open:/You:
line back at it, 10 asked "anything else under ss11", and 5 said "you have done it AGAIN". The
agent self-detected roughly 2 of ~14 anomalies. The user WAS the enforcement mechanism.

That had never been recorded as data. It was known the way a mood is known.

WHY A MEASUREMENT AND NOT A GATE — this is the load-bearing design decision. Dart
`O7t4WAplaNNk` carries a 2026-07-05 directive: CONSOLIDATE-AND-NET-SUBTRACT, do not add
mechanism #119, citing Huang et al. ICLR 2024 (arXiv:2310.01798) that intrinsic self-correction
— correction without external feedback — does not work. A gate here would be one more internal
self-check, which is precisely the intervention the literature says fails. So this instrument
does not block, warn, or advise. It COUNTS the external signal, so that any future claim of
"the agent self-gates better now" becomes falsifiable instead of felt.

WHAT IT CANNOT DO, stated because a measurement that oversells itself is worse than none:
- It counts USER PROMPTS, not agent violations. One prompt can cover several; several can
  cover one. It is a proxy, and the proxy's direction is meaningful while its magnitude is not.
- Patterns are literal-string matches over the user's own words. A user who phrases a
  correction differently is invisible to it. That is the same form-not-substance limit this
  session catalogued repeatedly, and it applies here too.
- A LOW score can mean the agent improved, OR that the user gave up. The instrument cannot
  distinguish those, and the second is the outcome that matters most. Read it alongside
  session length and whether work actually shipped.

Exit codes: 0 when it measured and no --alert-above threshold is breached (it reports, it does not
judge); 1 when the windowed mean breaches --alert-above; 2 when there is nothing to measure. (An
older line here said "Exit 0 always", which the code has not done since the threshold was added.)

MECHANISM HEADER (back-filled 2026-09-27, session 0a10c312; Dart EUVVnvFyRHeJ found 34 of 35
scheduled checks without one):
OBJECTIVE:   OBJECTIVES.md Tier 1 "System Catches Own Errors". The standing rules files
             (~/.claude/rules/*.md) name this tool as their effect measure: if the agent catches its
             own errors, the user issues fewer corrections. The metric serves the objective only if
             it counts the USER's words, hence the 2026-09-27 provenance rule.
SUCCESS:     the windowed session-mean correction rate falls over successive windows while sessions
             keep shipping work, and every run prints its rate and its definition.
FAILURE:     the rate rises or holds; OR the tool cannot measure (no transcripts or no sessions in the
             window: rc 2, never read as healthy); OR the definition changes without a re-baseline.
TRIGGERS:    date-driven, weekly (crontab Mon 06:47, scheduled-check-runner `correction-rate`). An
             event trigger was considered and rejected: a correction happens at UserPromptSubmit, but
             this is a cross-session TREND, and a per-prompt alarm would tell the user what they have
             just done themselves. The event-driven counterparts are the in-session gates that
             consume the SAME rules. REVISITED 2026-09-29 (session 1bdb029f): that reasoning
             misses the SYSTEM as a consumer (a prompt-time fingerprint of a pasted directive could
             feed its promotion into a rules file); proposal tracked on Dart EUVVnvFyRHeJ.
             PASTE-COUNT MODE (--phrase, added 2026-09-29): on demand; it is the shared counter the
             rules/*.md effect measures point at, because ad-hoc whole-file greps over-counted every
             rules phrase 1.4x-8x (any record, not the user's words).
CONSUMER:    scheduled-check-runner (it pages on its marker or a non-zero rc); the rules files'
             effect-measure targets (<= 10% of sessions by 2026-10-21); sessions that re-measure
             before claiming a rule works.
SILENT-FAIL: rc 2 with an ADVERSE line when there is nothing to measure; the definition is printed on
             every run, so a changed rule is visible in the output rather than inferred.
PRIOR-ART:   Huang et al. ICLR 2024 (arXiv:2310.01798) on intrinsic self-correction; ~/bin
             route_ledger.py (promptSource); anomaly-initiation-rate.py (interaction-scoped measure).
PROMOTION:   never blocking, by design: it measures the user, not an agent action, so there is
             nothing to gate. Its numbers feed decisions about promoting the rules' own gates.
predicate-rung: occurrence -- --self-check (16 checks, including 2 provenance cases); a mutant without
             the provenance filter fails both.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"

# The user's own recurring phrasings. Deliberately literal: an inferred "sentiment" score would
# be unfalsifiable, which is the defect this whole exercise is about.
PATTERNS = {
    "declared_anomaly":      re.compile(r"THIS IS AN ANOMALY", re.I),
    "demanded_gaps_schema":  re.compile(r"gaps? #1|gaps \(gaps #1\)", re.I),
    "quoted_my_tail_back":   re.compile(r'"Open[:—]|"You:|"Open —|Open / RISKS', re.I),
    "asked_anything_else":   re.compile(r"anything else that we can/should complete", re.I),
    "you_did_it_again":      re.compile(r"done it AGAIN", re.I),
    "asked_did_you_run_qc":  re.compile(r"did you run|have you run|are you satisfied", re.I),
    # Added 2026-08-27 after measuring the corpus: the THREE patterns below were the single
    # largest blind spot in this instrument. "DO NOT JUMP TO CONCLUSIONS" alone accounts for
    # 656 user issuances across 369 sessions (measured over ~/.claude/projects, 2026-08-27) --
    # the most-issued correction template in the workspace, and this file could not see ANY of
    # it. An instrument built to count corrections was blind to the most common correction.
    # Rate in substantive sessions (>=5 user turns, non-sidechain): May 47.8% -> Jul 38.0% ->
    # Aug 84.2%, i.e. the trend this file exists to expose was rising while it read 0.
    "demanded_no_premature_conclusion": re.compile(r"DO NOT JUMP TO CONCLUSIONS", re.I),
    "demanded_multi_line_inquiry":      re.compile(r"(multiple|single) lines? of inquiry", re.I),
    "demanded_prior_art_check":         re.compile(r"reinvent the wheel", re.I),
}


# WHO IS SPEAKING is decided by PROVENANCE, not by text (definition change, 2026-09-27, session
# 0a10c312). A transcript row with type "user" is anything delivered in the user ROLE, not only the
# user's own words: peer-session messages, task notifications, skill and hook injections (isMeta)
# and slash-command caveats all arrive that way. Measured over the 69 transcripts of the previous
# 30 days: of 3,363 rows the old text-only filter counted as "the user", 725 (21.6%) had
# origin.kind == "human". The rest were isMeta 1,624, peer 398, slash-command rows 389, task
# notifications 210, other 17. Those rows DILUTED the rate (a bigger denominator) and could add
# corrections the user never made (a peer quoting "THIS IS AN ANOMALY").
# RE-BASELINED AT THE CHANGE (both runs `--since-days 30`, 2026-09-27 ~22:00 PDT, same corpus):
#   old definition: 74 sessions, windowed mean 11%
#   new definition: 58 sessions, windowed mean 41% (decided per row: origin.kind when present, else
#     not isMeta and no machine prefix; drafts gave 60/42% and, with a rejected per-transcript
#     rule, 58/42%)
# So the measure was diluted about 4x. Any target set against the old number (the rules files'
# "<= 10% by 2026-10-21") was set against a figure that hid most corrections. Compare later runs
# with 42%, never 11%: a drop from 42% is a real change; a "drop" from 11% was never available.
DEFINITION = ("the user's own words only, since 2026-09-27: a row with origin counts iff origin.kind == "
              "'human'; a row without origin counts unless isMeta or a machine-wrapper prefix "
              "(decided per row). Before: every text user-role row.")
_MACHINE_PREFIXES = ("<task-notification", "<local-command", "<command-name", "<command-message",
                     "<cross-session-message", "Another Claude session", "[Cross-session",
                     "Stop hook feedback", "<agent-message", "Caveat: The messages below",
                     "This session is being continued from a previous conversation")


def is_human_row(d: dict, txt: str) -> bool:
    """True when a user-role transcript row carries the user's own words.

    A row WITH `origin` is decided by origin.kind. A row WITHOUT it is decided PER ROW: not isMeta and
    no machine-wrapper prefix. NOT per transcript. The origin field is written on a minority of rows
    even within a transcript: in 436 of 524 origin-bearing transcripts, under 10% of user rows carry
    it, and genuine typed prompts sit between them without one (round-6 review, 2026-09-28; a
    "no origin => machine" rule dropped most real turns). The prefixes cover the origin-less machine
    shapes found by the round-5 review: compaction-continuation summaries (1,303 rows, 262 matching
    correction patterns, i.e. double counts) and `<command-message>` rows (942)."""
    origin = d.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return origin.get("kind") == "human"
    return not d.get("isMeta") and not txt.lstrip().startswith(_MACHINE_PREFIXES)


def _flatten(c) -> str:
    """Message content (a string, or a list of blocks) -> its text blocks joined."""
    return c if isinstance(c, str) else "".join(
        b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")


def _own_words(txt: str) -> bool:
    """Tool results, system reminders and machine-injected prompts are not the user speaking."""
    return bool(txt.strip()) and "<system-reminder>" not in txt[:200] and not txt.lstrip().startswith(_MACHINE_PREFIXES)


def phrase_needles(ph: str) -> tuple[str, ...]:
    """The forms a phrase takes INSIDE a raw JSONL line: as typed, JSON-escaped (quotes, backslashes,
    newlines), and \\u-escaped (non-ASCII written with ensure_ascii). A raw-text prefilter that tests only
    the typed form drops real hits for any phrase holding one of those characters and reports 0 (review Q3)."""
    return tuple(dict.fromkeys((ph, json.dumps(ph, ensure_ascii=False)[1:-1], json.dumps(ph)[1:-1])))


def load_user_messages(path: Path, include_queued: bool = False, needles: tuple[str, ...] = ()) -> list[str]:
    """The user's own words. include_queued (paste mode, 2026-09-29): ALSO the user's MID-TURN messages,
    which the transcript records as `attachment` records of type `queued_command` (origin.kind human), not
    as `type: user` rows. An Opus review leg measured the TRIGGERS directive counted in 8 sessions and MISSED
    in 21 without them. The RATE mode keeps its definition (a silent definition change is this tool's own
    FAILURE condition): its matching undercount is tracked for an explicit re-baseline (Dart EUVVnvFyRHeJ).
    needles (paste mode): skip the JSON parse of any line holding none of them. A record whose text holds
    the phrase WITHIN ONE text block holds one of phrase_needles() in its raw line (JSON escapes covered:
    quote, backslash, control chars, \\u; not `\\/`, which Node never writes). It cut a 55 s / 846 MB run.
    LIMIT: a phrase split across two text BLOCKS of one message exists only after the join, so it is
    missed (as the earlier whole-file prefilter missed it). Measured 2026-09-30: 0 multi-text-block user
    rows in 179 top-level transcripts; re-measure if Claude Code starts splitting typed prompts."""
    out = []
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if needles and not any(n in line for n in needles):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("type") == "user":
                rows.append(d)
            elif include_queued and d.get("type") == "attachment":
                a = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
                origin = a.get("origin") if isinstance(a.get("origin"), dict) else {}
                if a.get("type") == "queued_command" and origin.get("kind") == "human":
                    txt = _flatten(a.get("prompt"))
                    if _own_words(txt):     # same text filter as a user row (review: reuse leg 6)
                        out.append(txt)
    for d in rows:
        txt = _flatten(d.get("message", {}).get("content", []))
        # Provenance: nothing another session, a notification or an injection sent counts. This predicate
        # is the RATE mode's DEFINITION: is_human_row decides machine prefixes itself (origin rows are
        # trusted by origin), so _own_words is NOT applied here (delta review MEDIUM: 20 rows changed).
        if txt.strip() and "<system-reminder>" not in txt[:200] and is_human_row(d, txt):
            out.append(txt)
    return out


# Whitespace-TOLERANT. The first version scraped the fixed literal `"timestamp":"` with
# str.find. Independent review (gpt-5.5, 2026-08-27) flagged it as fragile and the corpus
# CONFIRMED it: at least 3 transcript files under ~/.claude/projects write `"timestamp": "`
# WITH A SPACE. Those returned None from last_record_utc and were dropped from the window
# entirely -- silently, and in the under-counting direction, which is the same failure class
# as the mtime bug this file already carries a note about. Two silent-under-inclusion paths
# in one function is a pattern, not a coincidence: prefer structural parsing over literal
# scraping whenever the input is JSON.
_TS_RE = re.compile(r'"timestamp"\s*:\s*"([^"]{19,})"')


def is_adverse(mean_rate: float | None, threshold: float | None) -> bool:
    """THE SUCCESS/FAILURE CRITERION, isolated so both polarities can be controlled.
    No threshold configured => no defined failure condition => never adverse (and the caller
    says so out loud, because an undefined criterion silently reading 'healthy' is the defect
    this whole file is about)."""
    if threshold is None or mean_rate is None:
        return False
    return mean_rate >= threshold


def last_record_utc(path: Path) -> datetime | None:
    """Timestamp of the session's LAST record. mtime is a cheap PRE-filter only: a file can be
    touched without a new record, and norm-compliance-monitor.py documents mtime-vs-record drift
    as a real defect. So mtime narrows the candidate set; the record timestamp decides."""
    last = None
    try:
        # Tail first (the last record's timestamp is near the end): 12.4 s -> 0.01 s over 178 files
        # (efficiency leg, 2026-09-30). Full scan only when the tail holds no timestamp at all, e.g. a
        # final multi-MB tool_result line.
        # Same answer as the full scan: the FIRST match on the LAST line that has one (a record can carry
        # nested timestamps; delta review MEDIUM). The tail's first line may be cut, so it is dropped.
        with open(path, "rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - 65536))
            tail_lines = fh.read().decode("utf-8", errors="replace").splitlines()
        if size > 65536:
            tail_lines = tail_lines[1:]
        for line in reversed(tail_lines):
            m = _TS_RE.search(line)
            if m:
                last = m.group(1)
                break
        if last is None:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = _TS_RE.search(line)
                    if m:
                        last = m.group(1)
    except OSError:
        return None
    if not last:
        return None
    try:
        return datetime.strptime(last[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def measure(messages: list[str]) -> dict:
    counts = {k: sum(1 for m in messages if rx.search(m)) for k, rx in PATTERNS.items()}
    total = len(messages)
    # A message may match several patterns; count DISTINCT corrective messages so the rate
    # cannot exceed 1.0. A rate above 1.0 would mean the denominator is wrong.
    corrective = sum(1 for m in messages if any(rx.search(m) for rx in PATTERNS.values()))
    return {"user_messages": total, "corrective_messages": corrective,
            "correction_rate": round(corrective / total, 3) if total else None,
            "by_pattern": counts}


def self_check() -> int:
    ok = []
    r = measure(["THIS IS AN ANOMALY and gaps #1 too", "hello", "done it AGAIN"])
    ok.append(("a message matching TWO patterns counts ONCE as corrective",
               r["corrective_messages"] == 2))
    ok.append(("rate can never exceed 1.0", r["correction_rate"] <= 1.0))
    ok.append(("per-pattern counts still count both", r["by_pattern"]["declared_anomaly"] == 1
               and r["by_pattern"]["demanded_gaps_schema"] == 1))
    ok.append(("an empty session yields None, not 0.0 (no data is not a good score)",
               measure([])["correction_rate"] is None))
    ok.append(("a clean session scores 0.0", measure(["thanks", "ok"])["correction_rate"] == 0.0))
    # POSITIVE controls for the 2026-08-27 additions: each new pattern must actually fire.
    for key, probe in [("demanded_no_premature_conclusion", "DO NOT JUMP TO CONCLUSIONS OR ACTION"),
                       ("demanded_multi_line_inquiry",      "run multiple lines of inquiry"),
                       ("demanded_prior_art_check",         "so we don't reinvent the wheel")]:
        ok.append((f"positive control fires: {key}",
                   measure([probe])["by_pattern"][key] == 1))
    # NEGATIVE control: prose that is ABOUT the topic but is not the user correcting must NOT
    # fire, or the rate inflates every time the subject is merely discussed.
    neg = measure(["the wheel on the cart is broken", "I have a single line of code"])
    ok.append(("negative control silent: topic-adjacent prose does not fire",
               neg["corrective_messages"] == 0))
    # BOTH POLARITIES on the failure criterion. A positive-only control cannot fail.
    ok.append(("criterion fires when mean >= threshold",  is_adverse(0.50, 0.25) is True))
    ok.append(("criterion silent when mean < threshold",  is_adverse(0.10, 0.25) is False))
    ok.append(("criterion silent when no threshold set",  is_adverse(0.99, None) is False))
    ok.append(("criterion silent when nothing measured",  is_adverse(None, 0.25) is False))
    ok.append(("boundary: equal to threshold IS adverse", is_adverse(0.25, 0.25) is True))
    # PASTE-COUNT MODE (2026-09-29): a session counts once, only for the user's OWN words, and only
    # top-level session files are in the population. Each exclusion has its own negative control.
    import contextlib
    import io
    import tempfile as _tfp
    PH = "consider whether event-driven triggers were necessary"
    with _tfp.TemporaryDirectory() as _d:
        root = Path(_d)
        (root / "p" / "s3" / "subagents").mkdir(parents=True)
        def _w(path, recs):
            path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        _w(root / "p" / "s1.jsonl", [{"type": "user", "origin": {"kind": "human"},
                                      "message": {"content": f"TRIGGERS: {PH}? twice: {PH}"}}])
        _w(root / "p" / "s2.jsonl", [{"type": "user", "origin": {"kind": "peer"}, "isMeta": True,
                                      "message": {"content": f"peer quoting: {PH}"}},
                                     {"type": "user", "message": {"content": [
                                         {"type": "tool_result", "content": f"grep hit: {PH}"}]}}])
        _w(root / "p" / "s3" / "subagents" / "a.jsonl", [{"type": "user", "message": {"content": f"brief: {PH}"}}])
        # a MID-TURN paste (queued_command attachment, origin human) counts; a non-human queued one does not
        _w(root / "p" / "s4.jsonl", [{"type": "attachment", "attachment": {
            "type": "queued_command", "origin": {"kind": "human"}, "prompt": f"<pasted_content> TRIGGERS {PH}"}}])
        _w(root / "p" / "s5.jsonl", [{"type": "attachment", "attachment": {
            "type": "queued_command", "origin": {"kind": "peer"}, "prompt": f"peer relays: {PH}"}}])
        # human-origin but machine-shaped queued text (a notification) is not the user's words
        _w(root / "p" / "s6.jsonl", [{"type": "attachment", "attachment": {
            "type": "queued_command", "origin": {"kind": "human"}, "prompt": f"<task-notification> {PH}"}}])
        allp = sorted(root.rglob("*.jsonl"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            phrase_counts(allp, [PH], 30, True, root)
        res = json.loads(buf.getvalue())
    ok.append(("paste mode: population is top-level session files only (nested subagent excluded)",
               res["population"] == 5))
    ok.append(("paste mode: human pastes count ONCE per session (typed s1 + MID-TURN queued s4); peer, "
               "tool_result, subagent and a peer-origin queued command do not", res["counts"][PH] == 2))
    # A phrase holding a quote and a non-ASCII char is JSON-escaped in the raw line: the raw prefilter
    # must still find it (review Q3). The tail-first timestamp read must fall back to a full scan when the
    # final 64 KB hold no timestamp, and must return the LAST timestamp, not the first.
    PHQ = 'the "shape" — fix it'
    with _tfp.TemporaryDirectory() as _d:
        root = Path(_d)
        (root / "p").mkdir()
        _w(root / "p" / "q1.jsonl", [{"type": "user", "origin": {"kind": "human"}, "message": {"content": f"x {PHQ}"}}])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            phrase_counts([root / "p" / "q1.jsonl"], [PHQ], 30, True, root)
        resq = json.loads(buf.getvalue())
        _w(root / "p" / "t1.jsonl", [{"timestamp": "2026-01-01T00:00:00Z"}, {"timestamp": "2026-02-02T00:00:00Z"},
                                     {"type": "user", "message": {"content": "y" * 70000}}])
        _w(root / "p" / "t2.jsonl", [{"timestamp": "2026-01-01T00:00:00Z"}, {"timestamp": "2026-03-03T00:00:00Z"}])
        # a record with a NESTED timestamp after its own: the full scan took the FIRST match on the last line
        _w(root / "p" / "t3.jsonl", [{"timestamp": "2026-04-04T00:00:00Z",
                                      "toolUseResult": {"timestamp": "2026-05-05T00:00:00Z"}}])
        lr1, lr2 = last_record_utc(root / "p" / "t1.jsonl"), last_record_utc(root / "p" / "t2.jsonl")
        lr3 = last_record_utc(root / "p" / "t3.jsonl")
    ok.append(("last_record_utc: first match on the last line, as the full scan did", lr3 is not None and lr3.month == 4))
    # RATE-mode definition is unchanged: a human-ORIGIN row starting with a machine prefix is still decided
    # by is_human_row alone (the paste-mode queued filter must not leak into it)
    with _tfp.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as _fh:
        _fh.write(json.dumps({"type": "user", "origin": {"kind": "human"},
                              "message": {"content": "<command-name>/exit</command-name>"}}) + "\n")
    _rate_rows = load_user_messages(Path(_fh.name))
    ok.append(("rate mode: an origin-human row is judged by is_human_row only (definition unchanged)",
               _rate_rows == ["<command-name>/exit</command-name>"] if is_human_row(
                   {"origin": {"kind": "human"}}, "<command-name>/exit</command-name>") else _rate_rows == []))
    ok.append(("paste mode: a JSON-escaped phrase (quote, non-ASCII) is still counted", resq["counts"][PHQ] == 1))
    ok.append(("last_record_utc: full-scan fallback when the tail holds no timestamp",
               lr1 is not None and lr1.month == 2))
    ok.append(("last_record_utc: tail read returns the LAST timestamp", lr2 is not None and lr2.month == 3))
    # PROVENANCE (2026-09-27): only the user's own words count. Real row shapes, from transcripts.
    import tempfile
    rows = [
        {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
         "message": {"content": "THIS IS AN ANOMALY"}},                                   # counts
        {"type": "user", "origin": {"kind": "peer"}, "promptSource": "system", "isMeta": True,
         "message": {"content": "Another Claude session sent a message: THIS IS AN ANOMALY"}},
        {"type": "user", "origin": {"kind": "task-notification"}, "promptSource": "system",
         "message": {"content": "<task-notification> done it AGAIN </task-notification>"}},
        {"type": "user", "isMeta": True, "message": {"content": "skill text: gaps #1 #2 #3"}},
        {"type": "user", "message": {"content": "<task-notification> legacy row </task-notification>"}},
        {"type": "user", "message": {"content": "legacy human row, before origin existed"}},  # counts
    ]
    def _load(rs):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rs))
        try:
            return load_user_messages(Path(fh.name))
        finally:
            Path(fh.name).unlink()
    # A transcript that HAS origin fields still holds origin-less human prompts (round-6 review):
    # they count; origin-less MACHINE shapes do not.
    got = _load(rows + [
        {"type": "user", "message": {"content": "This session is being continued from a previous "
                                                 "conversation. The user said DO NOT JUMP TO CONCLUSIONS"}},
        {"type": "user", "message": {"content": "<command-message>ship</command-message>"}},
    ])
    ok.append(("provenance: peer, notification, isMeta, continuation summary and command rows are "
               "excluded; human rows (with or without origin) count",
               got == ["THIS IS AN ANOMALY", "legacy human row, before origin existed"]))
    ok.append(("provenance: a peer or a summary quoting a correction is NOT a user correction",
               measure(got)["corrective_messages"] == 1))
    ok.append(("provenance: an origin-less typed prompt amid origin-bearing rows still counts",
               _load([rows[0], {"type": "user", "message": {"content": "proceed"}}]) ==
               ["THIS IS AN ANOMALY", "proceed"]))
    # A LEGACY transcript (no origin anywhere): the prefix fallback applies.
    legacy = _load([r for r in rows if "origin" not in r] + [
        {"type": "user", "message": {"content": "This session is being continued from a previous conversation."}}])
    ok.append(("legacy transcript: plain rows count, wrappers and continuation summaries do not",
               legacy == ["legacy human row, before origin existed"]))

    bad = [m for m, good in ok if not good]
    for m in bad:
        print(f"  [FAIL/self-check] {m}")
    if not bad:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the counting logic")
    return 1 if bad else 0


def phrase_counts(paths: list, phrases: list, since_days: int, as_json: bool, root: Path | None = None) -> int:
    """PASTE-COUNT MODE — sessions whose OWN-WORDS user text contains each fixed string.

    WHY (2026-09-29, session 1bdb029f): every ~/.claude/rules/*.md file states its baseline and effect
    measure as "pasted by the user into N of M sessions", counted by ad-hoc greps. A whole-file
    `grep -lF` counts ANY record holding the phrase (tool results, subagent prompts, peer messages,
    the agent's own grep, and — once a rules file exists — its own loaded text): measured 1.4x-8x above
    user-text counts for all nine rules phrases, and 27 vs 9 for the TRIGGERS directive. One counter
    with a printed population and unit, shared by every rules file, retires that class.
    Population: top-level transcripts only (projects/<project>/<session>.jsonl), because 1,003 of
    1,181 transcripts touched in 30 days were NESTED subagent files whose 'user' rows are an agent's
    prompt. Unit: load_user_messages() (is_human_row), the same provenance rule as the rate mode."""
    sessions = [p for p in paths if p.parent.parent == (root or PROJECTS)]
    hits = {ph: 0 for ph in phrases}
    forms = {ph: phrase_needles(ph) for ph in phrases}
    for p in sessions:
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        # Cheap pre-filter on BYTES (no decode, no second copy): skip files where no record holds any
        # form of the phrase. The first uncapped run took 3m22s; the read_text version peaked at 846 MB.
        present = [ph for ph in phrases if any(n.encode("utf-8") in raw for n in forms[ph])]
        del raw
        if not present:
            continue
        needles = tuple(n for ph in present for n in forms[ph])
        text = "\n".join(load_user_messages(p, include_queued=True, needles=needles))
        for ph in present:
            if ph in text:
                hits[ph] += 1
    win = f"last {since_days}d" if since_days else "all time"
    if as_json:
        print(json.dumps({"population": len(sessions), "window": win, "unit": "sessions (own-words user "
                          "text, is_human_row, incl. mid-turn queued_command)", "counts": hits}, indent=2))
        return 0 if sessions else 2        # nothing measured is not a zero (review LOW)
    print(f"PASTE COUNT — population: {len(sessions)} top-level session transcript(s), {win}; "
          f"nested subagent transcripts excluded")
    print(f"  unit: sessions whose OWN-WORDS user text contains the fixed string ({DEFINITION[:60]}…)")
    for ph, n in hits.items():
        share = f"{n / len(sessions):.1%}" if sessions else "n/a"
        print(f"  {n:5d}  ({share})  {ph[:70]!r}")
    return 0 if sessions else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="session uuid; default = every transcript found")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--since-days", type=int, default=0,
                    help="only sessions whose LAST record is within N days (0 = all time). "
                         "Without this the tool rescans all history and reports the all-time "
                         "worst session, which never changes -- so a weekly alert built on it "
                         "carries no information and cannot show a trend.")
    ap.add_argument("--phrase", action="append", default=None,
                    help="PASTE-COUNT MODE (repeatable): count SESSIONS whose own-words user text "
                         "contains this fixed string. Population: top-level session transcripts "
                         "(projects/<project>/<session>.jsonl; nested subagent transcripts are "
                         "excluded, their 'user' rows are an agent's prompt). Unit: this file's "
                         "is_human_row provenance rule. The rules/*.md effect measures point here.")
    ap.add_argument("--alert-above", type=float, default=None,
                    help="SUCCESS/FAILURE CRITERION. If the windowed mean rate is >= this, "
                         "print the ADVERSE: marker. Without it this tool has no defined "
                         "failure condition and its consumer cannot distinguish good from bad.")
    args = ap.parse_args()

    if args.self_check:
        print("USER CORRECTION RATE: self-check")
        return self_check()

    paths = sorted(PROJECTS.rglob(f"{args.session}.jsonl")) if args.session \
        else sorted(PROJECTS.rglob("*.jsonl"))
    if not paths:
        # NO SILENT FAILURE. rc=2 -> the wrapper maps it to `unknown`, which it treats as
        # "a check that could not RUN must shout". Returning 0 here would report health.
        print("ADVERSE: no transcripts found — cannot measure, which is not a good score")
        return 2

    if args.phrase:
        # population restriction FIRST: the window filter below reads every file it is given, and
        # 1,003 of 1,181 recent transcripts are nested subagent files this mode excludes anyway.
        paths = [p for p in paths if p.parent.parent == PROJECTS]

    if args.since_days and not args.session:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.since_days)
        # age-only: mtime is a CHEAP PRE-FILTER for recency, never an identity claim. The
        # authoritative decision is the record timestamp on the next line.
        #
        # AN EARLIER VERSION OF THIS COMMENT CLAIMED IT "can only over-include". That was
        # WRONG, and an independent review leg (gpt-5.5, 2026-08-27) caught it: a transcript
        # restored from backup or copied with -p keeps an OLD mtime while holding RECENT
        # records, and is dropped here before last_record_utc ever sees it. So the prefilter
        # can UNDER-include, which silently understates the rate. Accepted deliberately: on
        # this workspace transcripts are append-only and written in place, so the case needs
        # a restore/copy to arise. If that assumption ever breaks, drop the prefilter -- it is
        # a speed optimisation, not a correctness requirement.
        def _recent_mtime(q: Path) -> bool:
            try:
                return q.stat().st_mtime >= cutoff.timestamp()
            except OSError:
                return True   # unreadable/vanished -> do NOT silently drop; let the record
                              # timestamp decide, or let it fall out there. Dropping here
                              # would remove a session from the DENOMINATOR invisibly.
        cheap = [p for p in paths if _recent_mtime(p)]
        paths = [p for p in cheap if (lr := last_record_utc(p)) and lr >= cutoff]
        if not paths:
            print(f"ADVERSE: no sessions in the last {args.since_days}d — cannot measure")
            return 2

    if args.phrase:
        return phrase_counts(paths, args.phrase, args.since_days, args.json)

    rows = []
    for p in paths:
        msgs = load_user_messages(p)
        if len(msgs) < 5:          # too short to mean anything; excluded and SAID so
            continue
        r = measure(msgs)
        # Full id for machines, short label for humans. The 8-char form is AMBIGUOUS:
        # 4 of 1962 workspace sessions share an 8-char prefix, and one pair differs by a
        # SINGLE hex digit (ANOMALY-REGISTER 141). Display truncation is fine; emitting a
        # truncated id into --json invites a downstream join that silently merges sessions.
        r["session_id"] = p.stem
        r["session"] = p.stem[:8]      # label only — never join on this
        rows.append(r)

    rows.sort(key=lambda r: r["correction_rate"] or 0, reverse=True)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"USER CORRECTION RATE — {len(rows)} session(s) with >=5 user messages")
    print(f"  (shorter sessions excluded: too few messages for the rate to mean anything)")
    print(f"  definition: {DEFINITION}\n")
    print(f"  {'session':10} {'msgs':>5} {'corrective':>11} {'rate':>6}")
    for r in rows[:15]:
        print(f"  {r['session']:10} {r['user_messages']:5d} {r['corrective_messages']:11d} "
              f"{r['correction_rate']:6.2f}")
    if rows:
        worst = rows[0]
        print(f"\n  highest: {worst['session']} at {worst['correction_rate']:.0%} — "
              f"{worst['corrective_messages']} of {worst['user_messages']} messages were corrections")
        for k, v in sorted(worst["by_pattern"].items(), key=lambda kv: -kv[1]):
            if v:
                print(f"    {v:4d}  {k}")
    print("\n  A FALLING rate can mean the agent improved OR that the user stopped correcting.")
    print("  This instrument cannot tell those apart. The second is the worse outcome.")

    # AGGREGATION CHOICE, and the alternatives it was weighed against (review F6, 2026-08-27
    # -- the first version of this comment argued only against the rejected all-time-worst,
    # which is validating a choice in isolation):
    #   windowed WORST   -- one bad session pins the number; no trend visible. Rejected.
    #   windowed p95     -- robust, but needs more sessions/window than this corpus gives (100
    #                       in 30d) for a stable tail estimate. Rejected for now.
    #   turn-WEIGHTED    -- arguably the most defensible: a 354-message session and a
    #                       6-message session currently count EQUALLY, so the number moves with
    #                       session MIX as well as with behaviour. NOT adopted only because the
    #                       16% baseline recorded on 2026-08-27 is a session-mean; switching
    #                       aggregations silently breaks comparability with it. Revisit
    #                       deliberately, re-baselining at the same time.
    # KNOWN BIAS, stated rather than hidden: session-mean is mix-sensitive per the above.
    #
    # WINDOWED MEAN, not the all-time worst. Reporting rows[0] over the whole corpus returns
    # the same session forever, which is why this check alerted `adverse` on 2/2 runs while
    # carrying no information (heartbeat 2026-08-17, 2026-08-24). A monitor that cannot change
    # its answer cannot show a trend, and a trend is the only thing this file exists to show.
    # F1 (review, 2026-08-27): rows cannot currently hold a None rate -- the `len(msgs) < 5`
    # guard above means total >= 5, so measure() never takes its `else None` branch. That is an
    # IMPLICIT invariant across two distant lines; lower the threshold to 0 and this becomes a
    # TypeError. Filter explicitly rather than rely on the coupling.
    rated = [r for r in rows if r["correction_rate"] is not None]
    mean = sum(r["correction_rate"] for r in rated) / len(rated) if rated else None
    win = f"last {args.since_days}d" if args.since_days else "all time"
    print(f"\n  windowed mean rate ({win}): "
          f"{mean:.0%} over {len(rated)} session(s)" if mean is not None else "  no rows")
    if args.alert_above is None:
        print("  no --alert-above set: NO DEFINED FAILURE CRITERION, so this run cannot fail.")
        return 0
    if is_adverse(mean, args.alert_above):
        print(f"ADVERSE: mean correction rate {mean:.0%} >= threshold {args.alert_above:.0%}")
        return 1
    print(f"  within criterion (< {args.alert_above:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
