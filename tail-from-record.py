#!/usr/bin/env python3
"""Generate a response tail from the TOOL-CALL RECORD, not from the agent's memory of it.

WHY THIS EXISTS. Measured 2026-08-11 in session 4ac72061: the user issued **28 corrections
about the response tail**, spanning 91% of the session — 15 on `You:` used as
observation-as-completion, 5 on `Open:` stated without disposition, 5 on escalating a decision
back that the agent could have made. Twenty-eight corrections produced twenty-eight more
instances.

THE ROOT, and why five existing mechanisms miss it. A14 checks the tail is PRESENT. A62 checks
a Closure declaration EXISTS. A26 checks Open items carry a tracking ID. `tail-consistency-
check.py` checks fields do not CONTRADICT each other. All five validate FORM. Every one of the
user's 28 corrections was about FUNCTION — "you did not tell me whether you tried", "why are
you turning this back to me". The agent answered a communication failure with syntax
validators, and satisfying 28 accumulated rules felt like improvement while the function never
changed.

WHAT THIS DOES DIFFERENTLY. It does not check a tail. It DRAFTS one from what actually
happened: the tool calls in the transcript. `Done:` becomes what the record shows ran.
`Open:` becomes what the record shows was attempted-and-failed or written-and-uncommitted.
The agent then edits a draft grounded in evidence rather than composing from recollection at
the moment of least capacity for self-assessment.

WHAT IT CANNOT DO — the honest limit, stated because a tool that oversells itself is worse
than none:
  - It sees TOOL CALLS, not intent. It cannot know that a command you ran was the wrong one.
  - It cannot detect an `Open:` item you never attempted, because nothing appears in the
    record for work never begun. That is the largest residue and it is exactly where
    "observation-as-completion" lives.
  - It drafts; it does not judge. A tail that passes this is not thereby a good tail.

Exit 0 always — it produces a draft. --self-check returns 1 on fixture failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"


def load_turn(path: Path) -> list[dict]:
    """Tool calls since the last assistant TEXT block — i.e. the current turn's work."""
    events = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = d.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if d.get("type") == "assistant" and b.get("type") == "text" and b.get("text", "").strip():
                events.append({"kind": "text"})
            elif b.get("type") == "tool_use":
                events.append({"kind": "tool", "name": b.get("name"),
                               "input": b.get("input", {})})
            elif b.get("type") == "tool_result":
                txt = b.get("content")
                if isinstance(txt, list):
                    txt = " ".join(x.get("text", "") for x in txt if isinstance(x, dict))
                events.append({"kind": "result", "text": str(txt)[:4000],
                               "error": bool(b.get("is_error"))})
    # Walk back to the last text block; everything after it is this turn.
    last_text = max((i for i, e in enumerate(events) if e["kind"] == "text"), default=-1)
    return events[last_text + 1:]


def _first_dirty_tracked_file() -> str:
    """A tracked file with pending changes, so the dirty branch can be exercised. "" when the
    tree is clean, and the caller SKIPS rather than passing vacuously."""
    import subprocess
    root = Path(__file__).resolve().parent
    try:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=15)
        for line in r.stdout.splitlines():
            name = line[3:].strip()
            if name and not name.startswith("??"):
                return str(root / name)
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _first_clean_tracked_file() -> str:
    """A tracked file with no pending changes, for fixtures that need a genuinely-committed
    path. Returns "" when none exists, and the caller SKIPS rather than passing vacuously."""
    import subprocess
    root = Path(__file__).resolve().parent
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files"],
                           capture_output=True, text=True, timeout=15)
        for name in r.stdout.split():
            st = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", name],
                                capture_output=True, text=True, timeout=10)
            if st.returncode == 0 and not st.stdout.strip():
                return str(root / name)
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _git_status(path: str) -> str | None:
    """Porcelain status for ONE path: '' when clean/committed, a code like '?? ' or ' M' when
    not, None when git cannot answer. None is UNKNOWN and is never treated as clean."""
    import subprocess
    fp = Path(path)
    try:
        r = subprocess.run(["git", "-C", str(fp.parent), "status", "--porcelain", "--", fp.name],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        line = r.stdout.strip()
        return line[:2].strip() if line else ""
    except (OSError, subprocess.SubprocessError):
        return None


def _sha_resolves(sha: str, repos=None) -> bool:
    """Does this look-like-a-SHA actually name a commit? Checked, not assumed."""
    import subprocess
    for r in (repos or [Path.home() / "dev/infrastructure/tools", Path.home() / "dev/share"]):
        try:
            if subprocess.run(["git", "-C", str(r), "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True, timeout=10).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def summarise(events: list[dict]) -> dict:
    commits, files, cmds, failures, uncommitted = [], [], [], [], []
    sha_shaped = []
    for i, e in enumerate(events):
        if e["kind"] != "tool":
            continue
        name, inp = e.get("name"), e.get("input", {})
        nxt = events[i + 1] if i + 1 < len(events) else {}
        out = nxt.get("text", "") if nxt.get("kind") == "result" else ""
        if name in ("Write", "Edit"):
            fp = inp.get("file_path", "?")
            files.append(fp)
        elif name == "Bash":
            c = str(inp.get("command", ""))[:400]
            cmds.append(c)
            for sha in re.findall(r"^\s*([0-9a-f]{7,10})\s+\S", out, re.M):
                # A SHA-SHAPED STRING IS NOT A COMMIT. Independent review: this captured any
                # hex-looking token from any command's output. Verify it resolves to a real
                # commit object before claiming it; unverifiable ones are reported separately.
                (commits if _sha_resolves(sha) else sha_shaped).append(sha)
            # A failure the record shows: an error result, or a non-zero EXIT we printed.
            if nxt.get("error") or re.search(r"EXIT=[1-9]|Exit code [1-9]|FAIL", out):
                failures.append(c.splitlines()[0][:110])
    # ASK GIT, do not pattern-match commands. Two prior versions were proxies: the first
    # joined ALL commands (so merely RUNNING a file looked committed), the second matched the
    # BASENAME inside git commands (so `git add other/alpha.py` cleared `/x/alpha.py`).
    # Independent review flagged both as proxy-for-thing, the defect class this session hit
    # repeatedly. `git status --porcelain <path>` answers the actual question.
    for f in files:
        st = _git_status(f)
        if st is None:
            uncommitted.append(f"{f} (git state UNKNOWN — not evidence it was committed)")
        elif st:
            uncommitted.append(f"{f} [{st}]")
    return {"commits": sorted(set(commits)), "files": sorted(set(files)),
            "commands": len(cmds), "failures": failures,
            "uncommitted": sorted(set(uncommitted)),
            "sha_shaped_unverified": sorted(set(sha_shaped))}


def draft(s: dict) -> str:
    out = ["DRAFT TAIL — from the tool-call record, not from recollection", ""]
    out.append("Done (what the record shows):")
    if s["commits"]:
        out.append(f"  commits (verified to resolve): {', '.join(s['commits'][:8])}")
    if s.get("sha_shaped_unverified"):
        out.append(f"  SHA-SHAPED but NOT a resolving commit — do not cite as done: "
                   f"{', '.join(s['sha_shaped_unverified'][:6])}")
    if s["files"]:
        out.append(f"  files written/edited: {len(s['files'])} — " +
                   ", ".join(Path(f).name for f in s["files"][:6]))
    out.append(f"  bash invocations: {s['commands']}")
    if not (s["commits"] or s["files"]):
        out.append("  NOTHING the record can attribute — if you are about to write a Done:")
        out.append("  line, it is not grounded in this turn's tool calls.")
    out.append("")
    out.append("Open (what the record shows as incomplete):")
    if s["failures"]:
        for f in s["failures"][:6]:
            out.append(f"  FAILED/non-zero, verify it was resolved: {f}")
    if s["uncommitted"]:
        for f in s["uncommitted"][:6]:
            out.append(f"  written but never referenced in a later git command: {f}")
    if not (s["failures"] or s["uncommitted"]):
        out.append("  nothing detectable — NOTE: the record cannot show work never begun,")
        out.append("  which is exactly where observation-as-completion hides. This is not")
        out.append("  evidence that nothing is open.")
    out.append("")
    out.append("You: state ONLY what you cannot execute yourself. If you can run it, run it.")
    return "\n".join(out)


def _real_sha() -> str:
    """A SHA that genuinely resolves in this repo, so the fixture tests verification rather
    than string-shape. Falls back to a shape-only value if git is unavailable, and the
    dependent fixture is skipped in that case rather than passing vacuously."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent),
                            "rev-parse", "--short=7", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _os_environ_snapshot():
    import os
    return dict(os.environ)


def self_check() -> int:
    ok = []
    if not _real_sha():
        print("  [SKIP] git unavailable — SHA-verification fixtures cannot run; NOT a pass")
        return 2
    ev = [{"kind": "text"},
          {"kind": "tool", "name": "Write", "input": {"file_path": "/x/alpha.py"}},
          {"kind": "result", "text": "written"},
          {"kind": "tool", "name": "Bash", "input": {"command": "git add alpha.py && git commit"}},
          # A REAL resolving SHA, injected at fixture time. The old fixture used a made-up
          # one and passed only because SHA-shaped text was accepted as a commit.
          {"kind": "result", "text": f"  {_real_sha()} feat: thing"},
          {"kind": "tool", "name": "Bash", "input": {"command": "python3 broken.py"}},
          {"kind": "result", "text": "EXIT=1"}]
    s = summarise(ev)
    ok.append(("a RESOLVING commit SHA is captured", _real_sha() in s["commits"]))
    ok.append(("a non-zero EXIT is captured as a failure", len(s["failures"]) == 1))
    # A file resolved at fixture time as genuinely clean in git. Using __file__ failed
    # correctly: this file is MODIFIED while being edited, so git rightly flagged it. The
    # fixture was wrong, not the code.
    # A genuinely DIRTY tracked file. Sabotaging the dirty branch (`elif st:`) produced ZERO
    # failures because every other fixture path returns UNKNOWN, and the only real file used
    # was clean. The dirty branch was uncovered.
    _dirty = _first_dirty_tracked_file()
    if _dirty:
        ok.append(("a MODIFIED tracked file IS flagged with its git status",
                   any(_dirty in u and "[" in u for u in
                       summarise([{"kind": "text"},
                                  {"kind": "tool", "name": "Write",
                                   "input": {"file_path": _dirty}},
                                  {"kind": "result", "text": "ok"}])["uncommitted"])))
    else:
        print("  [SKIP] no dirty tracked file — the dirty branch cannot be exercised; NOT a pass")

    _clean = _first_clean_tracked_file()
    if _clean:
        ok.append(("a genuinely CLEAN tracked file is not flagged (asks git, not command text)",
                   not summarise([{"kind": "text"},
                                  {"kind": "tool", "name": "Write",
                                   "input": {"file_path": _clean}},
                                  {"kind": "result", "text": "ok"}])["uncommitted"]))
    else:
        print("  [SKIP] no clean tracked file available — that fixture cannot run; NOT a pass")
    ok.append(("git-add of a DIFFERENT file with the same basename does NOT clear this one",
               any("/nonexistent-dir-xyz/alpha.py" in u for u in
                   summarise([{"kind": "text"},
                              {"kind": "tool", "name": "Write",
                               "input": {"file_path": "/nonexistent-dir-xyz/alpha.py"}},
                              {"kind": "result", "text": "ok"},
                              {"kind": "tool", "name": "Bash",
                               "input": {"command": "git add other/alpha.py"}},
                              {"kind": "result", "text": ""}])["uncommitted"])))
    ev2 = [{"kind": "text"},
           {"kind": "tool", "name": "Write", "input": {"file_path": "/x/orphan.md"}},
           {"kind": "result", "text": "written"}]
    # THE FIXTURE THAT WAS MISSING. Sabotaging the git-only filter produced ZERO failures
    # because every existing fixture used `git add`, which passes either way. A file merely
    # RUN must still be flagged — that is the whole point of the filter, and it was untested.
    ev_run = [{"kind": "text"},
              {"kind": "tool", "name": "Write", "input": {"file_path": "/x/ran.py"}},
              {"kind": "result", "text": "written"},
              {"kind": "tool", "name": "Bash", "input": {"command": "python3 ran.py --self-check"}},
              {"kind": "result", "text": "PASS"}]
    ok.append(("a file that was only RUN (not git-added) must still be flagged uncommitted",
               any(u.startswith("/x/ran.py") for u in summarise(ev_run)["uncommitted"])))
    ok.append(("a file never referenced in a git command IS flagged",
               any(u.startswith("/x/orphan.md") for u in summarise(ev2)["uncommitted"])))
    ok.append(("an empty turn produces a draft that says so, not a clean Done",
               "NOTHING the record can attribute" in draft(summarise([{"kind": "text"}]))))
    ok.append(("the empty-Open case warns rather than reassures",
               "is not" in draft(summarise([{"kind": "text"}])) and
               "evidence that nothing is open" in draft(summarise([{"kind": "text"}]))))
    # DRIVE load_turn() — a reviewer flipped its first-text/last-text boundary and every
    # fixture still passed, because nothing called it. Written to a real temp transcript.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _t = Path(_td) / "s.jsonl"
        _t.write_text("\n".join([
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "old turn"}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "OLD"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "NEW turn"}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "NEW"}}]}}),
        ]) + "\n")
        _ev = load_turn(_t)
        _cmds = [e["input"]["command"] for e in _ev if e["kind"] == "tool"]
    ok.append(("load_turn returns only events AFTER the LAST text block", _cmds == ["NEW"]))
    ok.append(("load_turn does not leak the previous turn's commands", "OLD" not in _cmds))
    # DRIVE the SHA verification: a made-up hex string must NOT be reported as a commit.
    ev_fake = [{"kind": "text"},
               {"kind": "tool", "name": "Bash", "input": {"command": "echo hi"}},
               {"kind": "result", "text": "  deadbee f00 not-a-real-commit"}]
    _sf = summarise(ev_fake)
    ok.append(("a SHA-shaped string that does not resolve is NOT reported as a commit",
               "deadbee" not in _sf["commits"] and "deadbee" in _sf["sha_shaped_unverified"]))

    # The refusal must be STRUCTURAL. A fixture that only checks warning text would pass on
    # the advisory version, which is the version that failed.
    import subprocess as _sp
    _env = dict(_os_environ_snapshot(), CLAUDE_CODE_SESSION_ID="deadbeef-not-this-session")
    _r = _sp.run([sys.executable, str(Path(__file__).resolve()),
                  "--session", "00000000-0000-0000-0000-000000000000"],
                 capture_output=True, text=True, timeout=60, env=_env)
    ok.append(("reading a FOREIGN session without the flag must REFUSE (exit 2), not warn",
               _r.returncode == 2))

    failed = [m for m, good in ok if not good]
    for m in failed:
        print(f"  [FAIL/self-check] {m}")
    if not failed:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the extraction")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", default="")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--allow-foreign-session", action="store_true",
                    help="deliberately read ANOTHER session's live transcript (refused by default)")
    args = ap.parse_args()

    if args.self_check:
        print("TAIL FROM RECORD: self-check")
        return self_check()

    # SESSION IDENTITY MUST BE AUTHORITATIVE, never newest-by-mtime. The derived-state scanner
    # caught this before it shipped: under concurrency, "most recently modified transcript" is
    # whichever PEER session wrote last, so the tool would draft a tail from another session's
    # work. Same class as the documented `usage.jsonl rows[-1]` trap. Resolve from the
    # environment, and refuse rather than guess.
    # identity-authoritative: ~/.claude/.sessions/<uuid>.json — the per-session registry the
    # SessionStart hook writes from the documented stdin session_id. An env var is only a
    # LOOKUP KEY and is accepted ONLY after it validates against that registry; two distinct
    # validated keys is a conflict we refuse rather than guess through. Pattern taken from the
    # /handoff skill's authority-anchored resolver.
    SESSIONS = Path.home() / ".claude" / ".sessions"
    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def _validated(k: str) -> str:
        return k if k and UUID_RE.match(k) and (SESSIONS / f"{k}.json").exists() else ""

    if args.session:
        sid = args.session                      # explicit operator intent overrides discovery
    else:
        # identity-authoritative: ~/.claude/.sessions/<uuid>.json — _validated() requires
        # the registry file to exist, so this env var is a lookup key, never the source.
        v1 = _validated(os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
        # identity-authoritative: ~/.claude/.sessions/<uuid>.json — _validated() requires
        # the registry file to exist, so this env var is a lookup key, never the source.
        v2 = _validated(os.environ.get("CLAUDE_SESSION_ID", ""))
        if v1 and v2 and v1 != v2:
            print("REFUSING: two distinct validated session ids in the environment. "
                  "Pass --session explicitly.")
            return 2
        sid = v1 or v2
    if not sid:
        print("REFUSING TO GUESS: no session id. Pass --session <uuid> or set")
        print("CLAUDE_CODE_SESSION_ID. Picking the newest transcript by mtime would select")
        print("whichever PEER session wrote last — a wrong tail is worse than no tail.")
        return 2
    paths = sorted(PROJECTS.rglob(f"{sid}.jsonl"))
    if not paths:
        print(f"no transcript for session {sid[:8]} — cannot ground a tail in the record")
        return 2
    tp = paths[0]
    import os as _os
    st = tp.stat()
    own = sid == _os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not own and not args.allow_foreign_session:
        # STRUCTURAL, not advisory. The first version printed a warning and produced the draft
        # anyway. This session measured advisory enforcement failing: 12 L-654 advisory prompts
        # with no behavioural change, against a workspace record of 4,618 advisory fires with no
        # effect. Shipping a warning as the remedy WAS the failure mode. Refuse by default;
        # reading another session requires choosing it explicitly.
        print("REFUSED: that transcript belongs to another session, and it is LIVE and")
        print("append-only — a draft from it may describe work the owning session is still")
        print("performing. Measured: 2,838 bytes of growth between two runs seconds apart.")
        print("If you mean to do this, pass --allow-foreign-session.")
        return 2
    print(draft(summarise(load_turn(tp))))
    print()
    print(f"  read from: {tp.name[:8]}…  ({st.st_size} bytes at read time)")
    if not own:
        # Reached only with --allow-foreign-session. THE MISATTRIBUTION MECHANISM, diagnosed 2026-08-11. A reviewer ran this against a
        # session that was still working and got that session's in-flight tool calls, which
        # from their position looked like "an unrelated concurrent actor's work". It was not a
        # parsing bug: the transcript is a LIVE, append-only file with no as-of boundary, and
        # it grew measurably during a 3-second observation. Reading another session's
        # transcript is inherently a race.
        print("  ⚠ THIS IS NOT YOUR SESSION. The transcript is live and append-only, so this")
        print("    draft may describe work the owning session is still performing. Attributing")
        print("    it to your own turn is the misattribution this warning exists to prevent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
