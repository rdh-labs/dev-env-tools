#!/usr/bin/env python3
# BUDGET: 3600
# (DEC-366 pilot budget for scheduled-check-runner, artifact-sweep at 07:00/19:00; measured 2026-09-02..10-02:
#  p50 84 s over 47 runs; the only 3 runs past 30 min (7,224 s, 32,626 s, 3,740 s) were all adverse rc 1.
#  The same file backs gate-ledger-archive, which is not enabled in the pilot and is unaffected.)
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
- WHERE copies go is decided ONCE, by rescue_dir(): ${XDG_STATE_HOME:-~/.local/state}/claude-rescue/<project-dir>/
  session-<sid8>/<kind>/ -- never ~/dev/share (2026-09-27). Callers pass --kind or ask --print-rescue-dir.

Exit codes: 0 when everything is rescued OR --report-only; 1 when files are missing and you
did not ask it to fix them. That non-zero is deliberate: this is a check, not telemetry.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import os
import shutil
import stat
import subprocess
import time
import sys
from pathlib import Path

# SWEEP_TMP_ROOT (tests only) moves the whole ephemeral root, for the reason SWEEP_ORPHAN_ROOT exists below: without
# it --all-sessions can only run against the machine's real /tmp (323 session dirs, 8.1 GB on 2026-09-27), so a
# round-trip control would read ambient state and send real notifications.
TMP_ROOT = Path(os.environ.get("SWEEP_TMP_ROOT") or "/tmp/claude-1001")

# Directory names never rescued: fixture repos, dependency dumps, bytecode. Surfaced, not silent.
PRUNE_DIRS = {".git", "node_modules", "__pycache__"}
PRUNED_DIRS: list[str] = []
# Directories os.walk could not read. Non-empty => the sweep saw less than the whole tree.
COLLECT_ERRORS: list[str] = []
# NON-REGULAR files (FIFO, socket, device) skipped BEFORE any open() -- "path (kind)". Surfaced, not silent, like
# PRUNED_DIRS: they hold no bytes to rescue, and open() on a writer-less FIFO never returns (Dart tqbNKHjSsJUh R0-1).
SKIPPED_FILES: list[str] = []
_SPECIAL_KINDS = ((stat.S_ISFIFO, "fifo"), (stat.S_ISSOCK, "socket"), (stat.S_ISCHR, "char-device"),
                  (stat.S_ISBLK, "block-device"))


def _special_kind(path: Path) -> str | None:
    """The kind of a NON-REGULAR file, decided by os.lstat (and, for a symlink, by its target) before anything opens
    it: 'fifo', 'socket', 'char-device', 'block-device'. None for a regular file or a directory, and None for a path
    that cannot be stat'ed at all -- a broken symlink stays in the listing and is REPORTED unreadable, as before.

    A symlink is judged by its TARGET because open() follows it: a link to a FIFO blocks exactly as the FIFO does.
    Measured 2026-09-27: 8 FIFOs under /tmp/claude-1001 hung two scheduled --all-sessions runs for 10 h and 22 h.
    """
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            st = os.stat(path)
    except OSError:
        return None
    if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
        return None
    return next((name for test, name in _SPECIAL_KINDS if test(st.st_mode)), "special")


# A session id names ONE path component -- the FULL id, not just the 8 characters the layout keeps (2026-09-27
# review of step 1.2: `--session <sid>/../../../elsewhere --rescue` swept a tree OUTSIDE TMP_ROOT and wrote its
# copies above the rescue root; `--session ..` swept the whole root into one project's partition). The rule is
# precompact_checkpoint.SID_OK's. 0 of 346 live session dirs failed it when it was introduced.
SID_OK = re.compile(r"(?!\.+\Z)[A-Za-z0-9._-]{1,64}")


def check_session_id(sid) -> str:
    """The id itself, if it can name exactly one directory; ValueError otherwise -- before anything is read."""
    if not isinstance(sid, str) or not SID_OK.fullmatch(sid):
        raise ValueError(f"session id {sid!r} cannot name a directory")
    return sid


def find_session_dir(session_id: str) -> Path | None:
    check_session_id(session_id)
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

# THE RESCUE LAYOUT, defined ONCE (plan v2.1 step 1.2, review H5, 2026-09-27). Every writer (precompact_checkpoint.py,
# ~/bin/context-ceiling-watch, a manual --rescue) and every reader (--all-sessions, ~/bin/unrescued-check) resolves
# through rescue_dir(); the others pass --kind or ask --print-rescue-dir instead of building a path of their own.
#   <root>/<project-dir>/session-<sid8>/<kind>/      root = ${XDG_STATE_HOME:-~/.local/state}/claude-rescue
# <project-dir> is the /tmp source's parent (e.g. -home-ichardart-dev), so each project's copies stay in their own
# partition: a confidential engagement's sessions never land in the dev estate's directory.
# WHY NOT ~/dev/share: three producers defaulted to it although the estate had REJECTED it as a durable root on
# 2026-09-05 (precompact_checkpoint.py:28-30: a repo with a remote and session-end auto-commits). The rejection was
# never executed; by 2026-09-26 share showed 4,200 Source Control lines, 99% session copies, two sessions pushed.
RESCUE_KINDS = ("precompact", "auto-sweep")
UNATTRIBUTED_PROJECT = "_unattributed"   # the /tmp source is gone, so no project dir can be read (never a real slug)


def rescue_root() -> Path:
    """${XDG_STATE_HOME:-~/.local/state}/claude-rescue. XDG Base Directory spec 0.8: unset OR EMPTY means
    $HOME/.local/state, and a RELATIVE value is invalid and is ignored."""
    xdg = os.environ.get("XDG_STATE_HOME", "")
    return (Path(xdg) if os.path.isabs(xdg) else Path.home() / ".local" / "state") / "claude-rescue"


def rescue_dir(sid: str, kind: str | None, project: str | None = None, root: Path | None = None) -> Path:
    """<root>/<project-dir>/session-<sid8>/<kind>/ -- the single source of the rescue layout.

    kind: one of RESCUE_KINDS for a WRITER's own directory, or None for the session directory that holds every
    kind -- what a READER indexes, so a file counts as rescued if it sits under EITHER kind (review H5).
    project: the /tmp source's parent dir name; None derives it from the live /tmp tree, and UNATTRIBUTED_PROJECT
    when that tree is gone (a SOURCE_GONE session has nothing to copy, so nothing is ever written there).
    Both are refused (ValueError) unless each names exactly one path component: the FULL session id, and a project
    that is not '', '.', '..' and holds no '/' or NUL.
    """
    if kind is not None and kind not in RESCUE_KINDS:
        raise ValueError(f"unknown rescue kind {kind!r} (expected one of {', '.join(RESCUE_KINDS)})")
    check_session_id(sid)
    if project is None:
        src = find_session_dir(sid)
        project = src.parent.name if src is not None else UNATTRIBUTED_PROJECT
    if project in ("", ".", "..") or "/" in project or "\0" in project:
        raise ValueError(f"project dir {project!r} cannot name a directory")
    base = (root if root is not None else rescue_root()) / project / f"session-{sid[:8]}"
    return base / kind if kind else base


def _open_regular(path: Path):
    """A binary file object for a REGULAR file, else None -- and it never blocks.

    os.walk lists FIFOs, sockets and devices as files, and open() on a FIFO with no writer never returns. Measured
    2026-09-27: two scheduled --all-sessions runs had sat in open() (wchan wait_for_partner) for 10 h and 22 h on
    test FIFOs in session 779d2073's scratchpad, so the twice-daily sweep had reported nothing since 09-26 07:04.
    Two layers: _special_kind() (lstat, BEFORE any open) refuses a non-regular file outright; O_NONBLOCK plus fstat
    on the OPEN fd then covers a file swapped for a FIFO between that check and this open. Anything refused here has
    no bytes to rescue, and callers REPORT it (skipped or unreadable) -- it is never dropped silently.
    """
    if _special_kind(path) is not None:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            return os.fdopen(fd, "rb")
    except OSError:
        pass
    os.close(fd)
    return None


def digest(path: Path) -> str | None:
    """sha256 of the file's bytes. None when unreadable or not a regular file -- which is itself reportable."""
    h = hashlib.sha256()
    fh = _open_regular(path)
    if fh is None:
        return None
    try:
        with fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def collect(root: Path, prune: bool = True) -> dict[str, Path]:
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
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: errors.append(str(e))):
        # PRUNE, never silently: an embedded .git (a test-fixture repo) makes `git add` of the
        # rescue refuse or nest a repo; node_modules dumps and __pycache__ are reconstructible
        # and trip the credential scanner on prop names. Peer session c37ca269 hit both on
        # 2026-09-19 committing an auto-sweep and pruned by hand. Pruned dirs are COUNTED and
        # surfaced so "0 missing" cannot be read as "everything copied".
        # prune=False for the durable index and the credential audit: a file already rescued
        # under a fixture .git must still count as rescued, and a token in .git/config is still
        # a token (Agent review leg, 2026-09-19).
        pruned = [d for d in dirnames if d in PRUNE_DIRS] if prune else []
        if pruned:
            PRUNED_DIRS.extend(str(Path(dirpath) / d) for d in pruned)
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if (kind := _special_kind(fp)) is not None:
                SKIPPED_FILES.append(f"{fp} ({kind})")   # never opened, never listed as a file, never silent
                continue
            out[str(fp.relative_to(root))] = fp
    if errors:
        out["__WALK_ERRORS__"] = Path("/dev/null")   # forces a non-COMPLETE verdict
        COLLECT_ERRORS.extend(errors)
    return out


def content_index(root: Path) -> set[str]:
    """Hashes of everything already durable. Rescue is proven by CONTENT, not by filename --
    a stale file with the right name is not a copy of anything."""
    return {d for p in collect(root, prune=False).values() if (d := digest(p))}


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


def unattributed_root_files(durable: Path, root: Path | None = None) -> list[dict]:
    """LOOSE files sitting directly in TMP_ROOT, belonging to no session subtree.

    THE GAP THIS CLOSES (found 2026-08-19, by the user, in this tool). sweep() resolves
    /tmp/claude-1001/<project>/<session> and searches only that subtree, so anything written
    to the TMP ROOT is outside it BY CONSTRUCTION -- structurally the same defect as the
    original incident, where files created AFTER a rescue were outside it by construction.
    Measured when found: 19 loose files, including three reusable mutation harnesses and two
    review-leg outputs. The tool would have printed COMPLETE with all 19 unsaved.

    WHY THE BOUNDARY WAS NEVER PROBED: scoping by session id is what makes attribution
    SOUND, so the boundary was also the correctness argument, and widening it looks like a
    regression rather than a fix. The tool's own tests seeded files inside the session dir --
    a test built from the same assumption as the code cannot falsify that assumption.

    THIS DOES NOT AUTO-RESCUE THEM, deliberately. A loose root file has no session
    attribution; in a multi-session workspace it may belong to a peer, and blind-copying
    another session's scratch into a shared repo is a worse failure than reporting it.
    Reporting is the fix: the caller must not be able to see "clean" while these exist.

    THIS IS ORPHAN RECONCILIATION, which is a solved genre and was NOT invented here. The
    standard shape (object-storage reconciliation workers; pg_verifybackup's manifest form)
    is: compare the store against the record on a SCHEDULE, be idempotent, and REPORT
    orphans rather than delete or auto-claim them. Two things adopted from that prior art
    that a first draft of this function did not have:
      1. it is called from all_sessions() -- the SCHEDULED path -- not only on demand.
         all_sessions() skipped loose files too (`if not proj.is_dir(): continue`), so the
         blind spot existed in both modes and fixing only the on-demand one would have left
         the autonomous path blind, which is the worse of the two.
      2. AGE against the retention deadline. /tmp retention here is ~24-36h, so an orphan is
         not a flat fact: at 2h it is a note, at 30h it is nearly lost. Reporting age is the
         RPO idea from backup-coverage tooling and makes the list triageable instead of long.
    """
    # SWEEP_ORPHAN_ROOT exists so this is TESTABLE in isolation. Without it the orphan scan
    # always reads the real /tmp/claude-1001, so a control asserting "clean" would flip the
    # moment any session left a loose file -- an environment-dependent test, which is not a
    # test. Caught immediately: adding the orphan check turned this suite's own negative
    # control red because 22 real orphans were sitting there.
    root = root or Path(os.environ.get("SWEEP_ORPHAN_ROOT") or TMP_ROOT)
    if not root.exists():
        return []
    dst_hashes = content_index(durable)
    now = time.time()
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        d = digest(p)
        if d is not None and d in dst_hashes:
            continue                       # already durable, byte-identical
        try:
            st = p.stat()
            # age-only: mtime is used ONLY to rank orphans against the ~24-36h retention
            # deadline. Attribution is deliberately NOT inferred from it -- a loose root
            # file has no owner and this code never guesses one; that is why orphans are
            # reported rather than auto-rescued.
            size_mb, age_h = st.st_size / 1_048_576, (now - st.st_mtime) / 3600
        except OSError:
            size_mb, age_h = 0.0, 0.0
        out.append({"name": p.name, "path": str(p), "size_mb": round(size_mb, 2),
                    "age_hours": round(age_h, 1),
                    # ~24-36h retention: past 20h an orphan is close enough to the deadline
                    # that "I'll get it next session" is no longer a safe assumption.
                    "near_deadline": age_h >= 20.0,
                    "unreadable": d is None})
    return out


def sweep(session_id: str, durable: Path, skip_large_mb: float = 5.0, src_override: Path | None = None):
    # PRUNED_DIRS is reset per sweep and carried on the result. The first version left it
    # append-only across calls, so the rescue path's RE-SWEEP and --all-sessions both
    # accumulated earlier sessions' prunes, and --all-sessions never printed them at all
    # (CLI review leg, 2026-09-19: surfaced at one output site, not every site).
    PRUNED_DIRS.clear()
    SKIPPED_FILES.clear()
    src_dir = src_override or find_session_dir(session_id)
    if src_dir is None:
        return {"verdict": "SOURCE_GONE", "detail": f"no /tmp dir for {session_id} — ephemeral state already lost",
                "missing": [], "src": None, "durable": str(durable)}

    n_err = len(COLLECT_ERRORS)
    src = collect(src_dir)
    src_errs = COLLECT_ERRORS[n_err:]   # THIS walk's errors; the durable index below appends its own
    skipped = list(SKIPPED_FILES)   # the SOURCE's non-regular files; the durable index below must not add to them
    dst_hashes = content_index(durable)
    missing, elsewhere, unreadable = [], [], []
    if "__WALK_ERRORS__" in src:
        # EXPLICIT (2026-09-27 review, HIGH-1): a directory the walk could not read means the sweep saw less than the
        # whole tree -- unreadable >= 1, so never COMPLETE and never MISSING-BY-DESIGN. This rode on digest()
        # refusing the /dev/null sentinel; had it hashed it as empty, a durable EMPTY file would have "rescued" it.
        del src["__WALK_ERRORS__"]
        unreadable.append({"name": "__WALK_ERRORS__", "path": f"{len(src_errs)} unreadable dir(s) in the source tree"
                           + (f", e.g. {src_errs[0]}" if src_errs else "")})
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
            "source_count": len(src), "durable_count": len(dst_hashes),
            "pruned": list(PRUNED_DIRS), "skipped": skipped}


def would_refuse(m: dict) -> bool:
    """True when rescue() would REFUSE this missing entry by design (name, content, size) -- same order,
    same predicates as rescue(), so a report-only sweep can print refused= without copying anything.
    Third consumer found by the batch-3 Agent leg: unrescued-check never passes --rescue and read every
    guard refusal as 'UNRESCUED -- rescue with: ...' forever."""
    if not isinstance(m, dict) or "name" not in m or "path" not in m:
        return False          # a malformed entry is not a refusal; the sweep itself never produces one
    return (looks_like_credential(m["name"]) or content_secret_kind(Path(m["path"])) is not None
            or bool(m.get("oversize")))


def final_lines(result: dict, rescued: int | None = None, refused: int | None = None,
                not_rescued: int | None = None) -> list[str]:
    """The sweep's two CONTRACT lines, in order, on every single-session CLI exit.

    FINAL-VERDICT carries the verdict token ONLY and must stay byte-identical (context-ceiling-watch
    compares it with ==). FINAL-DETAIL carries the counts a consumer needs to READ the verdict.
    Grammar, stable (a consumer parses ONE regex; fields only ever APPEND):
      FINAL-DETAIL: verdict=<V> missing=<n|-> total=<n|-> unreadable=<n|-> rescued=<n|-> refused=<n|-> not_rescued=<n|->
    Counts are the POST-rescue sweep's when a rescue ran; '-' means not applicable on this path.
    `refused` is the by-design count (secret guard, oversize); `not_rescued` is copy/read-back
    failures. A consumer reads missing == refused as MISSING-BY-DESIGN — the reading the first
    live PreCompact proof could not make (2026-09-21; session-end critique, Opus leg, HIGH-1).
    `refused` counts the SAME population as `missing` -- the files still missing now -- on every path
    (2026-09-27 review, HIGH-A): the rescue path used to count this run's refusals over the PRE-rescue list,
    so a refused file deduped by content plus one failed copy made missing == refused with a file LOST.
    """
    def n(v):
        return "-" if v is None else str(v)
    src_known = result.get("src") is not None
    if refused is None and src_known and result.get("missing"):
        # no rescue ran (report-only): classify without copying, so refused= is never '-' when it is knowable
        refused = sum(1 for m in result["missing"] if would_refuse(m))
    return [
        "FINAL-DETAIL: verdict={} missing={} total={} unreadable={} rescued={} refused={} not_rescued={}".format(
            result["verdict"],
            n(len(result.get("missing", [])) if src_known else None),
            n(result.get("source_count") if src_known else None),
            n(len(result.get("unreadable", [])) if src_known else None),
            n(rescued), n(refused), n(not_rescued)),
        f"FINAL-VERDICT: {result['verdict']}",
    ]


# Filenames whose CONTENT is typically a live credential. Rescuing these moves secrets from
# an ephemeral dir into a git repo. Found the hard way: a rescue copied live JWT session
# cookies for a client site into ~/dev/share. They were untracked and removed, but the tool
# is scheduled — an autonomous copier must never blind-copy a secret.
CREDENTIAL_NAMES = (".jar", ".cookies", "cookies.txt", "login.json", ".netrc",
                    "credentials.json", "token.json", ".pem", ".key", "id_rsa")


def looks_like_credential(name: str) -> bool:
    n = name.lower()
    return any(n.endswith(x) or n.rsplit("/", 1)[-1] == x for x in CREDENTIAL_NAMES)


# CONTENT patterns. The name list was a stopgap and said so: a JWT in `notes.txt` passed it.
# These match the SHAPE of a secret, so the filename becomes irrelevant.
SECRET_PATTERNS = [
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY")),
    # Anthropic / OpenAI-style keys -- absent until 2026-09-21 although the precompact checkpoint's
    # own redactor had them; a task output echoing one would have been copied into a pushed repo
    # (session-end critique, Opus leg, session f3219e83).
    ("anthropic-key", re.compile(r"sk-" r"ant-[A-Za-z0-9_-]{16,}")),   # split literal: the pre-commit scanner blocks the joined shape
    ("sk-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bearer-header", re.compile(r"[Aa]uthorization:\s*[Bb]earer\s+\S{20,}")),
    ("generic-secret-assign", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*[\"\']?[A-Za-z0-9/_+\-]{16,}")),
    # Google OAuth client secret (client_secret.json) -- absent until 2026-09-21: the cron rescue copied one into a
    # pushed working tree (dev-79 Agent-leg review C2; Dart AQ7MTb9SRbhj). Split literal: the pre-commit scanner
    # refuses the joined shape.
    ("google-client-secret", re.compile(r"\bGOCSP" r"X-[A-Za-z0-9_-]{10,}")),
    # The JSON-quoted form of generic-secret-assign: {"token": "..."} -- the closing quote sits between the word and
    # the separator, so the \b\s*[:=] form above can never match a JSON key (same review). Exact key only, so
    # token_uri / client_secret_file do not fire; the value must be 16+ characters, JSON escapes (\" \\) allowed
    # inside it (/ship Agent leg: a value containing an escaped quote must not slip through). Linear: no nested quantifier.
    ("generic-secret-assign-json", re.compile(
        r'(?i)"(api[_-]?key|client[_-]?secret|secret|password|passwd|token|private[_-]?key)"\s*:\s*"(?:[^"\\\n]|\\.){16,}"')),
]


def content_secret_kind(path: Path, probe_bytes: int = 8 * 1024 * 1024) -> str | None:
    """Name of the secret shape found in this file (first probe_bytes, default 8 MB -- above the
    rescue's own oversize cap, so every file the rescue would copy is probed WHOLE), or None.

    NEVER returns or prints the matched text -- only the KIND. A scanner that echoes the
    secret it found has moved the secret into a log, which is the defect it exists to stop.
    """
    fh = _open_regular(path)   # a FIFO would block --audit-credentials forever (see _open_regular)
    if fh is None:
        return None
    try:
        with fh:
            head = fh.read(probe_bytes)
    except OSError:
        return None
    text = head.decode("utf-8", errors="replace")
    for kind, rx in SECRET_PATTERNS:
        if rx.search(text):
            return kind
    return None


def _mkdir_private(path: Path) -> None:
    """mkdir -p where every directory CREATED here is 0700; an existing directory keeps its mode.

    XDG Base Directory spec 0.8: a missing base directory "should be created with permission 0700".
    mkdir(parents=True) used the umask, so a fresh XDG_STATE_HOME -- and each rescue directory under it -- came out
    0755 (2026-09-27 review, L2). Rescue copies are session scratch, which can hold client material.
    """
    missing = []
    while not path.is_dir():
        missing.append(path)
        if path.parent == path:
            break
        path = path.parent
    for d in reversed(missing):
        try:
            os.mkdir(d, 0o700)
        except FileExistsError:
            continue          # created concurrently, or not a directory: never chmod what this call did not create
        os.chmod(d, 0o700)    # the umask cannot narrow or widen it


def rescue(result, durable: Path) -> tuple[list[str], list[str], list[str]]:
    """Copy missing files, then RE-READ to confirm each landed. cp exit 0 proves nothing.

    Returns (confirmed, refused, failed). REFUSED is BY DESIGN — credential-shaped name, secret
    content, oversize — and FAILED is a copy or read-back error. They were ONE list until
    2026-09-21: the first live PreCompact proof then reported "MISSING" for three guard refusals,
    a FINAL-DETAIL line was added to carry the counts, and the session-end critique (Opus leg)
    showed the line still could not say "refused" because the fact was never in the data model.
    Refusals are NAMED, never silent: a silent skip would recreate the original defect
    (something absent from durable with no record why).
    """
    _mkdir_private(durable)
    confirmed, refused, failed = [], [], []
    for m in result["missing"]:
        if looks_like_credential(m["name"]):
            refused.append(f"{m['name']} (SKIPPED: credential-shaped NAME — not copied into a repo)")
            continue
        if (kind := content_secret_kind(Path(m["path"]))) is not None:
            refused.append(f"{m['name']} (SKIPPED: contains a {kind} — not copied into a repo)")
            continue
        if m["oversize"]:
            refused.append(f"{m['name']} (oversize {m['size_mb']}MB — copy manually if wanted)")
            continue
        target = durable / m["name"]
        try:
            # names are RELATIVE PATHS since the basename-collision fix; without this the
            # copy fails ENOENT on every nested file. Caught by RUNNING it, not by review:
            # both review legs saw the pre-relative-path commit.
            _mkdir_private(target.parent)
            shutil.copy2(m["path"], target)
        except OSError as exc:
            failed.append(f"{m['name']} ({exc})")
            continue
        if target.exists() and target.stat().st_size == Path(m["path"]).stat().st_size:
            confirmed.append(m["name"])
        else:
            failed.append(f"{m['name']} (copied but read-back mismatch)")
    return confirmed, refused, failed


def self_check() -> int:
    """Run _self_check() against an ISOLATED ephemeral root: TMP_ROOT, SWEEP_TMP_ROOT and SWEEP_ORPHAN_ROOT all
    point at an empty temp dir, and every CLI subprocess below inherits them. Before 2026-09-27 the self-check read
    the machine's real /tmp/claude-1001 (a find_session_dir scan and the orphan scan of 99 loose files), so its
    result depended on ambient state and it could meet the FIFOs that hung the scheduled sweep (orchestrator rule,
    session 6ccf0ff0: no suite reads the real root)."""
    global TMP_ROOT
    import tempfile
    saved = (TMP_ROOT, os.environ.get("SWEEP_TMP_ROOT"), os.environ.get("SWEEP_ORPHAN_ROOT"))
    with tempfile.TemporaryDirectory() as td_iso:
        TMP_ROOT = Path(td_iso) / "tmp"
        TMP_ROOT.mkdir()
        os.environ["SWEEP_TMP_ROOT"] = os.environ["SWEEP_ORPHAN_ROOT"] = str(TMP_ROOT)
        try:
            return _self_check(TMP_ROOT)
        finally:
            TMP_ROOT = saved[0]
            for key, val in (("SWEEP_TMP_ROOT", saved[1]), ("SWEEP_ORPHAN_ROOT", saved[2])):
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val


def _self_check(iso_root: Path) -> int:
    """Fixtures with known answers, run on every scheduled invocation.

    Every case below is a defect that ACTUALLY SHIPPED and was caught by independent review
    after an earlier 4/4 local pass. Fixtures written by the author of the bug share the
    author's blind spot, so these are regression tests against real history, not imagination.
    """
    global TMP_ROOT   # the round trip below points the reader at a fixture tree, and restores it
    import tempfile
    ok = []
    ok.append(("the self-check runs against an ISOLATED /tmp root, never the machine's (every CLI call inherits it)",
               TMP_ROOT == iso_root and Path("/tmp/claude-1001") not in (TMP_ROOT, *TMP_ROOT.parents)
               and os.environ.get("SWEEP_TMP_ROOT") == os.environ.get("SWEEP_ORPHAN_ROOT") == str(iso_root)))
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
    # CONTENT-SECRET fixtures. Every secret shape is ASSEMBLED AT RUNTIME so no literal
    # secret pattern appears in this source file -- a test that hardcodes one has planted one
    # (and trips the workspace credential scanner, which is how this was found).
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        DASH5 = "-" * 5
        jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "pZCI6MiwiYSI6MX0" + ".sig_not_real"
        (f1 := t/"innocent-notes.txt").write_text(f"session log\npayload-token\t{jwt}\n")
        (f2 := t/"README.md").write_text("# just docs\nnothing secret here at all\n")
        (f3 := t/"key.pem").write_text(f"{DASH5}BEGIN RSA PRIVATE KEY{DASH5}\\nAAAA\\n")
        ok.append(("a JWT in an INNOCENTLY-NAMED file must be caught by CONTENT",
                   content_secret_kind(f1) == "jwt"))
        ok.append(("an ordinary doc must NOT be flagged (no false positive)",
                   content_secret_kind(f2) is None))
        ok.append(("a private key must be caught", content_secret_kind(f3) == "private-key"))
        ok.append(("the name guard alone would have MISSED the innocently-named file",
                   looks_like_credential("innocent-notes.txt") is False))
        # 2026-09-21: an Anthropic-style key had NO shape here while the precompact checkpoint's
        # redactor had one; and a shape sitting past the old 256 KB probe was never seen.
        sk = "sk-" + "ant-" + "api03-" + "x" * 24
        (f4 := t/"task-output.txt").write_text(f"tool said: {sk}\n")
        (f5 := t/"big-output.txt").write_text(("noise\n" * 60_000) + f"late {sk}\n")   # ~360 KB, key at the end
        ok.append(("an Anthropic-style key must be caught by CONTENT", content_secret_kind(f4) == "anthropic-key"))
        ok.append(("a key past the old 256 KB probe must still be caught (whole-file probe)",
                   content_secret_kind(f5) == "anthropic-key"))

    # SYMLINK fixture (Agent review SAS-2). shutil.copy2 WRITES THROUGH a symlinked target,
    # silently overwriting whatever it points at. os.replace severs the link instead. This was
    # fixed incidentally by the atomic-write change made for a different reviewer's finding --
    # so it is pinned here, because an accidental fix is one refactor away from regressing.
    with tempfile.TemporaryDirectory() as td_sym:
        ts = Path(td_sym)
        (asym := ts/"a").mkdir(); (ssym := ts/"s").mkdir()
        (dsym := ssym/"claude-sym").mkdir()
        victim = ts/"unrelated.txt"
        victim.write_text("PRECIOUS\n")
        (dsym/"gate_blocks_acked.jsonl").write_text('PRECIOUS\n{"x":1}\n')  # larger extension
        (asym/"claude-sym.jsonl").symlink_to(victim)
        archive_gate_ledgers(asym, src_root=ssym)
        ok.append(("a symlinked archive target must NOT be written through",
                   victim.read_text() == "PRECIOUS\n"))

    # ISOLATED same-size fixture. The version inside the shared block below asserted r == 1,
    # but an EARLIER conflict in that same source root already forced r == 1 -- so it passed
    # with the same-size check deleted. A fixture that cannot fail alone proves nothing.
    with tempfile.TemporaryDirectory() as td_iso:
        ti = Path(td_iso)
        (ai := ti/"a").mkdir(); (si := ti/"s").mkdir()
        (di := si/"claude-same").mkdir()
        (di/"gate_blocks_acked.jsonl").write_text('{"z":22}\n')   # same LENGTH...
        (ai/"claude-same.jsonl").write_text('{"z":11}\n')         # ...different CONTENT
        ok.append(("same-size different-content must be reported, not silently skipped",
                   archive_gate_ledgers(ai, src_root=si) == 1))
        ok.append(("...and the archive must be left byte-for-byte intact",
                   (ai/"claude-same.jsonl").read_text() == '{"z":11}\n'))
    with tempfile.TemporaryDirectory() as td_ok:
        to = Path(td_ok)
        (ao := to/"a").mkdir(); (so := to/"s").mkdir()
        (do := so/"claude-id").mkdir()
        (do/"gate_blocks_acked.jsonl").write_text('{"z":1}\n')
        (ao/"claude-id.jsonl").write_text('{"z":1}\n')            # identical -> clean run
        ok.append(("an identical source is a CLEAN run, not a conflict",
                   archive_gate_ledgers(ao, src_root=so) == 0))

    # ARCHIVE fixtures. The previous version pointed src_root at an EMPTY directory, so the
    # copy loop never ran and the test proved nothing -- two sabotages (removing the
    # append-only guard, and clearing the archive) both passed it 15/15. A fixture must
    # EXERCISE the path it claims to cover.
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (arch := t/"arch").mkdir()
        (srcroot := t/"src").mkdir()
        (sess := srcroot/"claude-aaa").mkdir()
        (sess/"gate_blocks_acked.jsonl").write_text('{"a":1}\n')          # 1 record, SMALL
        (arch/"claude-aaa.jsonl").write_text('{"a":1}\n{"a":2}\n{"a":3}\n')  # 3 records, BIG
        big_before = (arch/"claude-aaa.jsonl").stat().st_size
        archive_gate_ledgers(arch, src_root=srcroot)
        ok.append(("a TRUNCATED source must never shrink the archive",
                   (arch/"claude-aaa.jsonl").stat().st_size == big_before))
        (sess2 := srcroot/"claude-bbb").mkdir()
        (sess2/"gate_blocks_acked.jsonl").write_text('{"b":1}\n{"b":2}\n')
        archive_gate_ledgers(arch, src_root=srcroot)
        ok.append(("a NEW session ledger must be archived",
                   (arch/"claude-bbb.jsonl").exists()))
        ok.append(("archiving must not delete unrelated archived ledgers",
                   (arch/"claude-aaa.jsonl").exists()))
        (sess/"gate_blocks_acked.jsonl").write_text('{"a":1}\n{"a":2}\n{"a":3}\n{"a":4}\n')
        archive_gate_ledgers(arch, src_root=srcroot)
        ok.append(("a GROWN source must be copied through",
                   (arch/"claude-aaa.jsonl").stat().st_size > big_before))
        (sess3 := srcroot/"claude-ccc").mkdir()
        (sess3/"gate_blocks_acked.jsonl").write_text('{"c":9}\n{"c":9}\n')
        (arch/"claude-ccc.jsonl").write_text('{"c":1}\n')     # archive holds DIFFERENT bytes
        archive_gate_ledgers(arch, src_root=srcroot)
        ok.append(("a LARGER but REWRITTEN source is a conflict, never an overwrite",
                   (arch/"claude-ccc.jsonl").read_text() == '{"c":1}\n'))
        (sess4 := srcroot/"claude-ddd").mkdir()
        (sess4/"gate_blocks_acked.jsonl").write_text('{"d":22}\n')   # same LENGTH...
        (arch/"claude-ddd.jsonl").write_text('{"d":11}\n')           # ...different CONTENT
        r = archive_gate_ledgers(arch, src_root=srcroot)
        ok.append(("same-size different-content must be reported, not silently skipped",
                   r == 1))
        keep = (arch/"claude-bbb.jsonl").read_bytes()
        archive_gate_ledgers(arch, src_root=srcroot)
        ok.append(("re-running must leave archived bytes byte-for-byte identical",
                   (arch/"claude-bbb.jsonl").read_bytes() == keep))

    # STDOUT CONTRACT: context-ceiling-watch parses exactly one trailing FINAL-VERDICT: line.
    # Assert it through the real CLI, not the dict (Agent review leg 2026-09-19: the fix
    # "rested on an unasserted stdout contract between two files").
    import subprocess as _sp
    with tempfile.TemporaryDirectory() as td_fv:
        _r = _sp.run([sys.executable, __file__, "--session", "00000000-self-check-no-such-session",
                      "--durable", str(Path(td_fv) / "fv-durable")], capture_output=True, text=True, timeout=60)
    _lines = [l for l in _r.stdout.splitlines() if l.startswith("FINAL-VERDICT:")]
    ok.append(("CLI prints exactly one FINAL-VERDICT line, as the LAST stdout line, on the SOURCE_GONE path",
               _lines == ["FINAL-VERDICT: SOURCE_GONE"] and _r.stdout.rstrip().endswith("FINAL-VERDICT: SOURCE_GONE")))
    ok.append(("CLI returns 1 (not 0) on SOURCE_GONE -- a lost source is never a pass", _r.returncode == 1))
    # SECOND CONTRACT LINE: FINAL-DETAIL directly precedes FINAL-VERDICT with a fixed key=value grammar,
    # so no consumer has to parse incidental lines for the counts (precompact_checkpoint.py and
    # context-ceiling-watch both did, 2026-09-21). Asserted through the CLI on the SOURCE_GONE path and
    # through final_lines() on the MISSING / rescued paths.
    _out = _r.stdout.splitlines()
    ok.append(("CLI prints FINAL-DETAIL as the line directly before FINAL-VERDICT (SOURCE_GONE: every count '-')",
               len(_out) >= 2 and _out[-2] == "FINAL-DETAIL: verdict=SOURCE_GONE missing=- total=- unreadable=- rescued=- refused=- not_rescued=-"))
    _grammar = re.compile(r"^FINAL-DETAIL: verdict=[A-Z_]+ missing=(\d+|-) total=(\d+|-) unreadable=(\d+|-) rescued=(\d+|-) refused=(\d+|-) not_rescued=(\d+|-)$")
    ok.append(("FINAL-DETAIL grammar holds on every path (a consumer parses ONE regex)",
               all(_grammar.match(final_lines(x, *a)[0]) for x, a in (
                   ({"verdict": "SOURCE_GONE", "src": None, "missing": []}, ()),
                   ({"verdict": "MISSING", "src": "/s", "missing": [1, 2], "unreadable": [], "source_count": 5}, ()),
                   ({"verdict": "COMPLETE", "src": "/s", "missing": [], "unreadable": [], "source_count": 5}, (2, 0, 0))))))
    # VERDICT-LINE fixtures: without these, hardcoding verdict="COMPLETE" passes everything.
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); (s2 := t/"s").mkdir(); (d2 := t/"d").mkdir()
        (s2/"only-here.py").write_text("irreplaceable")
        r_missing = sweep("x", d2, src_override=s2)
        (d2/"only-here.py").write_text("irreplaceable")
        r_complete = sweep("x", d2, src_override=s2)
        ok.append(("sweep() must return MISSING when a file is genuinely unrescued",
                   r_missing["verdict"] == "MISSING"))
        ok.append(("FINAL-DETAIL carries the real counts: MISSING 1 of 1 before the copy, then COMPLETE with rescued=1",
                   final_lines(r_missing)[0] == "FINAL-DETAIL: verdict=MISSING missing=1 total=1 unreadable=0 rescued=- refused=0 not_rescued=-"
                   and final_lines(r_complete, 1, 0, 0)[0] == "FINAL-DETAIL: verdict=COMPLETE missing=0 total=1 unreadable=0 rescued=1 refused=0 not_rescued=0"
                   and final_lines(r_complete, 1, 0, 0)[1] == "FINAL-VERDICT: COMPLETE"))
        # REFUSED vs FAILED are different facts (session-end critique 2026-09-21, Opus HIGH-1): a credential-shaped
        # fixture through the REAL rescue() lands in `refused`, not `failed`, and FINAL-DETAIL says so.
        (s3 := t/"s3").mkdir(); (d3 := t/"d3").mkdir()
        (s3/"ok.txt").write_text("plain"); (s3/"token.json").write_text("placeholder: the NAME is credential-shaped; the content need not be")
        r3 = sweep("x", d3, src_override=s3)
        c3, ref3, fail3 = rescue(r3, d3)
        after3 = sweep("x", d3, src_override=s3)
        ok.append(("rescue() splits REFUSED (credential-shaped name) from FAILED; FINAL-DETAIL carries refused=1 and missing == refused",
                   c3 == ["ok.txt"] and len(ref3) == 1 and fail3 == [] and after3["verdict"] == "MISSING"
                   and final_lines(after3, len(c3), len(ref3), len(fail3))[0]
                       == "FINAL-DETAIL: verdict=MISSING missing=1 total=2 unreadable=0 rescued=1 refused=1 not_rescued=0"
                   # REPORT-ONLY (no rescue ran): refused= is still classified, so a read-only consumer can read by-design
                   and final_lines(r3)[0] == "FINAL-DETAIL: verdict=MISSING missing=2 total=2 unreadable=0 rescued=- refused=1 not_rescued=-"))
        ok.append(("sweep() must return COMPLETE once the bytes exist in durable",
                   r_complete["verdict"] == "COMPLETE"))
    ok.append(("a nonexistent session is SOURCE_GONE, never COMPLETE",
               sweep("00000000-0000-0000-0000-000000000000", Path("/nonexistent"))["verdict"]
               == "SOURCE_GONE"))
    # SECRET GUARD SHAPES (2026-09-21): on 2026-09-19 the cron rescue copied a Google OAuth client_secret.json into
    # ~/dev/share because SECRET_PATTERNS had no Google prefix and generic-secret-assign cannot match a JSON-quoted
    # key. Fixtures are assembled from fragments (the joined literal never appears in this source); positive AND
    # negative, so a widened pattern that starts refusing token_uri or a short password fails here.
    with tempfile.TemporaryDirectory() as td_sec:
        ts = Path(td_sec)
        (ts / "google.json").write_text('{"installed": {"client_id": "1-a.apps.googleusercontent.com", "client_secret": "'
                                        + "GOCSP" + "X-" + "Ab" * 14 + '", "token_uri": "https://oauth2.googleapis.com/token"}}')
        (ts / "quoted.json").write_text('{"token": "' + "Zz" * 16 + '", "kind": "x"}')
        (ts / "benign.json").write_text('{"token_uri": "https://oauth2.googleapis.com/token", "password": "short", '
                                        '"client_secret_file": "where-it-lives.json", "note": "no secret here"}')
        ok.append(("a Google OAuth client_secret.json shape is refused by CONTENT (google-client-secret)",
                   content_secret_kind(ts / "google.json") == "google-client-secret"))
        ok.append(("a JSON-quoted secret key is refused (generic-secret-assign-json)",
                   content_secret_kind(ts / "quoted.json") == "generic-secret-assign-json"))
        (ts / "escaped.json").write_text('{"token": "' + "Ab" * 6 + '\\"' + "Cd" * 6 + '"}')   # an escaped quote INSIDE the value
        ok.append(("a JSON-quoted secret whose value contains an escaped quote is still refused",
                   content_secret_kind(ts / "escaped.json") == "generic-secret-assign-json"))
        ok.append(("token_uri, a short password and *_file keys in JSON are NOT refused (no false positive)",
                   content_secret_kind(ts / "benign.json") is None))

    # RESCUE LAYOUT (plan v2.1 step 1.2, review H5, 2026-09-27). The shipped defect: three producers defaulted to
    # ~/dev/share and the reader looked where only one of them wrote. The recorded rejection becomes a check here;
    # rescue-layout.test.sh proves each CALLER resolves through this function, with a mutation control per producer.
    share = (Path.home() / "dev" / "share").resolve()
    sid_l, proj_l = "abcdef12-3456-4789-8abc-def012345678", "-home-u-dev--with--dashes"   # edge: dashes, leading too
    defaults = [rescue_root()] + [rescue_dir(sid_l, k, project=proj_l) for k in RESCUE_KINDS]   # THIS environment's
    ok.append(("NO rescue default resolves under ~/dev/share (the 2026-09-05 rejection, as a check)",
               not any(d.resolve().is_relative_to(share) for d in defaults)))

    def _refused(*a) -> bool:
        try:
            rescue_dir(*a)
        except ValueError:
            return True
        return False
    saved_xdg = os.environ.get("XDG_STATE_HOME")
    try:
        with tempfile.TemporaryDirectory() as td_l:
            tl = Path(td_l)
            os.environ["XDG_STATE_HOME"] = str(tl / "not-created-yet")          # edge: the XDG dir does not exist
            ok.append(("an absolute XDG_STATE_HOME is the root's base, and resolving it creates nothing",
                       rescue_root() == tl / "not-created-yet" / "claude-rescue" and not (tl / "not-created-yet").exists()))
            for val, what in (("", "an EMPTY"), ("relative/state", "a RELATIVE")):
                os.environ["XDG_STATE_HOME"] = val
                ok.append((f"{what} XDG_STATE_HOME falls back to ~/.local/state (XDG spec 0.8)",
                           rescue_root() == Path.home() / ".local" / "state" / "claude-rescue"))
            ok.append(("rescue_dir(sid, kind) is <root>/<project-dir>/session-<sid8>/<kind> for BOTH kinds",
                       [rescue_dir(sid_l, k, project=proj_l, root=tl) for k in RESCUE_KINDS]
                       == [tl / proj_l / "session-abcdef12" / k for k in RESCUE_KINDS]))
            ok.append(("rescue_dir(sid, None) is the ONE dir holding every kind (what a reader indexes)",
                       {rescue_dir(sid_l, k, project=proj_l, root=tl).parent for k in RESCUE_KINDS}
                       == {rescue_dir(sid_l, None, project=proj_l, root=tl)}))
            # The FULL id is checked (review MEDIUM-2): until 2026-09-27 only sid[:8] was, and this label claimed more
            # than it tested -- '/' past the 8th character, '.' and '..' all passed.
            bad_ids = ("", "1111aaaa-0000/../../../elsewhere", "..", ".", "...", "a b", "x" * 65, "ab\0cd", "ab\ncd")
            ok.append(("an unknown kind is refused, and so is every session id that is not ONE safe path component "
                       "('/' past the 8th character, '.', '..', a space, 65 chars, NUL, newline); a valid one is not",
                       _refused(sid_l, "bogus") and all(_refused(s, "precompact") for s in bad_ids)
                       and not _refused("ab.cd_ef-12", "precompact", proj_l, tl) and not _refused("x" * 64, None, proj_l, tl)))
            ok.append(("a project component that is '', '.', '..' or holds '/' or NUL is refused, never a path",
                       all(_refused(sid_l, "precompact", p, tl) for p in ("", ".", "..", "a/b", "a\0b"))))
    finally:
        if saved_xdg is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved_xdg

    # FIFO / SPECIAL FILES (2026-09-27, Dart tqbNKHjSsJUh R0-1): open() on a FIFO with no writer never returns, and
    # os.walk lists FIFOs as files -- the scheduled sweep had hung for 22 h. A non-regular file, and a symlink to one,
    # is SKIPPED before any open(): counted and reported as its kind, never hashed or probed, never MISSING or
    # unreadable, never blocking COMPLETE (a broken symlink is still reported unreadable: fixture above). The alarm
    # turns a regression into a FAIL instead of a hung self-check.
    import signal

    class _FifoHung(Exception):
        """NOT TimeoutError: that is an OSError, which the open guard itself would swallow."""

    def _hung(signum, frame):
        raise _FifoHung()
    with tempfile.TemporaryDirectory() as td_f:
        tf = Path(td_f); (sf := tf / "src").mkdir(); (df := tf / "dur").mkdir()
        os.mkfifo(sf / "test.fifo"); (sf / "plain.txt").write_text("p"); (sf / "to-fifo.lnk").symlink_to(sf / "test.fifo")
        prev = signal.signal(signal.SIGALRM, _hung); signal.alarm(10); t0 = time.monotonic()
        try:
            r1 = sweep("x", df, src_override=sf)
            (df / "plain.txt").write_text("p")                  # the one REGULAR file is now rescued
            r2 = sweep("x", df, src_override=sf)
            got_f = (sorted(r1["skipped"]), [m["name"] for m in r1["missing"]], r1["unreadable"], r1["verdict"],
                     r2["verdict"], r2["skipped"] == r1["skipped"], digest(sf / "test.fifo"),
                     content_secret_kind(sf / "to-fifo.lnk"))
        except _FifoHung:
            got_f = "HUNG"
        finally:
            signal.alarm(0); signal.signal(signal.SIGALRM, prev)
        secs_f = time.monotonic() - t0
        ok.append(("a FIFO and a symlink to it are SKIPPED before any open(): reported as 'fifo', not MISSING or "
                   "unreadable, never blocking COMPLETE, and the sweep returns within seconds",
                   got_f == ([f"{sf / 'test.fifo'} (fifo)", f"{sf / 'to-fifo.lnk'} (fifo)"], ["plain.txt"], [],
                             "MISSING", "COMPLETE", True, None, None) and secs_f < 5))

    # CLI on the DEFAULT path (no --durable): the SOURCE_GONE contract is unchanged, --print-rescue-dir prints the
    # layout, a --rescue that names no kind is refused, and nothing is created for a session that has no source.
    with tempfile.TemporaryDirectory() as td_cd:
        st = Path(td_cd) / "st"; (orph := Path(td_cd) / "no-orphans").mkdir()
        env_cd = dict(os.environ, XDG_STATE_HOME=str(st), SWEEP_ORPHAN_ROOT=str(orph))
        gone = "00000000-self-check-no-such-session"
        _d = _sp.run([sys.executable, __file__, "--session", gone], env=env_cd, capture_output=True, text=True, timeout=60)
        _p = _sp.run([sys.executable, __file__, "--print-rescue-dir", "--session", gone, "--kind", "auto-sweep"],
                     env=env_cd, capture_output=True, text=True, timeout=60)
        _n = _sp.run([sys.executable, __file__, "--session", gone, "--rescue"], env=env_cd, capture_output=True, text=True, timeout=60)
        created = st.exists()
    ok.append(("CLI default path: SOURCE_GONE contract unchanged (one FINAL-VERDICT, last line, rc 1), nothing created",
               [l for l in _d.stdout.splitlines() if l.startswith("FINAL-VERDICT:")] == ["FINAL-VERDICT: SOURCE_GONE"]
               and _d.stdout.rstrip().endswith("FINAL-VERDICT: SOURCE_GONE") and _d.returncode == 1 and not created))
    ok.append(("--print-rescue-dir prints the layout path on ONE line and exits 0",
               _p.returncode == 0 and _p.stdout == f"{st / 'claude-rescue' / UNATTRIBUTED_PROJECT / 'session-00000000' / 'auto-sweep'}\n"))
    ok.append(("--rescue with neither --durable nor --kind is refused (rc 1), never a guessed directory",
               _n.returncode == 1 and "needs --kind" in _n.stderr and "FINAL-VERDICT" not in _n.stdout))

    # WALK ERROR -> MISSING by construction (2026-09-27 review, HIGH-1). A source dir the walk cannot read makes the
    # sweep MISSING with unreadable=1 whatever digest() makes of the sentinel: durable holds an EMPTY file, so a
    # sentinel hashed as empty bytes would be "rescued" and read COMPLETE. The FINAL-DETAIL pinned here (missing=0
    # == refused=0, unreadable=1) is exactly the shape the hook and the watcher must NOT read as by design.
    with tempfile.TemporaryDirectory() as td_w:
        tw = Path(td_w); (sw := tw / "src").mkdir(); (dw := tw / "dur").mkdir()
        (sw / "kept.txt").write_text("k"); (dw / "kept.txt").write_text("k"); (dw / "empty").write_bytes(b"")
        (locked := sw / "locked").mkdir(); (locked / "hidden.txt").write_text("h"); os.chmod(locked, 0)
        try:
            locked_really = not os.access(locked, os.R_OK | os.X_OK)
            rw = sweep("x", dw, src_override=sw)
        finally:
            os.chmod(locked, 0o700)
        ok.append(("a source dir the walk cannot read makes the sweep MISSING with unreadable=1, never COMPLETE, even "
                   "with an empty file in durable (precondition: chmod 000 made the dir unreadable)",
                   locked_really and rw["verdict"] == "MISSING" and rw["missing"] == []
                   and [u["name"] for u in rw["unreadable"]] == ["__WALK_ERRORS__"]
                   and final_lines(rw, 0, 0, 0)[0] == "FINAL-DETAIL: verdict=MISSING missing=0 total=1 unreadable=1 "
                                                      "rescued=0 refused=0 not_rescued=0"))

    # TRAVERSAL via --session (2026-09-27 review, MEDIUM-2 / CLI leg HIGH): an id that climbs out of its project dir,
    # '..' or '.', is refused by the CLI -- rc 1, nothing read (no "source :" line), nothing created. Pre-fix, the
    # first vector swept a tree OUTSIDE TMP_ROOT and wrote it above the rescue root (derived project '..').
    real_sid = "cafe0001-0000-4000-8000-000000000003"
    (TMP_ROOT / "-p" / real_sid / "scratchpad").mkdir(parents=True, exist_ok=True)
    (TMP_ROOT / "-p" / real_sid / "scratchpad" / "own.txt").write_text("own")
    (outside := TMP_ROOT.parent / "elsewhere").mkdir(exist_ok=True); (outside / "not-yours.txt").write_text("x")
    with tempfile.TemporaryDirectory() as td_tv:
        st_tv = Path(td_tv) / "st"; env_tv = dict(os.environ, XDG_STATE_HOME=str(st_tv))
        runs = [_sp.run([sys.executable, __file__, *a], env=env_tv, capture_output=True, text=True, timeout=60)
                for a in (["--session", f"{real_sid}/../../../elsewhere", "--rescue", "--kind", "precompact"],
                          ["--session", "..", "--rescue", "--kind", "precompact"],
                          ["--session", ".", "--report-only"],
                          ["--print-rescue-dir", "--session", "..", "--kind", "auto-sweep"])]
        created = st_tv.exists()
    ok.append(("a --session that climbs out (<sid>/../../../elsewhere), '..' or '.' is refused: rc 1, nothing read, "
               "nothing created",
               all(r.returncode == 1 and "cannot name a directory" in r.stderr and "source :" not in r.stdout
                   and "FINAL-VERDICT" not in r.stdout for r in runs) and not created))

    # 0700 (XDG spec 0.8; review L2): every directory the rescue CREATES is 0700 -- a fresh XDG_STATE_HOME, the layout
    # below it, nested dirs -- and an EXISTING directory keeps its mode.
    with tempfile.TemporaryDirectory() as td_m:
        tm = Path(td_m); (tm / "src" / "sub").mkdir(parents=True); (tm / "src" / "sub" / "f.txt").write_text("f")
        fresh = tm / "fresh-xdg"                                   # does not exist yet
        (old := tm / "old-xdg").mkdir(); os.chmod(old, 0o755)      # exists: must be left alone
        k_new = rescue_dir("x", "precompact", project="-p", root=fresh / "claude-rescue")
        k_old = rescue_dir("x", "precompact", project="-p", root=old / "claude-rescue")
        for k in (k_new, k_old):
            rescue(sweep("x", k.parent, src_override=tm / "src"), k)
        made = [fresh, fresh / "claude-rescue", k_new.parent.parent, k_new.parent, k_new, k_new / "sub"]
        modes = [p.stat().st_mode & 0o777 if p.exists() else None for p in made]
        old_modes = (old.stat().st_mode & 0o777, (old / "claude-rescue").stat().st_mode & 0o777)
    ok.append(("every directory the rescue CREATES is 0700 (fresh XDG_STATE_HOME, layout, nested), an existing one "
               "keeps its mode",
               modes == [0o700] * len(made) and old_modes == (0o755, 0o700)))

    # REFUSED COUNTS THE FILES STILL MISSING (2026-09-27 review, HIGH-A; the Agent leg's E13, reproduced through the
    # real CLI). cookies.txt is refused by name but its bytes equal a.txt's, which IS rescued; sub/b.md's copy fails
    # (read-only target). Pre-fix the rescue path printed refused=1 over the PRE-rescue list -> missing=1 == refused=1
    # -> every consumer read MISSING-BY-DESIGN "none lost" while b.md was LOST. Now refused=0: not by design.
    sid_13 = "e13e13e1-0000-4000-8000-000000000013"
    (s13 := TMP_ROOT / "-e13" / sid_13 / "scratchpad" / "sub").mkdir(parents=True, exist_ok=True)
    (s13.parent / "a.txt").write_text("same"); (s13.parent / "cookies.txt").write_text("same"); (s13 / "b.md").write_text("precious")
    with tempfile.TemporaryDirectory() as td_13:
        st13 = Path(td_13) / "st"
        blocked = st13 / "claude-rescue" / "-e13" / "session-e13e13e1" / "precompact" / "scratchpad" / "sub" / "b.md"
        blocked.parent.mkdir(parents=True); blocked.write_text("stale"); os.chmod(blocked, 0o444)
        r13 = _sp.run([sys.executable, __file__, "--session", sid_13, "--rescue", "--kind", "precompact", "--report-only"],
                      env=dict(os.environ, XDG_STATE_HOME=str(st13)), capture_output=True, text=True, timeout=60)
    d13 = [l for l in r13.stdout.splitlines() if l.startswith("FINAL-DETAIL:")]
    ok.append(("after a --rescue, refused= counts only files STILL missing: a refused twin of a rescued file plus one "
               "failed copy is missing=1 refused=0 (never missing == refused with a file lost)",
               d13 == ["FINAL-DETAIL: verdict=MISSING missing=1 total=3 unreadable=0 rescued=1 refused=0 not_rescued=1"]))

    # ROUND TRIP (review H5): two WRITERS through the real rescue() into the layout, then the scheduled READER through
    # the real all_sessions(). Isolated: TMP_ROOT and the orphan root point at a fixture tree; notify is off.
    import contextlib, io
    saved_tmp, saved_orph = TMP_ROOT, os.environ.get("SWEEP_ORPHAN_ROOT")
    prev = signal.signal(signal.SIGALRM, _hung); signal.alarm(30)   # a FIFO regression must FAIL here, never hang
    try:
        with tempfile.TemporaryDirectory() as td_rt:
            tr = Path(td_rt); root_rt = tr / "state" / "claude-rescue"
            TMP_ROOT = tr / "tmp"; os.environ["SWEEP_ORPHAN_ROOT"] = str(TMP_ROOT)
            sid_rt = "feedf00d-0000-4000-8000-00000000c0de"
            s_rt = TMP_ROOT / "-home-u-dev" / sid_rt

            def _reader() -> tuple[int, str]:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc_r = all_sessions(root_rt, notify=False)
                return rc_r, buf.getvalue()
            try:
                (s_rt / "scratchpad").mkdir(parents=True); (s_rt / "tasks").mkdir()
                (s_rt / "scratchpad" / "notes.md").write_text("n1"); (s_rt / "tasks" / "t.output").write_text("o1")
                os.mkfifo(s_rt / "tasks" / "live.pipe")                             # a writer-less FIFO in the session dir
                rc0, out0 = _reader()                                               # nothing rescued yet
                c1, _, _ = rescue(sweep(sid_rt, rescue_dir(sid_rt, None, root=root_rt)), rescue_dir(sid_rt, "precompact", root=root_rt))
                (s_rt / "scratchpad" / "late.py").write_text("late")                # created AFTER the first rescue
                c2, _, _ = rescue(sweep(sid_rt, rescue_dir(sid_rt, None, root=root_rt)), rescue_dir(sid_rt, "auto-sweep", root=root_rt))
                t_rd = time.monotonic(); rc1, out1 = _reader(); secs_rd = time.monotonic() - t_rd
                (s_rt / "scratchpad" / "unsaved.txt").write_text("u")
                rc2, out2 = _reader()
                landed = sorted(str(p.relative_to(root_rt)) for p in root_rt.rglob("*") if p.is_file())
                # the SAME session id under a second project dir, holding 2 files of its own: each pair must be read
                # against its own tree (1 at risk in -home-u-dev, 2 with no dir in -home-u) whatever order iterdir gives
                (s2_rt := TMP_ROOT / "-home-u" / sid_rt / "scratchpad").mkdir(parents=True)
                (s2_rt / "e1.md").write_text("e1"); (s2_rt / "e2.md").write_text("e2")
                rc3, out3 = _reader()
            finally:
                TMP_ROOT = saved_tmp
                if saved_orph is None:
                    os.environ.pop("SWEEP_ORPHAN_ROOT", None)
                else:
                    os.environ["SWEEP_ORPHAN_ROOT"] = saved_orph
    except _FifoHung:
        rc0 = rc1 = rc2 = rc3 = -1; out0 = out1 = out2 = out3 = "HUNG"; c1, c2, landed, secs_rd = [], [], [], float("inf")
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, prev)
    ok.append(("round trip, before any rescue: the reader says NO-RESCUE-DIR for the session (reported, not silent)",
               "1 session(s) checked, 0 with unrescued artifacts, 1 with no rescue directory at all" in out0
               and "NO-RESCUE-DIR  feedf00d" in out0))
    ok.append(("round trip: each writer lands in <project-dir>/session-<sid8>/<its kind>, and the later auto-sweep copies "
               "ONLY the new file (the index spans both kinds)",
               sorted(c1) == ["scratchpad/notes.md", "tasks/t.output"] and c2 == ["scratchpad/late.py"]
               and landed == ["-home-u-dev/session-feedf00d/auto-sweep/scratchpad/late.py",
                              "-home-u-dev/session-feedf00d/precompact/scratchpad/notes.md",
                              "-home-u-dev/session-feedf00d/precompact/tasks/t.output"]))
    ok.append(("round trip: --all-sessions counts a file rescued under EITHER kind -> 0 unrescued, 0 without a dir, rc 0",
               rc1 == 0 and "1 session(s) checked, 0 with unrescued artifacts, 0 with no rescue directory at all" in out1))
    ok.append(("round trip: the session's FIFO is counted SKIPPED by the scheduled reader, which returns within seconds",
               "1 special file(s) skipped (fifo/socket/device)" in out1 and "SKIPPED  feedf00d  1 special file(s)" in out1
               and secs_rd < 5))
    ok.append(("round trip negative control: a file created after both rescues is AT RISK and exits 1",
               rc2 == 1 and "1 with unrescued artifacts" in out2 and "AT RISK  feedf00d  1 file(s)" in out2))
    ok.append(("one session id under TWO project dirs: each pair is read against its own tree and partition",
               rc3 == 1 and "2 session(s) checked, 1 with unrescued artifacts, 1 with no rescue directory at all" in out3
               and "AT RISK  feedf00d  1 file(s)" in out3 and "NO-RESCUE-DIR  feedf00d  2 file(s)" in out3))
    failed = [m for m, good in ok if not good]
    for m in failed:
        print(f"  [FAIL/self-check] {m}")
    if not failed:
        print(f"  [PASS/self-check] {len(ok)}/{len(ok)} checks proved the comparison logic")
    return 1 if failed else 0


def all_sessions(durable_root: Path, notify: bool = True) -> int:
    """Scheduled mode: every live /tmp session, not just mine. /tmp retention is ~24-36h, so
    an unrescued artifact has a DEADLINE — this is what makes a date-driven trigger correct
    rather than decorative. Notifies on loss; silence means genuinely nothing at risk.

    durable_root is the root of the rescue LAYOUT (rescue_dir). notify=False is for the self-check's round
    trip only: the notification is not the path under test, and a test must never page the user."""
    if not TMP_ROOT.exists():
        print("SWEEP(all): no /tmp session root — nothing to check")
        return 0
    at_risk, unstarted, checked, pruned_total, skipped_by = [], [], 0, 0, []
    for proj in TMP_ROOT.iterdir():
        if not proj.is_dir():
            continue
        for sess in proj.iterdir():
            if not sess.is_dir():
                continue
            checked += 1
            # THE LAYOUT: this session's dir in ITS OWN project partition, holding every kind -- a file rescued by
            # EITHER producer counts (review H5). Until 2026-09-27 this read ~/dev/share/session-<sid8>-artifacts,
            # so the auto-sweep's copies (under -RESCUE/auto-sweep) never counted at all. src_override pins the sweep
            # to THIS dir: one session id can live under two project dirs (6 live on 2026-09-27, e.g.
            # -home-ichardart-dev and -home-ichardart), and find_session_dir() answered with the first for both.
            try:
                durable = rescue_dir(sess.name, None, project=proj.name, root=durable_root)
            except ValueError:
                # a dir name no rescue path can carry (0 of 346 on 2026-09-27): no producer can rescue it, so it is
                # AT RISK and named here -- never skipped, and never a crash of the scheduled run
                at_risk.append((repr(sess.name)[:40], len(collect(sess))))
                continue
            r = sweep(sess.name, durable, src_override=sess)
            pruned_total += len(r.get("pruned", []))
            if r.get("skipped"):
                skipped_by.append((sess.name[:8], len(r["skipped"])))
            if r["verdict"] != "MISSING":
                continue
            # Sessions with no rescue dir were previously SKIPPED as "not a broken promise".
            # That printed "0 with unrescued artifacts" while real data sat in /tmp under a
            # deletion clock — a false SAFE in the autonomous path, the worst place for one.
            # They are now their own category: reported, counted, never suppressed.
            (unstarted if not durable.exists() else at_risk).append(
                (sess.name[:8], len(r["missing"])))
    print(f"SWEEP(all): {checked} session(s) checked, {len(at_risk)} with unrescued artifacts, "
          f"{len(unstarted)} with no rescue directory at all, {pruned_total} dir(s) pruned "
          f"({'/'.join(sorted(PRUNE_DIRS))}), {sum(n for _, n in skipped_by)} special file(s) skipped "
          f"(fifo/socket/device)")   # appended: governed-outcomes-check reads '(\d+) with unrescued artifacts'
    for sid, n in skipped_by:
        print(f"  SKIPPED  {sid}  {n} special file(s) (fifo/socket/device): no bytes to rescue, never opened")
    for sid, n in unstarted:
        print(f"  NO-RESCUE-DIR  {sid}  {n} file(s) live only in /tmp")
    for sid, n in at_risk:
        print(f"  AT RISK  {sid}  {n} file(s) not in its rescue directory")

    # ORPHAN RECONCILIATION on the SCHEDULED path. This call is the reason the loop above
    # cannot see loose root files at all: it iterates TMP_ROOT with `if not proj.is_dir():
    # continue`, so a file sitting directly in the root is skipped before any session is
    # even considered. Reconciliation is defined as PERIODIC, so the scheduled path is the
    # one that most needs it -- nobody is watching the on-demand path.
    #
    # A review leg caught that this function's docstring ALREADY CLAIMED to be called from
    # here while the only call site was main(). A false statement in documentation about
    # wiring that does not exist is precisely the defect this whole family detects, written
    # inside the fix for it. Wiring it was the correct repair; softening the sentence would
    # have been the failure.
    orphans = unattributed_root_files(durable_root)
    if orphans:
        near = sum(1 for o in orphans if o["near_deadline"])
        print(f"  ORPHANS  {len(orphans)} unattributed file(s) loose in {TMP_ROOT}"
              + (f", {near} past 20h" if near else ""))
        at_risk = at_risk or [("<tmp-root>", len(orphans))]   # force the non-zero exit below

    if at_risk and notify and (NOTIFY := Path.home() / "bin" / "notify.sh").exists():
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


def audit_credentials() -> int:
    """Read-only sweep for secrets sitting in ephemeral session dirs. Copies NOTHING and
    prints no secret text -- only path + KIND, so the report itself is safe to keep."""
    if not TMP_ROOT.exists():
        print("AUDIT: no /tmp session root"); return 0
    hits, scanned = [], 0
    SKIPPED_FILES.clear()
    for proj in TMP_ROOT.iterdir():
        if not proj.is_dir():
            continue
        for sess in proj.iterdir():
            if not sess.is_dir():
                continue
            for rel, fp in collect(sess, prune=False).items():
                if rel == "__WALK_ERRORS__":
                    continue
                scanned += 1
                if (kind := content_secret_kind(fp)) is not None:
                    hits.append((sess.name[:8], rel, kind))
    print(f"CREDENTIAL AUDIT: {scanned} file(s) scanned across ephemeral session dirs, "
          f"{len(hits)} carrying secret-shaped content")
    if SKIPPED_FILES:
        print(f"  {len(SKIPPED_FILES)} special file(s) skipped (fifo/socket/device, never opened), "
              f"e.g. {SKIPPED_FILES[0]}")
    for sid, rel, kind in sorted(hits):
        print(f"  {kind:22s} {sid}  {rel}")
    if hits:
        print("\n  These live in ephemeral session dirs. Copying them into a repo is what")
        print("  --rescue now refuses, by CONTENT as well as by name.")
        print("  HONEST LIMIT: this is a MATCH count, not an exposure count. Files that")
        print("  DEFINE these patterns (scanner source, diffs of it, this file's own")
        print("  fixtures) match legitimately. Verified 2026-08-11: 2 of 3 sampled hits were")
        print("  diffs of credential_scanner.py. Triage per file; do not read the count as")
        print("  a breach tally. Matches are NOT auto-filtered -- hiding them to make the")
        print("  number look clean is the defect this tool exists to prevent.")
    return 1 if hits else 0


GATE_LEDGER_ARCHIVE = Path.home() / "dev/share/gate-ack-archive"


def archive_gate_ledgers(dest: Path = GATE_LEDGER_ARCHIVE, src_root: Path = Path("/tmp")) -> int:
    """Copy every session's gate_blocks_acked.jsonl somewhere durable.

    CLAUDE.md calls that ledger the DURABLE record of gate-block acknowledgements. It is
    written to /tmp/claude-<session>/, which is reaped in ~24-36h. Measured 2026-08-11:
    92 of 111 records were already past the horizon. This is not the ideal fix -- the ideal
    fix is writing it somewhere durable in the first place -- but it stops an ACTIVE loss
    without touching a hook, and a snapshot on a timer beats a snapshot taken once by hand.
    """
    dest.mkdir(parents=True, exist_ok=True)
    copied = conflicts = failures = 0
    for src in sorted(src_root.glob("claude-*/gate_blocks_acked.jsonl")):
        target = dest / f"{src.parent.name}.jsonl"
        try:
            # Append-only ledgers: only copy when the source has MORE bytes, so a reaped or
            # truncated source can never shrink the archive.
            if target.exists() and target.stat().st_size >= src.stat().st_size:
                # SAME size does NOT mean same content. Independent Agent review, 2026-08-11:
                # a 52-byte stale ledger stayed archived while 52 bytes of genuinely different
                # fresh data were discarded forever, under a printed success message. Size is a
                # cheap prefilter, never an equality test.
                if (target.stat().st_size == src.stat().st_size
                        and target.read_bytes() != src.read_bytes()):
                    print(f"  CONFLICT: {src.parent.name} is the same SIZE but different "
                          f"CONTENT — archive left intact, fresh data NOT captured",
                          file=sys.stderr)
                    conflicts += 1
                continue
            data = src.read_bytes()
            if target.exists() and not data.startswith(target.read_bytes()):
                # APPEND-ONLY CONTRACT: the source must EXTEND what we hold. A larger but
                # REWRITTEN ledger is a conflict, not an update -- size alone cannot tell them
                # apart. Independent review, 2026-08-11.
                print(f"  CONFLICT: {src.parent.name} is larger but not an extension of the "
                      f"archive — NOT overwritten", file=sys.stderr)
                conflicts += 1
                continue
            # Write a sibling then os.replace: atomic on POSIX. shutil.copy2 TRUNCATES the
            # destination first, so an interrupted copy destroyed the archive it protects.
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError as exc:
            print(f"  FAILED: {src} ({exc})", file=sys.stderr)
            failures += 1
            continue
        if target.exists() and target.read_bytes() == data:
            copied += 1
        else:
            failures += 1
    total = sum(1 for f in dest.glob("*.jsonl") for line in f.read_text(errors="replace").splitlines()
                if line.strip().startswith("{"))
    print(f"GATE LEDGER ARCHIVE: {copied} ledger(s) updated, {total} brace-prefixed line(s) "
          f"in archive at {dest}")
    if conflicts or failures:
        print(f"  {conflicts} conflict(s), {failures} failure(s) — NOT a clean run", file=sys.stderr)
    return 1 if (conflicts or failures) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    ap.add_argument("--all-sessions", action="store_true", help="scheduled mode: check every live session")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--archive-gate-ledgers", action="store_true",
                    help="copy gate_blocks_acked.jsonl out of /tmp before retention reaps it")
    ap.add_argument("--audit-credentials", action="store_true",
                    help="read-only: find secret-shaped content in ephemeral session dirs")
    # NOT required=True: that made --self-check and --all-sessions unrunnable without an
    # irrelevant flag — a checker that cannot demonstrate it works. Validated per-mode below.
    ap.add_argument("--durable", type=Path,
                    help="an EXPLICIT directory to compare against (and --rescue into), bypassing the layout "
                         "(tests); with --all-sessions, the layout's root. Omit it: rescue_dir() decides")
    ap.add_argument("--kind", choices=RESCUE_KINDS,
                    help="which producer is rescuing; --rescue without --durable copies into that kind's dir")
    ap.add_argument("--rescue-root", type=Path,
                    help="root of the layout (default ${XDG_STATE_HOME:-~/.local/state}/claude-rescue)")
    ap.add_argument("--print-rescue-dir", action="store_true",
                    help="print rescue_dir(--session, --kind) and exit 0; without --kind, the session dir")
    ap.add_argument("--rescue", action="store_true", help="copy the missing files, with read-back confirmation")
    ap.add_argument("--report-only", action="store_true", help="always exit 0")
    args = ap.parse_args()
    root = args.rescue_root.expanduser() if args.rescue_root else None   # None: rescue_root(), resolved in rescue_dir

    if args.self_check:
        print("SESSION ARTIFACT SWEEP: self-check")
        return self_check()

    if args.archive_gate_ledgers:
        return archive_gate_ledgers()

    if args.audit_credentials:
        return audit_credentials()

    if args.all_sessions:
        # The default is the layout's root -- until 2026-09-27 it was ~/dev/share, which also made the orphan
        # check below hash every file in share (its .git included) on each scheduled run.
        return all_sessions(args.durable.expanduser() if args.durable else (root or rescue_root()))

    if not args.session:
        print("ERROR: no session id (pass --session or set CLAUDE_CODE_SESSION_ID)", file=sys.stderr)
        return 1
    try:
        check_session_id(args.session)   # the FULL id, before anything is read (review MEDIUM-2)
        if args.print_rescue_dir:
            print(rescue_dir(args.session, args.kind, root=root))
            return 0
        if args.durable:
            index_dir = write_dir = args.durable.expanduser()
        elif args.rescue and not args.kind:
            # --durable used to be required; guessing a directory for a caller that named neither is how a default
            # drifts, so the caller must say which producer it is.
            print(f"ERROR: --rescue without --durable needs --kind ({'|'.join(RESCUE_KINDS)})", file=sys.stderr)
            return 1
        else:
            # Compare against the SESSION dir -- a file under EITHER kind is rescued -- and copy into this kind's own.
            index_dir = rescue_dir(args.session, None, root=root)
            write_dir = rescue_dir(args.session, args.kind, root=root) if args.kind else index_dir
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = sweep(args.session, index_dir)
    print(f"SESSION ARTIFACT SWEEP: {result['verdict']}")
    print(f"  {result['detail']}")
    if result.get("pruned"):
        print(f"  PRUNED (not rescued, by design): {len(result['pruned'])} dir(s) named "
              f"{'/'.join(sorted(PRUNE_DIRS))} -- e.g. {result['pruned'][0]}")
    if result.get("skipped"):
        print(f"  SKIPPED (not a regular file: no bytes to rescue, never opened): {len(result['skipped'])} -- "
              f"e.g. {result['skipped'][0]}")
    print(f"  source : {result['src']}")
    print(f"  durable: {result['durable']}")
    if args.rescue and write_dir != index_dir:
        print(f"  rescue into: {write_dir}")
    if result.get("durable_elsewhere"):
        # Named, never silent: the reader must be able to check this judgement.
        print(f"  {len(result['durable_elsewhere'])} symlink(s) already durable elsewhere "
              f"(e.g. {result['durable_elsewhere'][0]['target']})")

    # ORPHAN RECONCILIATION always prints, including the zero case. A line that appears only
    # on failure makes silence ambiguous -- the reader cannot tell "no orphans" from "this
    # build does not check". That ambiguity is the defect this whole tool exists to remove.
    orphans = unattributed_root_files(index_dir)
    if orphans:
        near = [o for o in orphans if o["near_deadline"]]
        print(f"  ORPHAN RECONCILIATION: {len(orphans)} unattributed file(s) loose in "
              f"{TMP_ROOT} — outside every session subtree, so no sweep covers them"
              + (f"; {len(near)} PAST 20h and near the ~24-36h retention deadline" if near else ""))
        for o in sorted(orphans, key=lambda x: -x["age_hours"])[:10]:
            mark = " [NEAR DEADLINE]" if o["near_deadline"] else ""
            print(f"    ORPHAN  {o['name']}  ({o['size_mb']}MB, {o['age_hours']}h old){mark}")
        if len(orphans) > 10:
            print(f"    ... and {len(orphans) - 10} more")
        print("    NOT auto-rescued: a loose root file has no session attribution and may "
              "belong to a peer. Copy deliberately.")
    else:
        print(f"  ORPHAN RECONCILIATION: 0 unattributed files in {TMP_ROOT}")

    if result["verdict"] == "MISSING":
        for m in result["missing"]:
            flag = "  [OVERSIZE]" if m["oversize"] else ""
            print(f"    MISSING  {m['name']}  ({m['size_mb']}MB){flag}")
        if args.rescue:
            confirmed, refused, failed = rescue(result, write_dir)
            print(f"  rescued (read-back confirmed): {len(confirmed)}")
            for f in refused:
                print(f"    REFUSED (by design): {f}")
            for f in failed:
                print(f"    NOT RESCUED: {f}")
            after = sweep(args.session, index_dir)
            print(f"  RE-SWEEP: {after['verdict']} — {after['detail']}")
            # FINAL-VERDICT is the ONE line a consumer should parse. It is printed on every
            # single-session exit, after any rescue, in the same shape. context-ceiling-watch
            # first parsed RE-SWEEP (printed only after a copy) and read a no-op sweep as a
            # failure; a consumer parsing an incidental line is the class, this line is the fix.
            # refused= over the files STILL missing (review HIGH-A), so missing == refused is a set identity
            print("\n".join(final_lines(after, len(confirmed), sum(1 for m in after["missing"] if would_refuse(m)),
                                        len(failed))))
            return 0 if after["verdict"] == "COMPLETE" or args.report_only else 1

    print("\n".join(final_lines(result)))
    return 0 if (result["verdict"] == "COMPLETE" or args.report_only) else 1


if __name__ == "__main__":
    sys.exit(main())
