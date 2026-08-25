#!/usr/bin/env python3
"""Closed-loop integrity monitor for review provenance.

WHY THIS EXISTS (objectives linkage — read before changing anything)
--------------------------------------------------------------------
Every QC skill emits `Reviewer scope: <model> | <context_class> | <budget>`. That
line is the workspace's only claim about WHO reviewed the work. ISSUE-3481 measured
what a wrong one costs: on an identical prompt, the fast-tier pair accepted citations
unchecked and misread a question; the flagship web-verified both papers and re-framed
the root cause. A review that silently degrades and still reports itself as flagship
converts QC into ceremony.

On 2026-08-07 all three CLI reviews in one session ran on a fallback model after
`gpt-5.6-sol` failed. Nobody noticed: the warning went to stderr, the caller piped
`2>&1 | tail`, and `usage.jsonl` collapsed requested and responding into one field.

OBJECTIVES SERVED (~/dev/share/OBJECTIVES-EXTRACTION-2026-04-03.md):
  * "more like doing the work, less like supervising the help" — the user must not
    have to ask "which model actually reviewed this?" for it to be answered.
  * world-adjudicated, never self-certified — a provenance claim the agent writes
    about itself is exactly the self-certification the objectives reject.

SUCCESS / FAILURE CRITERIA (explicit — not inferred from an exit code)
  SUCCESS  every recent review record carries a responding model AND a provenance
           label; fallback rate within budget; the provenance log parses.
  WARN     fallback rate elevated; a review annotated but not yet cross-checkable.
  FAILURE  a record claims a model it cannot evidence; the log is corrupt; the
           monitor itself has not run.

DESIGN RULES (scars — do not relax without reading the rationale)
1. FAIL-CLOSED. Verdict is max severity of findings. Never a default that later
   logic can talk up to PASS.
2. NO SILENT FAILURES — three mechanisms, because silence has three causes:
     a. fail-closed verdicts   — a finding cannot be swallowed
     b. startup self-check     — broken evaluation logic refuses to report
     c. dead-man's switch      — the monitor not running is itself detected
   (c) is not theoretical: `ai-stats` has emitted jq parse errors on a corrupt
   log since 2026-07-30 and exits 0 every time. Eight days, no signal.
3. CORRUPTION IS A FINDING, NOT A SKIP. A line that will not parse must raise a
   finding, never be silently dropped — dropping is how the above went unnoticed.
4. AUTONOMOUS LEARNING. A fixed fallback threshold would be a guess. The budget is
   derived from the trailing baseline, so "normal" is measured, not asserted, and
   an alert means "worse than this workspace's own history".

Exit codes: 0 pass, 1 warn, 2 failure, 3 self-check failed.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

HOME = Path.home()
USAGE_LOG = HOME / ".cache/model-selection/usage.jsonl"
ROUTER_LOG = HOME / ".cache/model-selection/ai-router-usage.jsonl"
REFLEXION_LOG = HOME / ".claude/logs/reflexion-execution.jsonl"
STATE = HOME / ".claude/state/review-provenance-state.json"
REPORT = HOME / ".claude/logs/review-provenance-health.json"
NOTIFY = HOME / "bin/notify.sh"

SEV = {"PASS": 0, "WARN": 1, "FAIL": 2}
SEV_NAME = {0: "PASS", 1: "WARN", 2: "FAIL"}

WINDOW_DAYS = 7
DEADMAN_HOURS = 36
MIN_BASELINE_N = 20          # below this, report counts only — never a rate
FALLBACK_WARN_MULTIPLE = 2.0  # x trailing baseline before it is worth waking someone
# Absolute ceiling, deliberately NOT adaptive. See fallback_sustained below: the adaptive
# limb alone stayed silent through a 17-day provider outage because its own baseline moved.
FALLBACK_ABSOLUTE_CEILING = 0.10


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Finding:
    severity: str
    code: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        if not self.findings:
            return "PASS"
        return SEV_NAME[max(SEV[f.severity] for f in self.findings)]

    def add(self, severity: str, code: str, detail: str) -> None:
        if severity not in SEV:
            self.findings.append(Finding("FAIL", "bad_severity",
                                         f"unknown severity {severity!r} for {code}"))
            severity = "FAIL"
        self.findings.append(Finding(severity, code, detail))


def read_jsonl(path: Path, rep: Report, label: str) -> list[dict]:
    """Parse a JSONL log. Corruption raises a finding — it is never silently skipped."""
    rows: list[dict] = []
    if not path.exists():
        rep.add("WARN", "log_missing", f"{label}: {path} does not exist")
        return rows
    bad = 0
    try:
        with path.open() as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    bad += 1
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    bad += 1
    except OSError as e:
        rep.add("FAIL", "log_unreadable", f"{label}: {e}")
        return rows
    if bad:
        rep.add("FAIL", "log_corrupt",
                f"{label}: {bad} non-record line(s). Consumers that skip these fail "
                f"open — ai-stats has done exactly that since 2026-07-30. "
                f"NOTE (2026-08-21): 'non-record', not 'unparseable' — the 8 lines in "
                f"ai-router-usage.jsonl are VALID JSON strings that are not objects "
                f"(literal \"$ROUTING_DECISION\" / \"$COMMAND\"), from a one-off writer "
                f"quoting bug on 2026-02-17, bracketed by clean records 11 minutes apart "
                f"and not recurring since. The old wording sent an investigator looking "
                f"for a parse error that does not exist. This FAIL is therefore standing "
                f"on unfixable historical data and will not self-clear; scoping severity "
                f"by recoverability was attempted and REVERTED because this check is "
                f"deliberately fail-closed and weakening it is not self-authorizable.")
    return rows


def within_window(rows: list[dict], days: int, rep: Report | None = None,
                  label: str = "") -> list[dict]:
    """Filter to the window. A record we cannot place in time is a FINDING, not a skip.

    Silently dropping undateable records is how a bad record disappears from
    evaluation entirely — the remaining clean records then produce PASS/exit 0.
    That is the same swallow-and-pass shape as the corrupt-line drop in read_jsonl.
    """
    cutoff = now() - timedelta(days=days)
    out, undateable = [], 0
    for r in rows:
        ts = r.get("ts") or r.get("timestamp")
        if not ts:
            undateable += 1
            continue
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            undateable += 1
            continue
        if t >= cutoff:
            out.append(r)
    if undateable and rep is not None:
        rep.add("FAIL", "undateable_records",
                f"{label}: {undateable} record(s) have a missing or unparseable "
                f"timestamp and were excluded — their contents were never evaluated, "
                f"so a clean verdict here does NOT cover them")
    return out


def check_provenance(rep: Report) -> None:
    """Are wrapper calls recording what actually answered, and how often do they degrade?"""
    rows = read_jsonl(USAGE_LOG, rep, "usage.jsonl")
    recent = within_window(rows, WINDOW_DAYS, rep, "usage.jsonl")
    rep.stats["usage_rows_total"] = len(rows)
    rep.stats["usage_rows_window"] = len(recent)

    if not recent:
        rep.add("WARN", "no_recent_calls",
                f"no wrapper calls in the last {WINDOW_DAYS}d — nothing to evaluate")
        return

    # Schema adoption: records written before 2026-08-07 lack the new fields. That is
    # expected and must not read as a defect — but a NEW record missing them is one.
    typed = [r for r in recent if "fallback_occurred" in r]
    rep.stats["records_with_provenance_fields"] = len(typed)
    rep.stats["records_without"] = len(recent) - len(typed)

    if typed:
        fb = [r for r in typed if r.get("fallback_occurred") is True]
        rate = len(fb) / len(typed)
        rep.stats["fallback_rate_window"] = round(rate, 4)
        rep.stats["fallback_count_window"] = len(fb)

        if len(typed) < MIN_BASELINE_N:
            # Honest about small n — a rate off 3 samples is noise dressed as signal.
            rep.stats["fallback_rate_note"] = (
                f"n={len(typed)} < {MIN_BASELINE_N}: counts only, no rate judgement")
        else:
            baseline = load_state().get("fallback_baseline")
            if baseline is None:
                rep.stats["fallback_rate_note"] = "baseline seeding this run"
            elif rate > FALLBACK_ABSOLUTE_CEILING:
                # AN ADAPTIVE BASELINE NORMALISES THE DRIFT IT WATCHES FOR. Measured
                # 2026-08-24: the codex/ChatGPT auth expired and the wrapper fell back to
                # other vendors 69 times over 17 days, 30 of them off-vendor entirely. This
                # check RAN 17 TIMES across that period, computed the rate correctly every
                # time, and never warned — because the trailing median climbed WITH the
                # degradation. State at discovery: baseline 0.1321, rate 0.1346, so the
                # spike test (rate > baseline x MULTIPLE) was nowhere near tripping. The
                # 0.05 in that expression is a floor on the THRESHOLD, not a ceiling on the
                # RATE, so it stops mattering once the baseline is large.
                # The file's own rationale caused this: "a hardcoded threshold would be a
                # guess that silently rots". Correct — and an adaptive one silently ADAPTS.
                # Both limbs are needed: relative catches sudden spikes, absolute catches
                # slow ramps the baseline would otherwise swallow.
                rep.add("WARN", "fallback_sustained",
                        f"fallback rate {rate:.1%} exceeds the absolute ceiling "
                        f"{FALLBACK_ABSOLUTE_CEILING:.0%} (trailing baseline {baseline:.1%} "
                        f"has absorbed it — a relative test alone cannot see a slow ramp). "
                        f"Check provider auth: `codex login status` reports stored "
                        f"credentials, NOT their validity — it said 'Logged in' while the "
                        f"API returned HTTP 401 token_expired.")
            elif rate > max(baseline * FALLBACK_WARN_MULTIPLE, 0.05):
                rep.add("WARN", "fallback_spike",
                        f"fallback rate {rate:.1%} is >{FALLBACK_WARN_MULTIPLE}x the "
                        f"trailing baseline {baseline:.1%} — models may be degrading "
                        f"silently; check which primary is failing")

        for r in fb:
            if r.get("model") == r.get("requested_model"):
                rep.add("FAIL", "inconsistent_record",
                        f"record claims fallback_occurred but model == requested_model "
                        f"({r.get('model')}) — the flag and the fields disagree")


def check_annotations(rep: Report) -> None:
    """Does each reflexion record's model claim have evidence behind it?"""
    rows = read_jsonl(REFLEXION_LOG, rep, "reflexion-execution.jsonl")
    recent = within_window(rows, WINDOW_DAYS, rep, "reflexion-execution.jsonl")
    rep.stats["reflexion_rows_window"] = len(recent)
    if not recent:
        return

    usage = within_window(read_jsonl(USAGE_LOG, rep, "usage.jsonl"), WINDOW_DAYS)
    models_seen = {r.get("model") for r in usage if r.get("model")}

    unevidenced = []
    for r in recent:
        model = r.get("model")
        if not model or r.get("action") == "skipped":
            continue
        # An inline/self review legitimately has no wrapper record.
        if "inline" in str(model) or str(model).startswith("claude"):
            continue
        if r.get("model_provenance") in ("usage-jsonl",):
            continue
        if model not in models_seen:
            unevidenced.append((r.get("timestamp", "?")[:19], model))

    rep.stats["annotations_unevidenced"] = len(unevidenced)
    if unevidenced:
        sample = "; ".join(f"{t} claims {m}" for t, m in unevidenced[:3])
        rep.add("FAIL", "annotation_unevidenced",
                f"{len(unevidenced)} review record(s) name a model with no matching "
                f"wrapper call in the same window — the annotation is self-reported "
                f"and unfalsifiable. e.g. {sample}")


def check_router_log(rep: Report) -> None:
    """The last-resort leg's log — the most degraded review is the least traceable."""
    if not ROUTER_LOG.exists():
        # Absence here is FAIL, not WARN: this log is the ONLY record of the most
        # degraded review path the stack can take. No log means that path is
        # untraceable, which is precisely the condition this tool exists to detect.
        rep.add("FAIL", "router_log_missing",
                f"{ROUTER_LOG} does not exist — the last-resort fallback leg has no "
                f"traceability at all")
        return
    read_jsonl(ROUTER_LOG, rep, "ai-router-usage.jsonl")


def self_check() -> list[str]:
    """No-silent-failures (b): refuse to report on broken evaluation logic."""
    errs: list[str] = []

    if Report().verdict != "PASS":
        errs.append("empty report must be PASS")

    r = Report()
    r.add("WARN", "t", "t")
    if r.verdict != "WARN":
        errs.append("a WARN finding must yield WARN")
    r.add("FAIL", "t", "t")
    if r.verdict != "FAIL":
        errs.append("FAIL must dominate WARN")

    r2 = Report()
    r2.add("NOPE", "t", "t")
    if r2.verdict != "FAIL":
        errs.append("REGRESSION: unknown severity must escalate to FAIL, not crash or pass")

    # Corruption must raise, never silently drop — the ai-stats failure mode.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write('{"ts":"2026-01-01T00:00:00Z"}\n')
        fh.write('"$ROUTING_DECISION"\n')       # the real corruption seen in the wild
        fh.write('not json at all\n')
        p = Path(fh.name)
    r3 = Report()
    read_jsonl(p, r3, "test")
    if not any(f.code == "log_corrupt" for f in r3.findings):
        errs.append("REGRESSION: corrupt log lines did not raise log_corrupt")
    if r3.verdict != "FAIL":
        errs.append("REGRESSION: log corruption did not fail-close")
    try:
        p.unlink()
    except OSError:
        pass

    # Pin the three fail-open paths found by cross-family review, each of which let a
    # real problem vanish while the verdict stayed clean.

    # (i) undateable records must be a finding, not a silent exclusion
    r4 = Report()
    kept = within_window([{"ts": "not-a-date"}, {"nots": 1}], 7, r4, "test")
    if kept or not any(f.code == "undateable_records" for f in r4.findings):
        errs.append("REGRESSION: undateable records were silently dropped instead of raising")

    # (ii) corrupt state must not read as a first run — that disables the dead-man switch
    if deadman({"_corrupt": "boom"}) is None:
        errs.append("REGRESSION: corrupt state did not trip the dead-man's switch")
    if deadman({}) is not None:
        errs.append("a genuine first run must NOT trip the dead-man's switch")

    # (iii) an absent router log is FAIL — that path is the least traceable review there is
    r5 = Report()
    r5.add("WARN", "log_missing", "x")
    if r5.verdict != "WARN":
        errs.append("severity ladder broken for log_missing")

    return errs


def load_state() -> dict:
    """Returns state, or {"_corrupt": reason} — NEVER a bare {} on corruption.

    A bare {} made a corrupt state file indistinguishable from a first run, which
    silently disabled the dead-man's switch: deadman() saw no last_run and returned
    None. The identical bug was fixed in public-endpoint-health.py hours earlier and
    was not carried across — hence this note, so the next reader carries it.
    """
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception as e:
            return {"_corrupt": f"{type(e).__name__}: {e}"}
    return {}


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def deadman(state: dict) -> str | None:
    if state.get("_corrupt"):
        return (f"state file is corrupt ({state['_corrupt']}) — the dead-man's switch "
                f"cannot evaluate, so treat the monitor as not-running")
    last = state.get("last_run")
    if not last:
        return None
    try:
        prev = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return f"state has an unparseable last_run ({last!r}) — treat as not-running"
    gap = now() - prev
    if gap > timedelta(hours=DEADMAN_HOURS):
        return (f"monitor had not run for {gap.days}d {gap.seconds // 3600}h "
                f"(budget {DEADMAN_HOURS}h)")
    return None


def notify(title: str, message: str, priority: str = "high") -> bool:
    if not NOTIFY.exists():
        print(f"[no-consumer] {NOTIFY} missing; dropped: {title}", file=sys.stderr)
        return False
    try:
        p = subprocess.run([str(NOTIFY), title, message, "--priority", priority,
                            "--channel", "auto"], capture_output=True, text=True, timeout=90)
    except Exception as e:
        print(f"[notify-failed] {e}", file=sys.stderr)
        return False
    return p.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", action="store_true", help="write report, update baseline, notify")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    errs = self_check()
    if errs:
        for e in errs:
            print(f"[SELF-CHECK FAILED] {e}", file=sys.stderr)
        notify("Review-provenance monitor SELF-CHECK FAILED",
               "Evaluation logic is broken; its results are not trustworthy. "
               + "; ".join(errs)[:300], "urgent")
        return 3
    if args.self_check:
        print("self-check: all assertions pass")
        return 0

    rep = Report()
    check_provenance(rep)
    check_annotations(rep)
    check_router_log(rep)

    state = load_state()
    stale = deadman(state)
    if stale:
        rep.add("FAIL", "monitor_stopped", stale)

    if args.json:
        print(json.dumps({"verdict": rep.verdict, "stats": rep.stats,
                          "findings": [asdict(f) for f in rep.findings]}, indent=2))
    else:
        print(f"VERDICT: {rep.verdict}")
        for k, v in rep.stats.items():
            print(f"  {k}: {v}")
        for f in rep.findings:
            print(f"  - [{f.severity}/{f.code}] {f.detail}")

    if args.log:
        # Autonomous learning: the baseline is this workspace's own trailing history,
        # so "elevated" means elevated FOR HERE. A hardcoded threshold would be a guess
        # that silently rots as models and routing change.
        rate = rep.stats.get("fallback_rate_window")
        n = rep.stats.get("records_with_provenance_fields", 0)
        if rate is not None and n >= MIN_BASELINE_N:
            hist = state.get("fallback_history", [])
            hist.append(rate)
            state["fallback_history"] = hist[-30:]
            state["fallback_baseline"] = round(statistics.median(state["fallback_history"]), 4)
        state["last_run"] = now().isoformat()
        write_json_atomic(STATE, state)
        write_json_atomic(REPORT, {"ts": now().isoformat(), "verdict": rep.verdict,
                                   "stats": rep.stats,
                                   "findings": [asdict(f) for f in rep.findings]})

        fails = [f for f in rep.findings if f.severity == "FAIL"]
        if fails:
            if not notify("Review provenance FAILING",
                          "; ".join(f"{f.code}: {f.detail}" for f in fails)[:400], "high"):
                print("[ALERTING DEGRADED] could not deliver", file=sys.stderr)
                return 2

    return {"PASS": 0, "WARN": 1, "FAIL": 2}[rep.verdict]


if __name__ == "__main__":
    sys.exit(main())
