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


def summarise(events: list[dict]) -> dict:
    commits, files, cmds, failures, uncommitted = [], [], [], [], []
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
                commits.append(sha)
            # A failure the record shows: an error result, or a non-zero EXIT we printed.
            if nxt.get("error") or re.search(r"EXIT=[1-9]|Exit code [1-9]|FAIL", out):
                failures.append(c.splitlines()[0][:110])
    # Only GIT commands count as "this file was handled". The first version joined ALL
    # commands, so merely RUNNING a file made it look committed — found on this tool's own
    # first run, where it failed to flag itself.
    joined = " ".join(c for c in cmds if re.search(r"\bgit\s+(add|commit|rm|mv)\b", c))
    for f in files:
        if Path(f).name not in joined:
            uncommitted.append(f)
    return {"commits": sorted(set(commits)), "files": sorted(set(files)),
            "commands": len(cmds), "failures": failures,
            "uncommitted": sorted(set(uncommitted))}


def draft(s: dict) -> str:
    out = ["DRAFT TAIL — from the tool-call record, not from recollection", ""]
    out.append("Done (what the record shows):")
    if s["commits"]:
        out.append(f"  commits: {', '.join(s['commits'][:8])}")
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


def self_check() -> int:
    ok = []
    ev = [{"kind": "text"},
          {"kind": "tool", "name": "Write", "input": {"file_path": "/x/alpha.py"}},
          {"kind": "result", "text": "written"},
          {"kind": "tool", "name": "Bash", "input": {"command": "git add alpha.py && git commit"}},
          {"kind": "result", "text": "  abc1234 feat: thing"},
          {"kind": "tool", "name": "Bash", "input": {"command": "python3 broken.py"}},
          {"kind": "result", "text": "EXIT=1"}]
    s = summarise(ev)
    ok.append(("a commit SHA in output is captured", "abc1234" in s["commits"]))
    ok.append(("a non-zero EXIT is captured as a failure", len(s["failures"]) == 1))
    ok.append(("a file later git-added is NOT flagged uncommitted", not s["uncommitted"]))
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
               summarise(ev_run)["uncommitted"] == ["/x/ran.py"]))
    ok.append(("a file never referenced in a git command IS flagged",
               summarise(ev2)["uncommitted"] == ["/x/orphan.md"]))
    ok.append(("an empty turn produces a draft that says so, not a clean Done",
               "NOTHING the record can attribute" in draft(summarise([{"kind": "text"}]))))
    ok.append(("the empty-Open case warns rather than reassures",
               "is not" in draft(summarise([{"kind": "text"}])) and
               "evidence that nothing is open" in draft(summarise([{"kind": "text"}]))))
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
    args = ap.parse_args()

    if args.self_check:
        print("TAIL FROM RECORD: self-check")
        return self_check()

    # SESSION IDENTITY MUST BE AUTHORITATIVE, never newest-by-mtime. The derived-state scanner
    # caught this before it shipped: under concurrency, "most recently modified transcript" is
    # whichever PEER session wrote last, so the tool would draft a tail from another session's
    # work. Same class as the documented `usage.jsonl rows[-1]` trap. Resolve from the
    # environment, and refuse rather than guess.
    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID", "") \
        or os.environ.get("CLAUDE_SESSION_ID", "")
    if not sid:
        print("REFUSING TO GUESS: no session id. Pass --session <uuid> or set")
        print("CLAUDE_CODE_SESSION_ID. Picking the newest transcript by mtime would select")
        print("whichever PEER session wrote last — a wrong tail is worse than no tail.")
        return 2
    paths = sorted(PROJECTS.rglob(f"{sid}.jsonl"))
    if not paths:
        print(f"no transcript for session {sid[:8]} — cannot ground a tail in the record")
        return 2
    print(draft(summarise(load_turn(paths[0]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
