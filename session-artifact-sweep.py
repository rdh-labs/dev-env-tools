#!/usr/bin/env python3
"""Compare a session's EPHEMERAL /tmp artifacts against its DURABLE rescue directory.

WHY: on 2026-08-11 a rescue globbed `*.md` and `*.txt` only. Three `.py` analysis scripts —
the ones that produced this session's retracted numbers — were never copied, while both
CONTINUITY.md and the rescue commit CLAIMED they were saved. The claim was asserted from the
write, never verified by a read. Seven more files created after the rescue were also missing,
because a rescue is a SNAPSHOT and was being treated as a standing guarantee.

WHAT THIS IS FOR: answering "is everything saved?" with a read instead of a recollection.

DESIGN NOTES that are load-bearing:
- EXTENSION-BLIND. It compares by FILENAME across the whole source tree. The original defect
  was an extension glob; a checker with its own extension list would reproduce it.
- MISSING is a FAILURE state, and so is "source directory absent" — the latter means the
  session's /tmp is already gone, which is unrecoverable, not clean.
- --rescue copies, then RE-READS to confirm. `cp` exiting 0 is not evidence the file arrived.

Exit codes: 0 when everything is rescued OR --report-only; 1 when files are missing and you
did not ask it to fix them. That non-zero is deliberate: this is a check, not telemetry.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

TMP_ROOT = Path("/tmp/claude-1001")

# Directories os.walk could not read. Non-empty => the sweep saw less than the whole tree.
COLLECT_ERRORS: list[str] = []


def find_session_dir(session_id: str) -> Path | None:
    if not TMP_ROOT.exists():
        return None
    for proj in TMP_ROOT.iterdir():
        cand = proj / session_id
        if cand.is_dir():
            return cand
    return None


# Roots that survive session end. A file already living under one of these needs no rescue.
# `/tmp` is NOT here — that is the whole premise.
DURABLE_ROOTS = (Path.home() / ".claude" / "projects", Path.home() / "dev")


def digest(path: Path) -> str | None:
    """sha256 of the file's bytes. None when unreadable -- which is itself reportable."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def collect(root: Path) -> dict[str, Path]:
    """Every file under root, keyed by path RELATIVE TO root -- never by basename.

    Basename keying silently collapsed `a/report.py` and `b/report.py` into one entry, so a
    durable directory holding either made both read as rescued. That is a false SAFE, the one
    failure this tool exists to prevent. Found by independent review AFTER the local
    self-check passed 4/4 -- the fixtures shared the author's blind spot.
    """
    out: dict[str, Path] = {}
    if not root.exists():
        return out
    # os.walk with onerror, NOT rglob: rglob SILENTLY skips directories it cannot read, so an
    # unreadable subtree yields "0 of 0 missing -> COMPLETE" -- the cleanest possible report
    # over data that is entirely invisible. Unreadable dirs are collected and surfaced.
    errors: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda e: errors.append(str(e))):
        for fn in filenames:
            fp = Path(dirpath) / fn
            out[str(fp.relative_to(root))] = fp
    if errors:
        out["__WALK_ERRORS__"] = Path("/dev/null")   # forces a non-COMPLETE verdict
        COLLECT_ERRORS.extend(errors)
    return out


def content_index(root: Path) -> set[str]:
    """Hashes of everything already durable. Rescue is proven by CONTENT, not by filename --
    a stale file with the right name is not a copy of anything."""
    return {d for p in collect(root).values() if (d := digest(p))}


def already_durable(path: Path) -> Path | None:
    """A symlink whose TARGET already sits under a durable root needs no copy.

    Found on this tool's first run: `tasks/*.output` are 129-byte symlinks into
    ~/.claude/projects/.../subagents/. Following them made 15 safe files look like 2.4MB of
    loss. Reporting them as MISSING would have been a false alarm; silently dropping them
    would have been the original defect in mirror image. So they get their own verdict.
    """
    if not path.is_symlink():
        return None
    target = path.resolve()
    # is_relative_to, NOT startswith: "/home/u/dev-old" string-prefixes "/home/u/dev" and
    # would have been wrongly excused as durable.
    return target if any(target.is_relative_to(r) for r in DURABLE_ROOTS) else None


def sweep(session_id: str, durable: Path, skip_large_mb: float = 5.0, src_override: Path | None = None):
    src_dir = src_override or find_session_dir(session_id)
    if src_dir is None:
        return {"verdict": "SOURCE_GONE", "detail": f"no /tmp dir for {session_id} — ephemeral state already lost",
                "missing": [], "src": None, "durable": str(durable)}

    src = collect(src_dir)
    dst_hashes = content_index(durable)
    missing, elsewhere, unreadable = [], [], []
    for name, path in sorted(src.items()):
        if (target := already_durable(path)) is not None:
            elsewhere.append({"name": name, "target": str(target)})
            continue
        d = digest(path)
        if d is None:
            # Broken symlink or unreadable file: NEVER silently dropped. Being unable to
            # read it is a reason to report it, not a reason to omit it.
            unreadable.append({"name": name, "path": str(path)})
            continue
        if d in dst_hashes:
            continue                       # proven rescued: identical BYTES exist in durable
        try:
            size_mb = path.stat().st_size / 1_048_576
        except OSError:
            unreadable.append({"name": name, "path": str(path)})
            continue
        missing.append({"name": name, "path": str(path), "size_mb": round(size_mb, 2),
                        "oversize": size_mb > skip_large_mb})
    verdict = "COMPLETE" if not (missing or unreadable) else "MISSING"
    return {"verdict": verdict,
            "detail": f"{len(missing)} of {len(src)} source file(s) have no byte-identical "
                      f"copy in durable ({len(unreadable)} unreadable)",
            "missing": missing, "durable_elsewhere": elsewhere, "unreadable": unreadable,
            "src": str(src_dir), "durable": str(durable),
            "source_count": len(src), "durable_count": len(dst_hashes)}


# Filenames whose CONTENT is typically a live credential. Rescuing these moves secrets from
# an ephemeral dir into a git repo. Found the hard way: a rescue copied live JWT session
# cookies for a client site into ~/dev/share. They were untracked and removed, but the tool
# is scheduled — an autonomous copier must never blind-copy a secret.
CREDENTIAL_NAMES = (".jar", ".cookies", "cookies.txt", "login.json", ".netrc",
                    "credentials.json", "token.json", ".pem", ".key", "id_rsa")


def looks_like_credential(name: str) -> bool:
    n = name.lower()
    return any(n.endswith(x) or n.rsplit("/", 1)[-1] == x for x in CREDENTIAL_NAMES)


def rescue(result, durable: Path) -> list[str]:
    """Copy missing files, then RE-READ to confirm each landed. cp exit 0 proves nothing.

    Credential-shaped files are SKIPPED and NAMED — never silently, because a silent skip
    would recreate the original defect (something absent from durable with no record why).
    """
    durable.mkdir(parents=True, exist_ok=True)
    confirmed, failed = [], []
    for m in result["missing"]:
        if looks_like_credential(m["name"]):
            failed.append(f"{m['name']} (SKIPPED: credential-shaped — not copied into a repo)")
            continue
        if m["oversize"]:
            failed.append(f"{m['name']} (oversize {m['size_mb']}MB — copy manually if wanted)")
            continue
        target = durable / m["name"]
        try:
            # names are RELATIVE PATHS since the basename-collision fix; without this the
            # copy fails ENOENT on every nested file. Caught by RUNNING it, not by review:
            # both review legs saw the pre-relative-path commit.
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(m["path"], target)
        except OSError as exc:
            failed.append(f"{m['name']} ({exc})")
            continue
        if target.exists() and target.stat().st_size == Path(m["path"]).stat().st_size:
            confirmed.append(m["name"])
        else:
            failed.append(f"{m['name']} (copied but read-back mismatch)")
    return confirmed, failed


def self_check() -> int:
    """Fixtures with known answers, run on every scheduled invocation.

    Every case below is a defect that ACTUALLY SHIPPED and was caught by independent review
    after an earlier 4/4 local pass. Fixtures written by the author of the bug share the
    author's blind spot, so these are regression tests against real history, not imagination.
    """
    import tempfile
    ok = []
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (src := t / "src").mkdir(); (dur := t / "dur").mkdir()
        (src / "a").mkdir(); (src / "b").mkdir()
        (src / "kept.md").write_text("x");  (dur / "kept.md").write_text("x")
        (src / "lost.py").write_text("y")                       # extension-glob defect
        (src / "a" / "report.py").write_text("AAA")             # basename collision...
        (src / "b" / "report.py").write_text("BBB")             # ...same name, other bytes
        (dur / "report.py").write_text("AAA")                   # only ONE is really rescued
        (src / "stale.md").write_text("fresh content")
        (dur / "stale.md").write_text("DIFFERENT old content")  # right name, wrong bytes
        idx = content_index(dur)
        got = {n for n, pth in collect(src).items() if (d := digest(pth)) and d not in idx}
        ok.append(("non-.md file must be flagged (the original extension-glob defect)",
                   "lost.py" in got))
        ok.append(("byte-identical copy must NOT be flagged", "kept.md" not in got))
        ok.append(("basename collision: the UNRESCUED twin must be flagged",
                   "b/report.py" in got))
        ok.append(("basename collision: the rescued twin must not be flagged",
                   "a/report.py" not in got))
        ok.append(("same name + different bytes is NOT a rescue", "stale.md" in got))
        (real := t / "real.txt").write_text("z")
        ok.append(("symlink outside a durable root is NOT excused",
                   already_durable(src / "l.txt") is None if not (src / "l.txt").symlink_to(real) else True))
        (src / "broken.lnk").symlink_to(t / "gone")
        ok.append(("broken symlink is counted, never invisible",
                   "broken.lnk" in collect(src)))
    # VERDICT-LINE fixtures: without these, hardcoding verdict="COMPLETE" passes everything.
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (s2 := t/"s").mkdir(); (d2 := t/"d").mkdir()
        (s2/"only-here.py").write_text("irreplaceable")
        r_missing = sweep("x", d2, src_override=s2)
        (d2/"only-here.py").write_text("irreplaceable")
        r_complete = sweep("x", d2, src_override=s2)
        ok.append(("sweep() must return MISSING when a file is genuinely unrescued",
                   r_missing["verdict"] == "MISSING"))
        ok.append(("sweep() must return COMPLETE once the bytes exist in durable",
                   r_complete["verdict"] == "COMPLETE"))
    ok.append(("a nonexistent session is SOURCE_GONE, never COMPLETE",
               sweep("00000000-0000-0000-0000-000000000000", Path("/nonexistent"))["verdict"]
               == "SOURCE_GONE"))
    failed = [m for m, good in ok if not good]
    for m in failed:
        print(f"  [FAIL/self-check] {m}")
    if not failed:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the comparison logic")
    return 1 if failed else 0


def all_sessions(durable_root: Path) -> int:
    """Scheduled mode: every live /tmp session, not just mine. /tmp retention is ~24-36h, so
    an unrescued artifact has a DEADLINE — this is what makes a date-driven trigger correct
    rather than decorative. Notifies on loss; silence means genuinely nothing at risk."""
    if not TMP_ROOT.exists():
        print("SWEEP(all): no /tmp session root — nothing to check")
        return 0
    at_risk, unstarted, checked = [], [], 0
    for proj in TMP_ROOT.iterdir():
        if not proj.is_dir():
            continue
        for sess in proj.iterdir():
            if not sess.is_dir():
                continue
            checked += 1
            durable = durable_root / f"session-{sess.name[:8]}-artifacts"
            r = sweep(sess.name, durable)
            if r["verdict"] != "MISSING":
                continue
            # Sessions with no rescue dir were previously SKIPPED as "not a broken promise".
            # That printed "0 with unrescued artifacts" while real data sat in /tmp under a
            # deletion clock — a false SAFE in the autonomous path, the worst place for one.
            # They are now their own category: reported, counted, never suppressed.
            (unstarted if not durable.exists() else at_risk).append(
                (sess.name[:8], len(r["missing"])))
    print(f"SWEEP(all): {checked} session(s) checked, {len(at_risk)} with unrescued artifacts, "
          f"{len(unstarted)} with no rescue directory at all")
    for sid, n in unstarted:
        print(f"  NO-RESCUE-DIR  {sid}  {n} file(s) live only in /tmp")
    for sid, n in at_risk:
        print(f"  AT RISK  {sid}  {n} file(s) not in its rescue directory")
    if at_risk and (NOTIFY := Path.home() / "bin" / "notify.sh").exists():
        body = ", ".join(f"{s}:{n}" for s, n in at_risk)
        p = subprocess.run([str(NOTIFY), "Session artifacts unrescued",
                            f"{len(at_risk)} session(s) have files only in /tmp ({body}). "
                            f"/tmp retention is ~24-36h.", "--priority", "high", "--channel", "auto"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            print(f"  [WARN] notify.sh exit {p.returncode} — alert NOT delivered", file=sys.stderr)
    # Non-zero when anything is at risk: a scheduled check that always exits 0 cannot be
    # monitored by exit code, which makes its own failure invisible.
    return 1 if at_risk else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    ap.add_argument("--all-sessions", action="store_true", help="scheduled mode: check every live session")
    ap.add_argument("--self-check", action="store_true")
    # NOT required=True: that made --self-check and --all-sessions unrunnable without an
    # irrelevant flag — a checker that cannot demonstrate it works. Validated per-mode below.
    ap.add_argument("--durable", type=Path, help="the rescue directory to compare against")
    ap.add_argument("--rescue", action="store_true", help="copy the missing files, with read-back confirmation")
    ap.add_argument("--report-only", action="store_true", help="always exit 0")
    args = ap.parse_args()

    if args.self_check:
        print("SESSION ARTIFACT SWEEP: self-check")
        return self_check()

    if args.all_sessions:
        return all_sessions(args.durable.expanduser() if args.durable else Path.home() / "dev" / "share")

    if not args.durable:
        print("ERROR: --durable is required for a single-session sweep", file=sys.stderr)
        return 1
    if not args.session:
        print("ERROR: no session id (pass --session or set CLAUDE_CODE_SESSION_ID)", file=sys.stderr)
        return 1

    result = sweep(args.session, args.durable.expanduser())
    print(f"SESSION ARTIFACT SWEEP: {result['verdict']}")
    print(f"  {result['detail']}")
    print(f"  source : {result['src']}")
    print(f"  durable: {result['durable']}")
    if result.get("durable_elsewhere"):
        # Named, never silent: the reader must be able to check this judgement.
        print(f"  {len(result['durable_elsewhere'])} symlink(s) already durable elsewhere "
              f"(e.g. {result['durable_elsewhere'][0]['target']})")

    if result["verdict"] == "MISSING":
        for m in result["missing"]:
            flag = "  [OVERSIZE]" if m["oversize"] else ""
            print(f"    MISSING  {m['name']}  ({m['size_mb']}MB){flag}")
        if args.rescue:
            confirmed, failed = rescue(result, args.durable.expanduser())
            print(f"  rescued (read-back confirmed): {len(confirmed)}")
            for f in failed:
                print(f"    NOT RESCUED: {f}")
            after = sweep(args.session, args.durable.expanduser())
            print(f"  RE-SWEEP: {after['verdict']} — {after['detail']}")
            return 0 if after["verdict"] == "COMPLETE" or args.report_only else 1

    return 0 if (result["verdict"] == "COMPLETE" or args.report_only) else 1


if __name__ == "__main__":
    sys.exit(main())
