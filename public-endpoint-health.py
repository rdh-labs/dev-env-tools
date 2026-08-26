#!/usr/bin/env python3
"""Closed-loop health monitor for stakeholder-facing web endpoints.

WHY THIS EXISTS (objectives linkage — read before changing anything)
--------------------------------------------------------------------
Every other *-health-check in this workspace answers "does my tooling work?".
None answered the only question a client cares about: "can the person I sent
the link to actually open it?"

That gap had a measured cost. On 2026-08-06 a client review was scheduled
against links that had never been opened by anything but an authenticated
session. `mouraquayle.ca` — a real client's custom domain — was sitting behind
a 401 auth wall, and `mq-studio-dev` was returning HTTP 500. Both deployments
were "healthy" by every inward-facing signal available.

OBJECTIVES SERVED (~/dev/share/OBJECTIVES-EXTRACTION-2026-04-03.md):
  * "more like doing the work, less like supervising the help" — the user must
    not have to hand-open every link before every client meeting.
  * world-adjudicated value, never self-certified — a deliverable its audience
    cannot open has not reached the world, whatever the deploy status says.

SUCCESS / FAILURE CRITERIA (explicit, per endpoint, declared in the manifest):
  SUCCESS  expected status + required content present + every same-origin link
           resolves.
  WARN     over the latency budget, or a draft/placeholder marker is shown to
           the audience. Raise a marker to FAIL per endpoint with
           "draft_marker_severity": "FAIL" in the manifest.
  FAILURE  any SUCCESS criterion unmet, OR zero endpoints were checked, OR the
           monitor itself is not running.
  None of these is inferred from an exit code — see DESIGN RULE 1.

DESIGN RULES (each is a scar; do not relax without reading the rationale)
------------------------------------------------------------------------
1. FAIL-CLOSED. Verdict is the MAX severity of accumulated findings, never a
   default that later logic can talk down. v1 of this file defaulted to FAIL
   and had a branch that raised it back to PASS — so a page flagged with a
   DRAFT marker printed "OK" and exited 0. The detector detected the problem
   and then reported success. A monitor that fails open is worse than none: it
   manufactures false trust. `self_check()` now asserts that exact regression.
2. NO GUESSED ALIASES. Resolve real production domains from the platform API.
   A 404 on a guessed <project>.vercel.app is not evidence a site is down.
3. UNAUTHENTICATED FETCH. Check as an external stakeholder, holding no creds.
4. 401/403 IS A FAILURE. An auth wall means the deployment is fine and the
   client still sees nothing. Deployment health is not the objective.
5. NO SILENT FAILURES — three mechanisms, because silence has three causes:
     a. fail-closed verdicts   — the check cannot swallow its own finding
     b. startup self-check     — broken evaluation logic refuses to report
     c. dead-man's switch      — the monitor not running is itself detected
   (c) exists because it actually happened: an edit to this file introduced a
   NameError that crashed every run, while the surrounding shell pipeline still
   reported exit 0. Nothing would have noticed.
6. CLOSED LOOP. Detection alone changes nothing — this workspace has measured
   advisories firing thousands of times to no effect. So state persists across
   runs: new failures notify, unresolved failures escalate harder, recoveries
   are announced, instability is surfaced even when currently green, and safe
   remediations apply automatically while outward-facing ones are proposed with
   an explicit authorization step.

Exit codes: 0 all pass, 1 warnings, 2 failure(s), 3 self-check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

HOME = Path.home()
MANIFEST = HOME / "dev/infrastructure/tools/public-endpoints.json"
STATE = HOME / ".claude/state/public-endpoint-health-state.json"
HISTORY = HOME / ".claude/logs/public-endpoint-health-history.jsonl"
REPORT = HOME / ".claude/logs/public-endpoint-health.json"
NOTIFY = HOME / "bin/notify.sh"

TIMEOUT = 25
UA = "public-endpoint-health/2.0 (+workspace monitoring)"

SEV = {"PASS": 0, "WARN": 1, "FAIL": 2}
SEV_NAME = {0: "PASS", 1: "WARN", 2: "FAIL"}

DRAFT_MARKERS = re.compile(r"\b(DRAFT|TODO|FIXME|PLACEHOLDER|Lorem ipsum)\b", re.I)

ESCALATE_AFTER = 3         # consecutive failed runs before the alert escalates
DEADMAN_HOURS = 36         # silence longer than this means the monitor itself died
MAX_BODY_BYTES = 8 << 20   # cap a single response so one huge page cannot hang a run
MAX_LINKS_PER_PAGE = 200   # cap link-following; truncation is REPORTED, never silent
ENDPOINT_WORKERS = 4       # endpoints are independent; serial runs risk scheduler timeout
LINK_WORKERS = 8           # links within one page, likewise
HISTORY_MAX_LINES = 20_000 # bound the append-only history so --uptime stays cheap


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Finding:
    severity: str      # PASS | WARN | FAIL
    code: str          # stable machine code — remediation routes on this
    detail: str


@dataclass
class Result:
    url: str
    label: str = ""
    objective: str = ""
    status: int | None = None
    size: int = 0
    latency_ms: int = 0
    findings: list[Finding] = field(default_factory=list)
    links_checked: int = 0
    links_broken: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Max severity observed. Fail-closed — nothing can lower this."""
        if not self.findings:
            return "PASS"
        return SEV_NAME[max(SEV[f.severity] for f in self.findings)]

    def add(self, severity: str, code: str, detail: str) -> None:
        # A manifest typo like "ERROR" must not crash verdict computation — and
        # must not silently downgrade either. Unknown severity escalates to FAIL.
        if severity not in SEV:
            self.findings.append(Finding(
                "FAIL", "bad_severity",
                f"unknown severity {severity!r} for {code} — treating as FAIL"))
            severity = "FAIL"
        self.findings.append(Finding(severity, code, detail))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict
        return d


class LinkGrabber(HTMLParser):
    """Collects hrefs, bounded. Stops storing past the cap so an 8MiB page
    cannot accumulate an unbounded list before the request cap is applied."""

    def __init__(self, limit: int = MAX_LINKS_PER_PAGE) -> None:
        super().__init__()
        self.links: list[str] = []
        self.limit = limit
        self.truncated = False

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for k, v in attrs:
            if k == "href" and v and not v.startswith(("#", "mailto:", "tel:", "javascript:")):
                if len(self.links) >= self.limit:
                    self.truncated = True
                    return
                self.links.append(v)


def fetch(url: str) -> tuple[int | None, bytes, str, int]:
    """Unauthenticated fetch, as an external stakeholder. -> (status, body, err, ms)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            # Bounded read: an unbounded r.read() lets one huge or streaming
            # response hang the whole run, which would trip the dead-man switch
            # for a reason that has nothing to do with endpoint health.
            body = r.read(MAX_BODY_BYTES)
            return r.status, body, "", int((time.monotonic() - t0) * 1000)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(MAX_BODY_BYTES)   # same cap as the success path
        except Exception:
            body = b""
        return e.code, body, "", int((time.monotonic() - t0) * 1000)
    except Exception as e:
        return None, b"", f"{type(e).__name__}: {e}", int((time.monotonic() - t0) * 1000)


def check(ep: dict) -> Result:
    """Evaluate one endpoint against its declared success criteria."""
    url = ep["url"]
    res = Result(url=url, label=ep.get("label") or url, objective=ep.get("objective", ""))

    expect_status = ep.get("expect_status", 200)
    require_text = ep.get("require_text")
    forbid_draft = ep.get("forbid_draft_markers", True)
    max_latency = ep.get("max_latency_ms", 8000)
    min_size = ep.get("min_size_bytes", 400)

    status, body, err, ms = fetch(url)
    res.status, res.size, res.latency_ms = status, len(body), ms

    if err:
        res.add("FAIL", "unreachable", f"unreachable — {err}")
        return res

    if status in (401, 403):
        res.add("FAIL", "auth_wall",
                f"HTTP {status} — auth wall. The deployment may be healthy; an "
                f"external stakeholder still sees nothing.")
        return res

    if status != expect_status:
        res.add("FAIL", "bad_status", f"HTTP {status} (expected {expect_status})")
        return res

    text = body.decode("utf-8", "replace")

    if re.search(r"<title[^>]*>\s*(4\d\d|5\d\d)\b", text, re.I):
        res.add("FAIL", "error_body", "HTTP 200 but the page body is an error page")
        return res

    if res.size < min_size:
        res.add("FAIL", "stub_response",
                f"response is only {res.size}b (min {min_size}b) — likely a stub")

    if require_text and require_text not in text:
        res.add("FAIL", "missing_content",
                f"required content {require_text!r} not found — the page may have "
                f"silently regressed to a placeholder")

    if ms > max_latency:
        res.add("WARN", "slow", f"{ms}ms exceeds the {max_latency}ms budget")

    if forbid_draft:
        markers = sorted({m.upper() for m in DRAFT_MARKERS.findall(text)})
        if markers:
            res.add(ep.get("draft_marker_severity", "WARN"), "draft_marker",
                    f"client-facing page shows {', '.join(markers)} marker(s) to its audience")

    if ep.get("follow_links"):
        g = LinkGrabber()
        try:
            g.feed(text)
        except Exception as e:
            # Never swallow this: a parser failure means we checked FEWER links
            # than we think, which reads identically to "all links fine".
            res.add("FAIL", "link_parse_failed",
                    f"could not parse links ({type(e).__name__}) — link coverage "
                    f"is unknown, so this page is NOT verified")
        origin = urlparse(url)
        targets: dict[str, str] = {}      # absolute url -> original href, deduped
        for href in g.links:
            target = urljoin(url, href)
            if urlparse(target).netloc == origin.netloc and target not in targets:
                targets[target] = href
        if g.truncated:
            # No silent caps: truncation is itself a finding.
            res.add("WARN", "links_truncated",
                    f"stopped collecting after {MAX_LINKS_PER_PAGE} links — the rest "
                    f"of this page's links are UNCHECKED, not known-good")

        # Links are independent; serially they dominate runtime and can push a
        # run past the scheduler's patience, which would surface as a dead-man
        # trip rather than as the endpoint problem it is not.
        with ThreadPoolExecutor(max_workers=LINK_WORKERS) as pool:
            for (target, href), (st, _, e2, _) in zip(
                    targets.items(), pool.map(fetch, targets)):
                res.links_checked += 1
                if e2 or st is None or st >= 400:
                    res.links_broken.append(f"{href} -> {st or e2}")
        if res.links_broken:
            res.add("FAIL", "broken_links",
                    f"{len(res.links_broken)} broken link(s) — a hub whose links do "
                    f"not resolve is a broken hub")

    return res


def self_check() -> list[str]:
    """No-silent-failures (b). Refuse to report on broken evaluation logic."""
    errs: list[str] = []

    if Result(url="x").verdict != "PASS":
        errs.append("a result with no findings must be PASS")

    r = Result(url="x")
    r.add("WARN", "t", "t")
    if r.verdict != "WARN":
        errs.append("a WARN finding must yield WARN")
    r.add("FAIL", "t", "t")
    if r.verdict != "FAIL":
        errs.append("FAIL must dominate WARN")

    # The exact v1 regression, pinned so it cannot come back.
    r2 = Result(url="x")
    r2.add("WARN", "draft_marker", "contains DRAFT")
    if r2.verdict != "WARN":
        errs.append(f"REGRESSION: a lone draft marker reported {r2.verdict}, "
                    f"expected WARN (this is the v1 fail-open bug)")

    if not DRAFT_MARKERS.search("this is a DRAFT document"):
        errs.append("DRAFT_MARKERS failed to match a real marker")
    if not DRAFT_MARKERS.search("placeholder text"):
        errs.append("DRAFT_MARKERS is case-sensitive; it must not be")
    if DRAFT_MARKERS.search("redrafted"):
        errs.append("DRAFT_MARKERS matched a substring it should not")

    # Pin the two CRITICAL fail-open paths found in cross-family review:
    # a missing or empty manifest must raise, never yield an empty list that
    # the exit-code logic then reports as success.
    import tempfile
    global MANIFEST
    saved = MANIFEST
    try:
        MANIFEST = Path(tempfile.gettempdir()) / "peh-definitely-not-here.json"
        try:
            load_manifest()
            errs.append("REGRESSION: a missing manifest did not raise — "
                        "zero endpoints would be reported as success")
        except ManifestError:
            pass
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"endpoints": []}')
            MANIFEST = Path(fh.name)
        try:
            load_manifest()
            errs.append("REGRESSION: an empty manifest did not raise — "
                        "zero endpoints would be reported as success")
        except ManifestError:
            pass
        try:
            MANIFEST.unlink()
        except OSError:
            pass
    finally:
        MANIFEST = saved

    return errs


def write_json_atomic(path: Path, payload: dict) -> None:
    """Temp-file + replace, so a crash mid-write cannot leave truncated state
    that the dead-man's switch would then read as 'unparseable, monitor down'.

    Deliberately local: tools/governance_file_editor.py has an atomic_write, but
    it raises GovernanceFileError and takes an fcntl lock — coupling a monitor to
    the governance editor to save three lines is the worse trade.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"endpoints": {}, "last_run": None}


def save_state(state: dict) -> None:
    write_json_atomic(STATE, state)


def append_history(results: list[Result]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    ts = now().isoformat()
    with HISTORY.open("a") as f:
        for r in results:
            f.write(json.dumps({
                "ts": ts, "label": r.label, "url": r.url, "verdict": r.verdict,
                "status": r.status, "latency_ms": r.latency_ms,
                "codes": [x.code for x in r.findings],
            }) + "\n")
    # Bound the append-only log. Unbounded growth would eventually make --uptime
    # slow and the disk footprint unexplained; trimming is announced, not silent.
    try:
        lines = HISTORY.read_text().splitlines()
        if len(lines) > HISTORY_MAX_LINES:
            keep = lines[-HISTORY_MAX_LINES:]
            HISTORY.write_text("\n".join(keep) + "\n")
            print(f"[history] trimmed {len(lines) - len(keep)} oldest entries "
                  f"(cap {HISTORY_MAX_LINES})", file=sys.stderr)
    except OSError:
        pass


def notify(title: str, message: str, priority: str = "high") -> bool:
    """Send, and report whether the send itself succeeded. Never assume."""
    if not NOTIFY.exists():
        print(f"[no-consumer] {NOTIFY} missing; dropped: {title}", file=sys.stderr)
        return False
    try:
        p = subprocess.run([str(NOTIFY), title, message, "--priority", priority,
                            "--channel", "auto"], capture_output=True, text=True, timeout=90)
    except Exception as e:
        print(f"[notify-failed] {e}", file=sys.stderr)
        return False
    if p.returncode != 0:
        print(f"[notify-failed] rc={p.returncode} {p.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return True


def evaluate_loop(results: list[Result], state: dict) -> dict:
    """Classify against prior runs: new / chronic / recovered / flapping."""
    eps = state.setdefault("endpoints", {})
    events: dict[str, list[Result]] = {"new_failures": [], "escalations": [],
                                       "recoveries": [], "flapping": []}

    for r in results:
        st = eps.setdefault(r.label, {"consecutive_failures": 0, "total_runs": 0,
                                      "total_failures": 0, "transitions": 0,
                                      "last_verdict": None, "first_failed_at": None})
        prev = st["last_verdict"]
        st["total_runs"] += 1

        if r.verdict == "FAIL":
            st["total_failures"] += 1
            st["consecutive_failures"] += 1
            if prev != "FAIL":
                st["first_failed_at"] = now().isoformat()
                st["transitions"] += 1
                events["new_failures"].append(r)
            elif (st["consecutive_failures"] >= ESCALATE_AFTER
                  and st["consecutive_failures"] % ESCALATE_AFTER == 0):
                # RE-escalate, not escalate-once. `== ESCALATE_AFTER` fired a single
                # alert at run 3 and then went permanently silent — so an endpoint
                # down forever produced zero alerts from run 4 onward, which is the
                # exact failure this tool exists to prevent. Found live 2026-08-08:
                # three client-facing endpoints sat at consecutive_failures=5 with
                # escalations=[] for 39 hours. Testing equality against a threshold
                # checks the SHAPE of a transition; a persistent outage is a STATE.
                # The cadence reuses ESCALATE_AFTER (3, 6, 9, …) rather than
                # introducing a second, invented interval constant.
                events["escalations"].append(r)
        else:
            if prev == "FAIL":
                st["transitions"] += 1
                events["recoveries"].append(r)
            st["consecutive_failures"] = 0
            st["first_failed_at"] = None

        # An endpoint that keeps flipping is unstable even while green right
        # now — invisible to any single run, which is why it is tracked here.
        # EDGE, not level. `transitions` never decays, so the bare level test
        # `transitions >= 4` fired a FLAPPING alert on EVERY subsequent run for
        # the life of the endpoint — measured: 12 consecutive alerts after 8
        # clean PASS runs. That trains dismissal of the same channel escalations
        # are delivered on, so it actively degrades the fix above it.
        # Re-notify only after ESCALATE_AFTER further transitions (no second
        # invented constant), and stay silent while the endpoint is stable.
        if st["total_runs"] >= 6 and st["transitions"] >= 4:
            last_at = st.get("flapping_notified_at_transitions")
            if last_at is None or st["transitions"] - last_at >= ESCALATE_AFTER:
                events["flapping"].append(r)
                st["flapping_notified_at_transitions"] = st["transitions"]

        st["last_verdict"] = r.verdict

    state["last_run"] = now().isoformat()
    return events


def deadman_check(state: dict) -> str | None:
    """No-silent-failures (c): has the monitor itself stopped running?"""
    last = state.get("last_run")
    if not last:
        return None
    try:
        prev = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        # Corrupt state must not disable the switch. "I cannot tell whether the
        # monitor is running" is a failure, not a pass.
        return (f"state file has an unparseable last_run ({last!r}) — the "
                f"dead-man's switch cannot evaluate; treat as not-running")
    gap = now() - prev
    if gap > timedelta(hours=DEADMAN_HOURS):
        return (f"monitor had not completed a run for {gap.days}d "
                f"{gap.seconds // 3600}h (budget {DEADMAN_HOURS}h) — the "
                f"schedule or the script may be broken")
    return None


def remediate(results: list[Result], apply: bool) -> list[str]:
    """Self-healing, split by blast radius.

    AUTO      reversible, no outward-facing effect, cannot surprise a third party.
    PROPOSE   changes what the outside world sees — printed with the exact command
              and left for explicit authorization. Auto-flipping a client site's
              protection is not a monitor's call to make.
    """
    actions: list[str] = []
    for r in results:
        for f in r.findings:
            if f.code == "auth_wall":
                actions.append(
                    f"PROPOSE  {r.label}: drop the auth wall so the audience can see it.\n"
                    f"           Vercel -> Settings -> Deployment Protection -> disable.\n"
                    f"           (outward-facing: requires explicit authorization)")
            elif f.code in ("bad_status", "error_body", "unreachable"):
                actions.append(
                    f"PROPOSE  {r.label}: server-side failure ({f.code}). Check build logs;\n"
                    f"           rollback to the last READY production deploy is usually the\n"
                    f"           fastest restore. (outward-facing: requires authorization)")
            elif f.code == "draft_marker":
                actions.append(
                    f"REVIEW   {r.label}: shows a draft marker to its audience. Finalise the\n"
                    f"           document, or set forbid_draft_markers=false in the manifest\n"
                    f"           with a note that this audience expects a draft.")
            elif f.code == "broken_links":
                actions.append(
                    f"REVIEW   {r.label}: dead link(s): {', '.join(r.links_broken[:3])}")
            elif f.code == "missing_content":
                actions.append(
                    f"REVIEW   {r.label}: required content vanished — likely a bad deploy.")
    if apply and actions:
        print("[remediate] no AUTO-class remediation is currently defined: every known\n"
              "            failure mode here is outward-facing and needs authorization.",
              file=sys.stderr)
    return actions


def uptime_report() -> str:
    """Automated evaluation — what the history says, not what today says."""
    agg: dict[str, dict] = {}
    try:
        fh = HISTORY.open()
    except OSError:
        return "no history yet"
    with fh:
        for line in fh:                       # stream; never load the whole file
            try:
                d = json.loads(line)
            except Exception:
                continue
            a = agg.setdefault(d["label"], {"runs": 0, "fails": 0})
            a["runs"] += 1
            a["fails"] += 1 if d["verdict"] == "FAIL" else 0
    out = []
    for label, a in sorted(agg.items()):
        pct = 100.0 * (a["runs"] - a["fails"]) / a["runs"] if a["runs"] else 0.0
        out.append(f"  {label:<44} {pct:5.1f}% healthy over {a['runs']} run(s)")
    return "\n".join(out) or "no history yet"


class ManifestError(RuntimeError):
    """Raised when the endpoint list cannot be loaded or is empty.

    This is deliberately an exception rather than an empty list. Returning []
    made 'the manifest is missing' indistinguishable from 'everything passed' —
    the monitor exited 0 having checked nothing. Checking nothing is a failure.
    """


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        raise ManifestError(f"no manifest at {MANIFEST} — zero endpoints would be checked")
    try:
        eps = json.loads(MANIFEST.read_text()).get("endpoints", [])
    except Exception as e:
        raise ManifestError(f"manifest at {MANIFEST} is unreadable: {e}") from e
    if not eps:
        raise ManifestError(f"manifest at {MANIFEST} declares zero endpoints")
    return eps


def vercel_domains() -> dict[str, list[str]]:
    """Resolve REAL production domains. Never guess <project>.vercel.app."""
    token = os.environ.get("VERCEL_TOKEN")
    ref = os.environ.get("VERCEL_TOKEN_OP_REF")
    if not token and ref:
        try:
            token = subprocess.run(["op", "read", ref], capture_output=True,
                                   text=True, timeout=60, check=True).stdout.strip()
        except Exception as e:
            print(f"[warn] could not read token from {ref}: {e}", file=sys.stderr)
    if not token:
        print("[warn] set VERCEL_TOKEN or VERCEL_TOKEN_OP_REF for --discover", file=sys.stderr)
        return {}
    team = os.environ.get("VERCEL_TEAM_ID", "")
    out: dict[str, list[str]] = {}

    def api(path: str):
        req = urllib.request.Request(f"https://api.vercel.com{path}",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)

    tq = f"teamId={team}&" if team else ""
    try:
        projects = api(f"/v9/projects?{tq}limit=100").get("projects", [])
    except Exception as e:
        print(f"[warn] project list failed: {e}", file=sys.stderr)
        return {}
    for p in projects:
        name = p.get("name")
        try:
            doms = api(f"/v9/projects/{quote(str(name), safe='')}"
                       f"/domains?{tq}limit=50").get("domains", [])
            out[name] = [d["name"] for d in doms]
        except Exception as e:
            # Do NOT record this as "no domains" — an auth/rate-limit failure
            # would then be indistinguishable from a project that genuinely has
            # none, and discovery would quietly under-report.
            out[name] = [f"ERROR: {type(e).__name__}: {e}"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--url", help="ad-hoc single URL")
    ap.add_argument("--links", action="store_true", help="follow links (with --url)")
    ap.add_argument("--discover", action="store_true", help="list real Vercel prod domains")
    ap.add_argument("--log", action="store_true", help="write report, track state, notify")
    ap.add_argument("--apply-remediation", action="store_true",
                    help="apply AUTO-class remediation (outward-facing is never auto)")
    ap.add_argument("--uptime", action="store_true", help="historical health")
    ap.add_argument("--self-check", action="store_true", help="run assertions and exit")
    args = ap.parse_args()

    errs = self_check()
    if errs:
        for e in errs:
            print(f"[SELF-CHECK FAILED] {e}", file=sys.stderr)
        notify("Endpoint monitor SELF-CHECK FAILED",
               "public-endpoint-health.py evaluation logic is broken; its results "
               "are not trustworthy. " + "; ".join(errs)[:300], "urgent")
        # DEC-334: 2 = CANNOT-ASSESS ("it tried to look and failed"), not 3 =
        # NOTHING-TO-ASSESS ("ran fine, nothing to measure"). A self-check failure is
        # the former: it looked, and its answer cannot be believed. Harmless while this
        # runs directly from the schedule, but scheduled-check-runner.sh is now SILENT
        # on rc=3, so wrapping this script later would mute the alert without anyone
        # editing this file. Fixing the contract, not relying on the invocation path.
        return 2
    if args.self_check:
        print("self-check: all assertions pass")
        return 0

    if args.discover:
        found = vercel_domains()
        if not found:
            print("[DISCOVERY FAILED] could not list projects — this is not the "
                  "same as 'there are no projects'", file=sys.stderr)
            return 2
        errs_seen = False
        for name, doms in sorted(found.items()):
            if any(d.startswith("ERROR:") for d in doms):
                errs_seen = True
            print(f"{name}: {', '.join(doms) if doms else '(no domains)'}")
        return 2 if errs_seen else 0

    if args.uptime:
        print(uptime_report())
        return 0

    if args.url:
        results = [check({"url": args.url, "follow_links": args.links})]
    else:
        try:
            eps = load_manifest()
            # Endpoints are independent. Serially, 8 endpoints x TIMEOUT is a
            # multi-minute worst case; pool.map preserves manifest order.
            with ThreadPoolExecutor(max_workers=ENDPOINT_WORKERS) as pool:
                results = list(pool.map(check, eps))
        except ManifestError as e:
            print(f"[MANIFEST FAILURE] {e}", file=sys.stderr)
            notify("Endpoint monitor CANNOT RUN", str(e), "urgent")
            return 2

    # Belt and braces for the same fail-open class: whatever the route in,
    # zero results can never be reported as success.
    if not results:
        print("[NO ENDPOINTS CHECKED] refusing to report success", file=sys.stderr)
        notify("Endpoint monitor checked NOTHING",
               "The run produced zero results. Success cannot be inferred from "
               "an absence of failures when nothing was examined.", "urgent")
        return 2

    state = load_state()
    stale = deadman_check(state)
    events = evaluate_loop(results, state)

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        width = max((len(r.label) for r in results), default=10)
        for r in results:
            icon = {"PASS": "OK  ", "WARN": "WARN", "FAIL": "FAIL"}[r.verdict]
            extra = f" [{r.links_checked} links ok]" if r.links_checked and not r.links_broken else ""
            print(f"{icon}  {r.label:<{width}}  {r.status}  {r.size}b  {r.latency_ms}ms{extra}")
            for f in r.findings:
                print(f"        - [{f.severity}/{f.code}] {f.detail}")
            for b in r.links_broken[:10]:
                print(f"        ! {b}")

        actions = remediate(results, args.apply_remediation)
        if actions:
            print("\nRemediation:")
            for a in actions:
                print("  " + a)
        if stale:
            print(f"\n[DEAD-MAN] {stale}")

    if args.log:
        append_history(results)
        write_json_atomic(REPORT, {
            "ts": now().isoformat(),
            "checked": len(results),
            "failed": [r.label for r in results if r.verdict == "FAIL"],
            "warned": [r.label for r in results if r.verdict == "WARN"],
            "events": {k: [r.label for r in v] for k, v in events.items()},
            "deadman": stale,
            "results": [r.to_dict() for r in results],
        })
        save_state(state)

        undelivered: list[str] = []

        if stale and not notify("Endpoint monitor STOPPED", stale, "urgent"):
            undelivered.append("monitor-stopped")
        for r in events["new_failures"]:
            ok = notify(f"DOWN: {r.label}",
                        f"{r.url}\n{r.findings[0].detail if r.findings else ''}\n"
                        f"Objective: {r.objective or 'unstated'}", "high")
            if not ok:
                # The state write above already recorded last_verdict=FAIL, so on
                # the next run this endpoint reads as "still failing" and would
                # NOT re-emit a new-failure alert. Roll its verdict back so the
                # alert is retried. An alert that failed to send must not be
                # consumed by the state machine.
                undelivered.append(r.label)
                state["endpoints"][r.label]["last_verdict"] = None
        for r in events["escalations"]:
            stx = state["endpoints"][r.label]
            n = stx["consecutive_failures"]
            if not notify(f"STILL DOWN ({n}x): {r.label}",
                          f"{r.url} has failed {n} consecutive checks "
                          f"and has not been fixed.", "urgent"):
                undelivered.append(r.label)
                # Same principle the new-failure branch above states explicitly:
                # an alert that failed to send must not be consumed by the state
                # machine. Without this, a dropped escalation at cf=3 had its
                # next opportunity at cf=6 — three runs, ~24h of silence on a
                # live outage. Step the counter back so the cadence retries on
                # the very next run instead of skipping a whole interval.
                stx["consecutive_failures"] -= 1
        for r in events["recoveries"]:
            if not notify(f"RECOVERED: {r.label}", r.url, "default"):
                undelivered.append(r.label)
        for r in events["flapping"]:
            if not notify(f"FLAPPING: {r.label}",
                          f"{r.url} is unstable across runs even though it is "
                          f"green now.", "high"):
                undelivered.append(r.label)
                # Un-record the notification so the edge re-fires next run.
                state["endpoints"][r.label].pop(
                    "flapping_notified_at_transitions", None)

        if undelivered:
            # The alerting channel failing is itself a monitoring failure. Saying
            # nothing here would be the purest form of the silent failure this
            # whole tool exists to prevent.
            print(f"[ALERTING DEGRADED] could not deliver alerts for: "
                  f"{', '.join(undelivered)}", file=sys.stderr)
            save_state(state)     # persist the rollback so retries actually happen
            return 2

    if any(r.verdict == "FAIL" for r in results) or stale:
        return 2
    return 1 if any(r.verdict == "WARN" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
