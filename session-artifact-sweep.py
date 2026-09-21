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
import re
import os
import shutil
import subprocess
import time
import sys
from pathlib import Path

TMP_ROOT = Path("/tmp/claude-1001")

# Directory names never rescued: fixture repos, dependency dumps, bytecode. Surfaced, not silent.
PRUNE_DIRS = {".git", "node_modules", "__pycache__"}
PRUNED_DIRS: list[str] = []
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
            "source_count": len(src), "durable_count": len(dst_hashes),
            "pruned": list(PRUNED_DIRS)}


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
]


def content_secret_kind(path: Path, probe_bytes: int = 8 * 1024 * 1024) -> str | None:
    """Name of the secret shape found in this file (first probe_bytes, default 8 MB -- above the
    rescue's own oversize cap, so every file the rescue would copy is probed WHOLE), or None.

    NEVER returns or prints the matched text -- only the KIND. A scanner that echoes the
    secret it found has moved the secret into a log, which is the defect it exists to stop.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(probe_bytes)
    except OSError:
        return None
    text = head.decode("utf-8", errors="replace")
    for kind, rx in SECRET_PATTERNS:
        if rx.search(text):
            return kind
    return None


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
    durable.mkdir(parents=True, exist_ok=True)
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
            target.parent.mkdir(parents=True, exist_ok=True)
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
    at_risk, unstarted, checked, pruned_total = [], [], 0, 0
    for proj in TMP_ROOT.iterdir():
        if not proj.is_dir():
            continue
        for sess in proj.iterdir():
            if not sess.is_dir():
                continue
            checked += 1
            durable = durable_root / f"session-{sess.name[:8]}-artifacts"
            r = sweep(sess.name, durable)
            pruned_total += len(r.get("pruned", []))
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
          f"({'/'.join(sorted(PRUNE_DIRS))})")
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


def audit_credentials() -> int:
    """Read-only sweep for secrets sitting in ephemeral session dirs. Copies NOTHING and
    prints no secret text -- only path + KIND, so the report itself is safe to keep."""
    if not TMP_ROOT.exists():
        print("AUDIT: no /tmp session root"); return 0
    hits, scanned = [], 0
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
    ap.add_argument("--durable", type=Path, help="the rescue directory to compare against")
    ap.add_argument("--rescue", action="store_true", help="copy the missing files, with read-back confirmation")
    ap.add_argument("--report-only", action="store_true", help="always exit 0")
    args = ap.parse_args()

    if args.self_check:
        print("SESSION ARTIFACT SWEEP: self-check")
        return self_check()

    if args.archive_gate_ledgers:
        return archive_gate_ledgers()

    if args.audit_credentials:
        return audit_credentials()

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
    if result.get("pruned"):
        print(f"  PRUNED (not rescued, by design): {len(result['pruned'])} dir(s) named "
              f"{'/'.join(sorted(PRUNE_DIRS))} -- e.g. {result['pruned'][0]}")
    print(f"  source : {result['src']}")
    print(f"  durable: {result['durable']}")
    if result.get("durable_elsewhere"):
        # Named, never silent: the reader must be able to check this judgement.
        print(f"  {len(result['durable_elsewhere'])} symlink(s) already durable elsewhere "
              f"(e.g. {result['durable_elsewhere'][0]['target']})")

    # ORPHAN RECONCILIATION always prints, including the zero case. A line that appears only
    # on failure makes silence ambiguous -- the reader cannot tell "no orphans" from "this
    # build does not check". That ambiguity is the defect this whole tool exists to remove.
    orphans = unattributed_root_files(args.durable.expanduser())
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
            confirmed, refused, failed = rescue(result, args.durable.expanduser())
            print(f"  rescued (read-back confirmed): {len(confirmed)}")
            for f in refused:
                print(f"    REFUSED (by design): {f}")
            for f in failed:
                print(f"    NOT RESCUED: {f}")
            after = sweep(args.session, args.durable.expanduser())
            print(f"  RE-SWEEP: {after['verdict']} — {after['detail']}")
            # FINAL-VERDICT is the ONE line a consumer should parse. It is printed on every
            # single-session exit, after any rescue, in the same shape. context-ceiling-watch
            # first parsed RE-SWEEP (printed only after a copy) and read a no-op sweep as a
            # failure; a consumer parsing an incidental line is the class, this line is the fix.
            print("\n".join(final_lines(after, len(confirmed), len(refused), len(failed))))
            return 0 if after["verdict"] == "COMPLETE" or args.report_only else 1

    print("\n".join(final_lines(result)))
    return 0 if (result["verdict"] == "COMPLETE" or args.report_only) else 1


if __name__ == "__main__":
    sys.exit(main())
