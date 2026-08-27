#!/usr/bin/env python3
"""Measure BEHAVIOURAL-NORM compliance over the session-transcript corpus.

WIRED 2026-08-27: `47 7 * * *` via scheduled-check-runner.sh. The three divergences below are
RECONCILED as of the same date — see RECONCILED.

`~/bin/peer-comms-check` (cron'd since 2026-08-19) already measures the same relation
per-session, and is the SOURCE OF TRUTH for what counts as a shared write. This file was
built without finding it — a duplication, disclosed rather than hidden. Three unreconciled
divergences make any number here non-comparable to that tool's:

  1. SHARED-PATH DEFINITION. peer-comms-check and hooks/shared/shared_roots.py use an
     ALLOWLIST of 6 roots (dev/share, bin, dev/infrastructure, .claude/{projects,commands,
     hooks}). This file uses a DENYLIST (/tmp, /scratchpad, /.cache), so it counts
     ~/dev/projects/** as shared where no other implementation does. BROADER, not equal.
  2. MISSING _GIT_WRITE. peer-comms-check treats bare `git commit`/`git push` as an implicit
     shared write (its :63). This file does not, so it UNDERCOUNTS exactly the case
     peer_comms_reminder.py's git-push trigger targets.
  3. mtime IS NOT SESSION START. The corpus cutoff below gates on file mtime. A session that
     BEGAN before a norm's effective date but was appended to after it is pulled into the
     post-norm denominator whole — the same class as the 27x error documented below, at
     session granularity instead of corpus granularity. Gate on the record timestamp.

RECONCILED 2026-08-27 — all three are now closed, plus a FOURTH that was never listed:
  1. FIXED: the denylist is gone; SHARED_ROOTS mirrors peer-comms-check's 6-root allowlist.
  2. FIXED: _GIT_WRITE added, matching peer-comms-check.
  3. FIXED: _select_corpus gates on the first record's timestamp (_session_start), not mtime.
     PARTIAL: ~1% of transcripts carry no readable start and still fall back to mtime; the count
     is reported in the detail line rather than left silent.
  4. FIXED (unlisted, found during that work): TRANSCRIPTS hardcoded ONE project directory and
     was blind to 34% of its own in-scope corpus (51 of 77 visible). It now globs all of them.
STILL NOT EQUAL to peer-comms-check: that tool reports a per-session verdict on
never-notified/unnotified-tail; this one measures send-before-first-shared-write across a
corpus. Same shared-path definition now, different relation — do not read one as the other.
The shared-path list is a THIRD hand-maintained mirror; the real fix is a data file all three
consumers ingest, which would make the numbers comparable by construction.


WHY THIS EXISTS (Dart MaGcB8c8X3uY, Gap #2 — "build the missing denominator").
The workspace measures SCANNER precision (fp_measure.py) and has never measured
a NORM's compliance rate. Consequence, quoted from that task: "a norm firing 0/6
looks exactly like one firing 6/6, and 'we added a norm' has never once been
followed by 'and here is its measured effect'." Unmeasurable failures cannot be
remediated, only re-analysed.

SHAPE: deliberately the same as tools/fp_measure.py — a predicate replayed over a
real corpus, reporting rate + explicit denominator + coverage, so a zero is an
OBSERVED zero rather than an absence of instrumentation.

CONSUMER (this is the point — an unconsumed metric is the defect it measures). INSTALLED, not
aspirational, as of 2026-08-27; verified end-to-end in BOTH polarities before wiring:
    healthy        -> {"check":"norm-compliance-e2e","rc":0,"marker_hit":0,"status":"ok"}
    forced-adverse -> {"check":"norm-compliance-e2e-adverse","rc":1,"marker_hit":1,"status":"adverse"}
    47 7 * * * scheduled-check-runner.sh norm-compliance <log> \
        '^NORM-ADVERSE|^NORM-CHECK-ERROR|^NORM-CHECK-WARN|^NORM-OK-LOWN' -- python3 <this file>
This file shipped UNCONSUMED for two sessions while its own docstring called that the defect it
measures. The gap was closed only after an explicit prompt; the artifact existing was mistaken
for the outcome. If you unwire it, change this banner in the same commit.

WARN IS IN THE MARKER DELIBERATELY. NORM-CHECK-WARN fires when the baseline fails to
persist, which means regression detection is BLIND on the next run — a cannot-assess
condition, not cosmetic. Measured 2026-08-27 across this workspace: 14 of 29 runner callers
(48%) watch only their tool's ADVERSE FINDING and not its inability to run, and one of them
(`peer-comms`) had therefore emitted an unwatched CANNOT-ASSESS every day for 8 days while
appearing healthy. Leaving WARN out of this marker would reproduce that defect in the very
tool built to measure it.
The runner supplies heartbeat + notify.sh delivery + SIGPIPE-safe marker matching.
Do NOT cron this with a bare `>> log 2>&1`: 53 of 103 crontab job lines already do
that and are silent by construction (measured 2026-08-27).

SUCCESS / FAILURE CRITERIA (explicit, so "no silent failures" applies to the metric
itself and not only to the run):
    NORM-OK       compliance >= WARN_PCT, and the corpus was readable
    NORM-ADVERSE  compliance <  WARN_PCT  -> the runner notifies
    NORM-ADVERSE  compliance fell >= REGRESSION_PP points below the recorded
                  baseline -> regression, notified even if above WARN_PCT
    NORM-CHECK-ERROR corpus unreadable / denominator zero -> notified, never silent
Exit codes: 0 OK, 1 adverse, 2 check error. Markers are the contract; the exit code
is the adjacent signal (see scheduled-check-runner.sh's own header on why).

HONEST LIMITATION, stated because the number is quotable and will be quoted:
whether LIVE PEERS existed at the moment of a write is NOT reconstructable from a
transcript — the session registry is ephemeral. So the peer-comms denominator is
"sessions that wrote", a SUPERSET of "sessions that owed a broadcast", and the
reported rate is a LOWER BOUND on true compliance. Do not quote it as the true rate.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

# EVERY project directory, not one. The slug is derived from the session's CWD -- launched from
# ~/dev it is "-home-ichardart-dev", from ~ it is "-home-ichardart" -- so hardcoding one directory
# silently makes it the POPULATION DEFINITION. Measured 2026-08-27 before this fix: 77 transcripts
# in scope across all dirs, 51 visible here -- BLIND TO 34% OF ITS OWN DENOMINATOR while reporting
# a confident rate. ~/bin/peer-comms-check documents fixing this exact bug (it had 49.2% coverage
# with ZERO uuid overlap to the largest unscanned directory); this file was written afterwards and
# reproduced it. It was also untestable by construction: TRANSCRIPTS is overwritten by the contract
# fixture and was absent from _MUTABLE, so no assertion ever touched the production population.
TRANSCRIPTS = os.path.expanduser("~/.claude/projects/*/*.jsonl")
STATE = os.path.expanduser("~/.claude/logs/norm-compliance-baseline.json")
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# NORM EFFECTIVE DATES — THE MOST IMPORTANT CONSTANT IN THIS FILE.
#
# A norm cannot be violated before it existed. The first version of this tool measured the
# WHOLE transcript corpus (2026-03-04 -> 2026-08-27) against a norm established 2026-08-15,
# and reported 1.9% compliance. Restricted to the sessions actually under the norm, the true
# figure is 51.5% (17/33) — a 27x error, in the alarming direction, that survived a positive
# control and was caught only by an independent Frame-Validity judge asking whether the norm
# predated the corpus.
#
# A denominator built without an effective date is WORSE THAN NO DENOMINATOR: it manufactures
# a confident wrong number that reads as measurement. Any norm added below MUST carry its date.
# Source of a date: the `modified:` field of the memory file that establishes the norm.
NORM_EFFECTIVE = {
    # feedback_peer_comms_is_per_edit_not_one_time.md modified 2026-08-15
    # feedback_peer_comms_proactive_at_start.md      modified 2026-08-22
    # Earliest of the pair — the obligation begins with the first statement of it.
    "peer-comms": "2026-08-15",
}

# THRESHOLDS ARE UNCALIBRATED — stated rather than implied. These are judgement calls, not
# measured bars, and a threshold presented without that label is the same defect as an
# undated denominator. Baseline at first correct measurement: peer-comms 51.5%, n=33.
WARN_PCT = 35.0       # below this -> NORM-ADVERSE. Set ~15pp under the observed baseline.
REGRESSION_PP = 10.0  # drop this many percentage points vs baseline -> NORM-ADVERSE.
MIN_N = 10            # below this denominator, report NORM-OK-LOWN: too few to judge.

# THE CONSTRUCT THE RATE MEASURES. Bump this whenever the PREDICATE changes -- shared-path
# definition, what counts as a write, or how the corpus is selected. A baseline recorded under a
# different construct is not a baseline, it is a different quantity wearing the same name.
#
# DEMONSTRATED 2026-08-27, which is why this exists: the allowlist reconciliation moved the rate
# 30.3% -> 61.8% with ZERO behaviour change (proved by running the pre-change code against the
# same corpus: 31.6%, i.e. flat vs its own baseline). The persisted 30.3% baseline survived the
# change, so REGRESSION_PP=10 was measuring against a quantity that no longer existed: a real
# collapse from 61.8% to 35% would have registered as a +4.7pp IMPROVEMENT and never alerted.
# Silent, and it took a peer misreading the jump as a behaviour change to surface it.
CONSTRUCT_VERSION = "2026-08-27-allowlist-sessionstart-allprojectdirs"
# CONTROL GAP, STATED RATHER THAN IMPLIED: this guard has NO assertion and NO mutant. It was
# proven live in three polarities (differing construct -> WARN; absent construct -> WARN;
# matching -> silent) but the suite cannot detect its removal, so by this file's own standard its
# green tick is not evidence. Controlling it needs a contract fixture with a PRE-SEEDED baseline
# -- the existing fixture always starts with prior=None, so the branch is unreachable there.
# Owed, not done. Same shape as the dead low-N branch this file was rebuilt to fix.


# A Bash command that MUTATES a file. Deliberately broad on the mutation verbs and narrow on
# redirection (`> &` is a descriptor dup, not a write).
# NOTE the `(?!/dev/)` guard. Without it this regex matched `2>/dev/null` and `> /dev/null`,
# which appear in a large fraction of ordinary shell commands. That made first_write fire on
# almost every session's first Bash call and drove the measured rate from 51.5% to 9.1% —
# a 42pp error introduced BY the fix for a different edge case, caught only because the number
# moved implausibly and the predicate was then tested against known non-writes.
_BASH_MUTATES = re.compile(
    r"(^|[;&|]|\s)(tee\b|sed\s+-i|cp\b|mv\b|install\b|truncate\b|dd\b)"
    r"|>>?\s*(?!&|/dev/)[^\s]", re.M)
# SHARED-PATH DEFINITION -- ALLOWLIST, mirroring ~/bin/peer-comms-check's SHARED_ROOTS and
# hooks/shared/shared_roots.py. This file previously used a DENYLIST (/tmp, /scratchpad,
# /.cache) with "anything else is shared", which counted ~/dev/projects/** as shared where no
# other implementation does -- BROADER, so its rate measured a different construct and was not
# comparable to the tool that is actually cron'd.
#
# MIRRORED, NOT IMPORTED, and not drift-checked by reading the other copies' source. A
# cross-repo runtime import was considered and rejected (see peer-comms-check's header and the
# P-H2 plan); a test-time source-reader would be strictly MORE fragile than the duplication it
# replaced -- it breaks the moment the list moves behind a helper or into a config file. The
# real fix is to extract this policy into a data file all three consumers ingest, which makes
# the numbers comparable by construction instead of by hand-synchronisation. Until then this is
# the third hand-maintained mirror of an unasserted list, and saying so is the honest state.
SHARED_ROOTS = [
    os.path.expanduser("~/dev/share"),
    os.path.expanduser("~/bin"),
    os.path.expanduser("~/dev/infrastructure"),
    os.path.expanduser("~/.claude/projects"),      # the memory repos live here
    os.path.expanduser("~/.claude/commands"),
    os.path.expanduser("~/.claude/hooks"),
]

# A commit or push mutates a shared repo even when no single file path appears in the command.
# peer-comms-check has counted this since it was written; this file did not, so it UNDERCOUNTED
# exactly the case peer_comms_reminder.py's git-push trigger targets.
_GIT_WRITE = re.compile(r"\bgit\s+(?:commit|push)\b")


# Under the old DENYLIST, "mutating verb present but no target parsed" returned True ("not
# scratch => shared"). Under an ALLOWLIST that is wrong -- "not parsed" is not evidence of a
# shared root -- so it is now False. Named, and in _MUTABLE, because flipping it back is the
# pre-allowlist defect and three mutants need to reach it: with an allowlist, a bad
# _BASH_MUTATES can no longer change any verdict on its own (its false positives land on
# /dev/null and other non-shared targets, which the allowlist rejects anyway). The guard and
# the allowlist are partly redundant now, and a mutant that cannot change a verdict is not a
# control -- so those mutants flip this flag too, restoring the historical PAIR of defects.
_UNPARSED_TARGET_IS_SHARED = False


# Canonicalised ONCE. _is_shared_path realpaths the candidate, so the roots must be realpathed
# too or the comparison is asymmetric: a path under a symlinked root resolves to the target while
# the root keeps its symlink spelling, and the write silently stops counting. No root is a symlink
# on this machine today (checked), so this changes no current verdict -- but hooks ARE deployed by
# symlink in this workspace, so the asymmetry is one `ln -s` away from being live.
_REAL_SHARED_ROOTS = [os.path.realpath(r).rstrip(os.sep) for r in SHARED_ROOTS]


def _is_shared_path(p) -> bool:
    """Is this path inside a root other live sessions read or build on?"""
    if not p:
        return False
    # A RELATIVE path cannot be classified. realpath() would resolve it against THIS process's
    # cwd, which has nothing to do with the session that ran the command -- and because this
    # tool lives under ~/dev/infrastructure/tools, every relative mutation in the corpus
    # resolved INTO a shared root and counted, purely as an artifact of where the checker is
    # installed. Measured: `sed -i s/a/b/ notes.md` returned True from the tools directory.
    # The transcript does carry a cwd field, but this predicate is not given it, so the honest
    # answer is "not classifiable" rather than a location-dependent guess.
    if not os.path.isabs(p):
        return False
    try:
        rp = os.path.realpath(p)
    except (OSError, ValueError):
        return False
    for root in _REAL_SHARED_ROOTS:
        if rp == root or rp.startswith(root + os.sep):
            return True
    return False
# Mutation-control seam ONLY — always False in production. Exists so a mutant can corrupt the
# MEASUREMENT path, which no predicate-level mutant can reach. See _contract_failures().
# Re-entry guard. _main_contract_failures() calls main(), and main() runs mutation_test(),
# which calls back into the contract assertions -> infinite recursion. Caught on first run.
_IN_CONTRACT_TEST = False
_COUNT_UNREADABLE = True   # mutated to prove the OSError handler is under test


def _below_floor(wrote) -> bool:
    """Is this denominator too small to judge on?  EXTRACTED SO A MUTANT CAN REACH IT.

    Inline in main() this decision was untestable: _main_contract_failures() patched MIN_N to 1,
    so the NORM-OK-LOWN branch never executed and `MIN_N = 10000` survived the suite GREEN while
    production would have reported "too few to judge" forever and never rendered a verdict.
    Same lesson as _select_corpus below: a decision with no seam has no control.

    Asserted through main()'s OUTPUT, never by calling this directly -- a unit call would stay
    green if main() reverted to an inline comparison and unplugged this function.
    """
    return wrote < MIN_N


def _restore_globals(snapshot):
    """Put every snapshotted global back. EXTRACTED FOR THE SAME REASON as _below_floor.

    The teardown used to reset _IN_CONTRACT_TEST to a hardcoded False rather than restoring it,
    and deleting that line left the suite green -- the guard's only failure direction was
    fail-open. Routing teardown through one named function gives the mutation harness something
    to break, so "the mutation gate is never re-armed" becomes a detectable defect.
    """
    globals().update(snapshot)


def _classify(first_send, first_write):
    """Bucket one session: never | compliant | tie | late.

    EXTRACTED to delete two mutation seams (_FORCE_NO_COMPLIANT, _FORCE_TIE_WIDE) that existed
    in production code purely so tests could corrupt classification. Mutants now patch this
    function, exactly as they patch _write_targets — the harness's existing idiom. Suggested by
    an Opus reviewer; ~15 lines, removes two globals and makes classification unit-testable.
    """
    if first_send is None:
        return "never"
    if first_send < first_write:
        return "compliant"
    if first_send == first_write:
        return "tie"
    return "late"


_START_SCAN_LINES = 20      # records scanned for a timestamp before falling back to mtime
_MTIME_FALLBACKS = 0        # sessions whose start could not be read and were gated by mtime


def _session_start(path):
    """Epoch seconds of a session's FIRST record, falling back to file mtime.

    mtime is not session start. A session that BEGAN before a norm's effective date but was
    APPENDED TO after it has a post-norm mtime, so mtime-gating pulls it into the post-norm
    denominator WHOLE -- the same class as the 27x undated-denominator error, at session
    granularity instead of corpus granularity. Records carry an ISO `timestamp`; use it.

    Falls back to mtime when no record carries a timestamp (meta-only or unreadable files) so
    the corpus never silently shrinks. Only the first few lines are read: the first record is
    the session start and scanning further would cost a full read of ~3000 transcripts.
    """
    try:
        with open(path, errors="replace") as fh:
            for _i, line in enumerate(fh):
                if _i >= _START_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = json.loads(line).get("timestamp")
                except json.JSONDecodeError:
                    continue
                if ts:
                    return datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError):
        pass
    # FALLBACK IS A DEGRADATION, NOT A DEFAULT. Gating on mtime is the very defect this function
    # exists to fix, so a run that silently fell back would be indistinguishable from a correct
    # one. Counted here and reported in main()'s detail line.
    globals()["_MTIME_FALLBACKS"] = _MTIME_FALLBACKS + 1
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _select_corpus(all_paths, cutoff, mtime_fn=None):
    """Restrict the corpus to sessions that existed under the norm. Returns (paths, excluded).

    EXTRACTED so it can be tested. While inlined in main(), NO test reached it: every contract
    assertion calls measure_peer_comms() directly, so an Opus reviewer reproduced the 27x error
    verbatim — replacing the filter with `paths = all_paths` — and got SELF-TEST PASS, EXIT=0.
    The defect this whole file exists to prevent was the one thing the suite could not see.

    mtime_fn is injected so the contract assertion can supply synthetic timestamps rather than
    touching the filesystem — no production seam required. It defaults to _session_start, NOT
    os.path.getmtime; see that function for why the difference matters.
    """
    # Resolved at CALL time, not bound as a default. `mtime_fn=_session_start` in the signature
    # captures the function object when this module is defined, so patching the global left the
    # default pointing at the original -- the mutation control proved the seam was decorative by
    # staying green when the behaviour was reverted.
    if mtime_fn is None:
        mtime_fn = _session_start
    kept = [p for p in all_paths if mtime_fn(p) >= cutoff]
    return kept, len(all_paths) - len(kept)


def _tool_uses(record):
    """Yield (tool_name, tool_input) from one transcript record, tolerating shape drift."""
    msg = record.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                inp = block.get("input")
                yield name, (inp if isinstance(inp, dict) else {})


def _write_targets(cmd):
    """Absolute paths this command WRITES TO — not every path it mentions.

    THIRD instance of one bug class in this file, each found by a different reviewer:
    (1) the redirect leg matched `2>/dev/null`; (2) scratch writes were counted; (3) THIS —
    the predicate accepted any non-scratch path anywhere in the command as evidence of a
    shared write, so `cp /home/x/real.md /tmp/backup` (mutates SCRATCH, merely READS shared)
    and `grep -R foo /home/x/dev > /tmp/out.txt` were both false positives. Read source and
    write target must be distinguished, so the target is parsed per verb.

    The mutation controls did NOT catch this: no case exercised a mixed scratch/shared
    command. Mutation controls test the mutants you thought of — they raise the floor, they
    do not close the class.
    """
    targets = []
    # Redirection: the token after > or >> (excluding &-dups and /dev/*).
    targets += re.findall(r">>?\s*(?!&)(/\S+|[\w./-]+)", cmd)
    # tee [flags] FILE...
    for m in re.finditer(r"\btee\b((?:\s+-\S+)*)((?:\s+\S+)*)", cmd):
        targets += [t for t in m.group(2).split() if not t.startswith("-")]
    # sed -i ... FILE  (last non-flag token of that clause)
    for m in re.finditer(r"\bsed\s+-i\b([^;&|]*)", cmd):
        toks = [t for t in m.group(1).split() if not t.startswith("-")]
        if toks:
            targets.append(toks[-1])
    # cp / mv / install SRC... DST  -> destination is the LAST non-flag token
    for m in re.finditer(r"\b(cp|mv|install)\b([^;&|]*)", cmd):
        toks = [t for t in m.group(2).split() if not t.startswith("-")]
        if len(toks) >= 2:
            targets.append(toks[-1])
    # dd of=FILE
    targets += re.findall(r"\bof=(\S+)", cmd)
    # truncate [flags] FILE
    for m in re.finditer(r"\btruncate\b([^;&|]*)", cmd):
        toks = [t for t in m.group(1).split() if not t.startswith("-")]
        if toks:
            targets.append(toks[-1])
    return [t for t in targets if t and not t.startswith("/dev/")]


def _is_shared_write(name, tool_input):
    """True if this tool call mutated SHARED state (edge cases (a) and (b))."""
    if name in WRITE_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        return _is_shared_path(path)
    if name == "Bash":
        cmd = str(tool_input.get("command") or "")
        if _GIT_WRITE.search(cmd):
            return True
        if not _BASH_MUTATES.search(cmd):
            return False
        targets = _write_targets(cmd)
        if not targets:
            # See _UNPARSED_TARGET_IS_SHARED above.
            return _UNPARSED_TARGET_IS_SHARED
        return any(_is_shared_path(t) for t in targets)
    return False


def measure_peer_comms(paths):
    """Predicate: did the session call SendMessage BEFORE its first shared-state write?

    THREE EDGE CASES, each found by an independent code-quality review and each closed here
    rather than noted. All three were invisible to the original positive control, because a
    control on the EXTRACTOR cannot see a defect in the POPULATION.

    (a) BASH WRITES COUNT. The first version keyed only on Write/Edit/MultiEdit/NotebookEdit,
        so a session that mutated files via `sed -i`/`tee`/redirection — which this harness's
        auto mode explicitly instructs — had first_write=None and dropped out of the
        denominator entirely, inflating compliance. Measured on the post-norm window: only
        1 of 47 sessions is Bash-write-only (33 -> 34 denominator, <=1.5pp), so the effect is
        immaterial HERE, but it is a silent exclusion and is now counted rather than assumed.
    (b) SCRATCHPAD WRITES DO NOT COUNT. A write to /tmp or a scratchpad is not shared state
        and creates no peer-coordination obligation; counting it manufactures a denominator.
    (c) TIES ARE THEIR OWN BUCKET. first_send == first_write (same transcript record) is
        neither before nor after. The original silently classified it as non-compliant. An
        invariant now asserts the buckets sum to the denominator, so a future miscount is loud.

    Returns (compliant, wrote, never, scanned, unreadable, ties).
    """
    compliant = wrote = never = scanned = unreadable = ties = late = 0
    for path in paths:
        first_send = first_write = None
        seen = False
        try:
            with open(path, errors="replace") as fh:
                for idx, line in enumerate(fh):
                    if not line.strip():
                        continue
                    seen = True
                    # Cheap prefilter before json.loads. Kept deliberately loose:
                    # a false positive costs one parse, a false negative loses a row.
                    if "SendMessage" not in line and "tool_use" not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    for name, tool_input in _tool_uses(record):
                        if name == "SendMessage" and first_send is None:
                            first_send = idx
                        elif first_write is None and _is_shared_write(name, tool_input):
                            first_write = idx
                    # Both latched -> nothing later can change the verdict. Measured on the
                    # live corpus: 131,449 of 150,730 lines (87.2%) were being read AFTER
                    # both targets resolved; adding this break took a full run 3.93s -> 0.40s
                    # (9.8x). Correctness-neutral, both fields are `is None`-guarded above.
                    if first_send is not None and first_write is not None:
                        break
        except OSError:
            unreadable += 1 if _COUNT_UNREADABLE else 0
            continue
        if not seen:
            continue
        scanned += 1
        if first_write is None:
            continue
        wrote += 1
        _b = _classify(first_send, first_write)
        never += _b == "never"
        compliant += _b == "compliant"
        ties += _b == "tie"
        late += _b == "late"
    # INVARIANT: every writing session lands in exactly one bucket. A silent miscount here
    # is how the first version of this tool shipped a 27x error.
    assert compliant + late + never + ties == wrote, (
        f"bucket invariant violated: {compliant}+{late}+{never}+{ties} != {wrote}")
    return compliant, wrote, never, scanned, unreadable, ties


def _load_baseline():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_baseline(data):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as fh:
            json.dump(data, fh, indent=1)
    except OSError as exc:                       # never fail the check on a state write
        print(f"NORM-CHECK-WARN: baseline not persisted: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--norm", default="peer-comms", choices=["peer-comms"],
                    help="which norm to measure (one implemented; the harness is the point)")
    ap.add_argument("--set-baseline", action="store_true",
                    help="record this run's rate as the regression baseline")
    # `--self-check` is the workspace-standard name (DECISIONS-LOG.md:7000; six sibling
    # tools use it). `--self-test` kept as a hidden alias so nothing that already calls it
    # breaks, but a bulk runner sweeping for --self-check must find this tool.
    ap.add_argument("--self-check", "--self-test", dest="self_check", action="store_true",
                    help="positive+negative controls AND mutation controls, then exit")
    # ONE number on stdout, from a BARE command with no shell metacharacters, so a commit
    # message asserting this count can be mechanically re-executed by the commit-msg hook.
    # A grep -c over the source would be a text match on a narrow pattern; this is the
    # count the suite actually runs.
    ap.add_argument("--count-cases", action="store_true",
                    help="print the number of predicate cases and exit (machine-readable)")
    args = ap.parse_args()

    # An internal invariant failure is a BROKEN CHECK, not a norm regression. Without this the
    # bucket-invariant and label-drift asserts exit 1 with no marker, and the runner maps rc=1
    # to adverse -> "the norm regressed". rc=2 + NORM-CHECK-ERROR says "this check is broken".
    #
    # --self-check IS INSIDE THE TRY. It used to sit above it, so an AssertionError on the
    # self-check path escaped uncaught: rc=1, EMPTY STDOUT, no marker -- and rc=1 is this file's
    # declared code for "the norm regressed" (see Exit codes above). A broken check therefore
    # presented as a norm regression with nothing on stdout for the runner to match, which is the
    # silent-by-marker failure this file's header exists to argue against.
    #
    # The bare `except Exception` is deliberate and is NOT defensive padding: the suite itself
    # raises non-AssertionError types on realistic edits. A name mismatch when adding a key to
    # _MUTABLE raises KeyError inside mutation_test(); a malformed mutant regex raises re.error;
    # tempfile raises OSError on a full /tmp. Every one of those would otherwise reproduce the
    # exact rc=1/no-marker defect fixed above. A monitor must never exit non-zero without saying
    # why on stdout.
    try:
        if args.count_cases:
            print(len(_predicate_cases()))
            return 0
        if args.self_check:
            return self_test()
        return _main_inner(args)
    except AssertionError as exc:
        print(f"NORM-CHECK-ERROR: internal invariant failed: {exc}")
        return 2
    except Exception as exc:                      # noqa: BLE001 - see comment above
        # Marker on STDOUT for the runner; traceback on STDERR for the human. Without the
        # traceback this catch made --self-check -- the one invocation run specifically to debug
        # the harness -- report a one-line repr with no line number.
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"NORM-CHECK-ERROR: check crashed: {exc!r}")
        return 2


def _main_inner(args):

    if not _IN_CONTRACT_TEST and mutation_test() != 0:   # a suite a mutant survives is not a suite
        print("NORM-CHECK-ERROR: mutation controls failed — refusing to report a number "
              "from a test suite that cannot detect a broken predicate")
        return 2

    all_paths = sorted(glob.glob(TRANSCRIPTS))
    if not all_paths:
        print(f"NORM-CHECK-ERROR: no transcripts matched {TRANSCRIPTS}")
        return 2

    # Restrict the corpus to sessions that existed under the norm. See NORM_EFFECTIVE.
    eff = NORM_EFFECTIVE.get(args.norm)
    if not eff:
        print(f"NORM-CHECK-ERROR: no effective date recorded for norm '{args.norm}' — "
              f"refusing to measure, because an undated denominator produces a confident "
              f"wrong number (see NORM_EFFECTIVE)")
        return 2
    cutoff = datetime.strptime(eff, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    # RESET BEFORE THE REAL CORPUS. mutation_test() above runs main() against synthetic fixtures
    # whose records carry no timestamp, so every one of them takes the mtime fallback and bumps
    # this counter. Without the reset the production line reported 430 when the true figure was
    # 30 -- a diagnostic added THIS SESSION to make silent degradation visible, shipping a
    # confidently wrong number on its first run. Exactly the class this file's header documents.
    globals()["_MTIME_FALLBACKS"] = 0
    paths, excluded = _select_corpus(all_paths, cutoff)
    if not paths:
        print(f"NORM-CHECK-ERROR: no transcripts on/after the norm's effective date {eff} "
              f"({len(all_paths)} exist, all older)")
        return 2

    compliant, wrote, never, scanned, unreadable, ties = measure_peer_comms(paths)
    if wrote == 0:
        print(f"NORM-CHECK-ERROR: denominator is zero "
              f"({scanned} transcripts scanned, {unreadable} unreadable) — "
              f"predicate or corpus shape has changed")
        return 2

    pct = 100.0 * compliant / wrote
    baseline = _load_baseline()
    _rec = baseline.get(args.norm, {})
    prior = _rec.get("pct")
    # A baseline from a DIFFERENT construct is not comparable. Warn loudly and refuse the
    # regression comparison rather than compute a difference between two different quantities.
    # NORM-CHECK-WARN is already in the consumer's marker set, so this is not silent.
    _stale_construct = prior is not None and _rec.get("construct") != CONSTRUCT_VERSION
    if _stale_construct:
        print(f"NORM-CHECK-WARN: baseline was recorded under construct "
              f"{_rec.get('construct', '<unversioned>')!r} but this build measures "
              f"{CONSTRUCT_VERSION!r} — regression comparison SKIPPED (the two are different "
              f"quantities). Re-run with --set-baseline to re-anchor.")
        prior = None

    detail = (f"{args.norm}: {compliant}/{wrote} = {pct:.1f}% complied "
              f"({never} never broadcast, {ties} tie; {scanned} transcripts on/after {eff}, "
              f"{excluded} pre-norm EXCLUDED, {unreadable} unreadable"
              + (f", {_MTIME_FALLBACKS} mtime-gated (session start unreadable)"
                 if _MTIME_FALLBACKS else "") + ")")
    if prior is not None:
        detail += f" | baseline {prior:.1f}%"

    if _below_floor(wrote):
        print(f"NORM-OK-LOWN: {detail} — denominator below MIN_N={MIN_N}; "
              f"reporting without a verdict rather than judging on too few sessions")
        return 0

    adverse = []
    if pct < WARN_PCT:
        adverse.append(f"below floor {WARN_PCT:.1f}% (UNCALIBRATED threshold)")
    if prior is not None and (prior - pct) >= REGRESSION_PP:
        adverse.append(f"regressed {prior - pct:.1f}pp vs baseline")

    if args.set_baseline or prior is None:
        baseline[args.norm] = {"pct": pct, "n": wrote,
                               "at": datetime.now(timezone.utc).isoformat(),
                               "construct": CONSTRUCT_VERSION}
        _save_baseline(baseline)

    if adverse:
        print(f"NORM-ADVERSE: {detail} — {'; '.join(adverse)}. "
              f"NOTE: denominator is a superset (live-peer state is not reconstructable), "
              f"so this is a LOWER bound on true compliance.")
        return 1
    print(f"NORM-OK: {detail}")
    return 0


# A path under a REAL shared root. The suite's shared exemplars used to be "/home/x/dev/...",
# which the allowlist correctly rejects (wrong home) -- so they must be rooted at this
# machine's home, or every "counts" case silently flips to False and the suite measures
# nothing while still reporting 18 green cases.
_EX_SHARED = os.path.expanduser("~/dev/share")
_EX_BIN = os.path.expanduser("~/bin")
_EX_HOOKS = os.path.expanduser("~/.claude/hooks")


def _predicate_cases():
    """(label, command_or_path, kind, expected) for _is_shared_write. Single source of truth
    so the assertion suite and the mutation controls exercise exactly the same cases."""
    return [
        ("shared Write counts",           "Write", {"file_path": _EX_SHARED + "/a.md"}, True),
        ("scratchpad Write excluded",     "Write", {"file_path": "/tmp/c/scratchpad/a.md"}, False),
        ("Bash sed -i counts",            "Bash", {"command": f"sed -i s/a/b/ {_EX_SHARED}/a.md"}, True),
        ("Bash tee counts",               "Bash", {"command": f"cat x | tee {_EX_SHARED}/a.md"}, True),
        ("Bash read-only excluded",       "Bash", {"command": f"grep -R foo {_EX_SHARED}"}, False),
        ("descriptor dup 2>&1 excluded",  "Bash", {"command": "ls /home/x 2>&1"}, False),
        ("2>/dev/null excluded",          "Bash", {"command": "ls /home/x 2>/dev/null"}, False),
        ("> /dev/null excluded",          "Bash", {"command": "grep -R f /home/x > /dev/null"}, False),
        ("read-only pipeline excluded",   "Bash", {"command": "crontab -l | grep -c foo"}, False),
        ("scratch-only Bash excluded",    "Bash", {"command": "echo hi > /tmp/c/scratchpad/x"}, False),
        ("non-write tool excluded",       "Read", {"file_path": _EX_SHARED + "/a.md"}, False),
        # More than one root must be exercised, or "gut the allowlist" has nothing to bite
        # and 5 of the 6 roots sit unasserted behind a green tick.
        # Relative targets are unclassifiable; absolute ones are not. Without this pair the
        # location-dependent false positive above was invisible to the suite.
        ("relative Bash target excluded (cwd unknown)",
         "Bash", {"command": "sed -i s/a/b/ notes.md"}, False),
        ("relative cp target excluded (cwd unknown)",
         "Bash", {"command": "cp a.txt notes.md"}, False),
        ("second shared root counts (~/bin)",
         "Write", {"file_path": _EX_BIN + "/thing.sh"}, True),
        ("third shared root counts (~/.claude/hooks)",
         "Write", {"file_path": _EX_HOOKS + "/x.py"}, True),
        # A repo moves with no path in the command. BOTH polarities, plus a read-only
        # sibling so the new leg is shown discriminating rather than always-true.
        ("bare vcs-commit counts",        "Bash", {"command": "git commit -m x"}, True),
        ("bare vcs-push counts",          "Bash", {"command": "git push"}, True),
        ("vcs status excluded",           "Bash", {"command": "git status --porcelain"}, False),
        # 2 of 3 _SCRATCH alternations were DEAD: all 13 scratch paths in the suite began
        # with /tmp/, so `_SCRATCH = r"^/tmp/"` passed everything green. Verified 2026-08-27.
        ("outside every shared root excluded", "Write", {"file_path": "/home/x/work/scratchpad/a.md"}, False),
        ("dot-cache excluded",            "Write", {"file_path": "/home/x/.cache/thing/a.md"}, False),
        # MIXED-PATH cases — the CRITICAL a reviewer found that the mutants missed.
        # Write TARGET decides, never a path merely mentioned.
        ("cp shared->scratch excluded (target is scratch)",
         "Bash", {"command": f"cp {_EX_SHARED}/real.md /tmp/c/scratchpad/backup.md"}, False),
        ("cp scratch->shared counts (target is shared)",
         "Bash", {"command": f"cp /tmp/c/scratchpad/x.md {_EX_SHARED}/real.md"}, True),
        ("read shared, redirect to scratch, excluded",
         "Bash", {"command": f"grep -R foo {_EX_SHARED} > /tmp/c/scratchpad/out.txt"}, False),
        ("read scratch, redirect to shared, counts",
         "Bash", {"command": f"cat /tmp/c/scratchpad/x > {_EX_SHARED}/real.md"}, True),
        ("tee to shared counts even when reading scratch",
         "Bash", {"command": f"cat /tmp/c/scratchpad/x | tee {_EX_SHARED}/real.md"}, True),
    ]


# Every contract assertion's label, listed once so the COVERAGE AUDIT can see them.
# Without this the audit iterated _predicate_cases() only, so a contract assertion with no
# mutant breaking it passed unaudited — verified 2026-08-27 by deleting the sole contract
# mutant and watching the suite stay green while printing "0 uncontrolled". That was the
# FOURTH iteration of one shape in a single session: the marker covered findings not
# failures; the mutants covered the predicate not the contract; the contract assertions
# covered the function not the pipeline; and the audit covered the predicate cases not the
# contract assertions. Each fix's own coverage stopped at the boundary it had just moved.
CONTRACT_LABELS = (
    "CONTRACT wrote counts only shared writes",
    "CONTRACT compliant counts send-before-write",
    "CONTRACT never counts wrote-without-send",
    "CONTRACT scanned counts every readable transcript",
    "CONTRACT unreadable counts a real OSError",
    "CONTRACT ties is zero when no send/write share a record",
    "CONTRACT corpus filter keeps only post-cutoff",
    "CONTRACT corpus filter counts exclusions",
    "CONTRACT corpus filter gates on session start, not file mtime",
)

# Assertions on main()'s OUTPUT, kept separate because a different function checks them.
# The coverage audit unions both; the drift guards check each against its own function.
MAIN_CONTRACT_LABELS = (
    "CONTRACT main() applies the corpus cutoff",
    "CONTRACT main() measures only post-cutoff sessions",
    "CONTRACT main() reports NORM-OK-LOWN below the floor",
    "CONTRACT main() re-arms the mutation gate after the contract test",
)


def _started_pre_norm_is_excluded():
    """A session that BEGAN pre-norm but was APPENDED to post-norm must be EXCLUDED.

    Written against real files because the defect lives in the gap between two real signals:
    the file's mtime (post-norm, because it was appended to) and its first record's timestamp
    (pre-norm, because that is when the session started). A synthetic stub for either one would
    assert the mock, not the behaviour.
    """
    import tempfile, time
    cutoff = datetime.strptime(NORM_EFFECTIVE["peer-comms"], "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp()
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "appended.jsonl")
        started = datetime.fromtimestamp(cutoff - 86400 * 30, tz=timezone.utc).isoformat()
        with open(f, "w") as fh:
            fh.write(json.dumps({"timestamp": started, "type": "user"}) + "\n")
        os.utime(f, (time.time(), time.time()))      # appended to TODAY
        kept, _excluded = _select_corpus([f], cutoff)
        return kept == []


def _contract_failures():
    """Assertions that observe measure_peer_comms' OUTPUT, not just the predicate.

    WHY THIS EXISTS (peer dev-a5, 2026-08-27): "coverage of assertions is not coverage of the
    contract." Every one of the 16 predicate cases observes `_is_shared_write` in isolation.
    NONE observed the measurement itself, so three mutations left the suite fully GREEN:
      C1  measure_peer_comms never counts anyone compliant  -> would report 0% forever
      C2  the NORM_EFFECTIVE cutoff filter disabled         -> reintroduces the 27x error
      C3  the bucket invariant assert removed
    C2 is the one that matters: this file exists BECAUSE of that defect, carries a long
    docstring about it, and the suite could not detect its removal. A mutant count of 7 and
    "0 uncontrolled" were both true as printed and gave false assurance.

    Fixtures are synthetic transcripts with known buckets, so the returned tuple is checked
    rather than the classifier that feeds it.
    """
    import tempfile

    def rec(name, inp):
        return json.dumps({"message": {"content": [
            {"type": "tool_use", "name": name, "input": inp}]}})

    SHARED = {"file_path": _EX_SHARED + "/real.md"}
    fixtures = {
        # compliant: SendMessage strictly before the first shared write
        "compliant.jsonl": [rec("SendMessage", {}), rec("Write", SHARED)],
        # late: write first, broadcast after
        "late.jsonl":      [rec("Write", SHARED), rec("SendMessage", {})],
        # never: wrote, never broadcast
        "never.jsonl":     [rec("Write", SHARED)],
        # not counted: no shared write at all (scratchpad only)
        "nowrite.jsonl":   [rec("Write", {"file_path": "/tmp/scratchpad/x.md"})],
        # 5th fixture exists to defeat a TRUNCATION defect. A cross-family reviewer
        # (gpt-5.6-sol, 2026-08-27) pointed out that with exactly 4 fixtures, changing the
        # production loop to `paths[:4]` would pass all contract assertions AND all mutants:
        # the fixture count equalled the truncation point, so truncation was unobservable.
        # The `scanned == len(paths)` assertion below is the real guard; this 5th file stops
        # the count being degenerate.
        "second-late.jsonl": [rec("Write", SHARED), rec("SendMessage", {})],
    }
    fails = []
    checked = []
    with tempfile.TemporaryDirectory() as d:
        paths = []
        # A DIRECTORY among the paths: open() raises IsADirectoryError (an OSError), so the
        # real except-handler runs. This replaced a _FORCE_UNREADABLE seam that sat AFTER the
        # try/except and therefore never entered the handler it claimed to test.
        _dirpath = os.path.join(d, "a_directory_not_a_file.jsonl")
        os.mkdir(_dirpath)
        paths.append(_dirpath)
        for fname, lines in fixtures.items():
            p = os.path.join(d, fname)
            with open(p, "w") as fh:
                fh.write("\n".join(lines) + "\n")
            paths.append(p)
        compliant, wrote, never, scanned, unreadable, ties = measure_peer_comms(sorted(paths))

    # Corpus-filter assertions: synthetic mtimes, no filesystem. These are what a mutation
    # disabling the cutoff must break — the defect this file exists to prevent.
    _fake = {"old_a": 100.0, "old_b": 150.0, "new_a": 300.0, "new_b": 400.0}
    _kept, _excl = _select_corpus(sorted(_fake), 200.0, mtime_fn=_fake.__getitem__)

    for label, got, want in (
        ("CONTRACT corpus filter keeps only post-cutoff", sorted(_kept), ["new_a", "new_b"]),
        ("CONTRACT corpus filter counts exclusions", _excl, 2),
        ("CONTRACT corpus filter gates on session start, not file mtime",
         _started_pre_norm_is_excluded(), True),
        ("CONTRACT wrote counts only shared writes", wrote, 4),
        ("CONTRACT compliant counts send-before-write", compliant, 1),
        ("CONTRACT never counts wrote-without-send", never, 1),
        # DYNAMIC, not a literal. Hardcoding the fixture count is what made truncation
        # (`paths[:N]`) unobservable — the expected value moved with the defect.
        ("CONTRACT scanned counts every readable transcript", scanned, len(paths) - unreadable),
        ("CONTRACT unreadable counts a real OSError", unreadable, 1),
        ("CONTRACT ties is zero when no send/write share a record", ties, 0),
    ):
        if got != want:
            # Bare label only. Embedding got/want here made declared break sets brittle:
            # a mutant's expected set would have to encode the exact wrong values it produces,
            # so ANY value change minted a new label and read as detection — phantom coverage.
            fails.append(label)
        checked.append(label)
    # Drift guard: the audit iterates CONTRACT_LABELS, so an assertion added here without
    # being listed there would be unauditable — the exact gap this constant closes.
    # SET comparison, not tuple. The first version compared tuples, so merely REORDERING the
    # assertions would fail the guard — and a guard that fires spuriously is a guard that gets
    # disabled. Membership is the invariant; order is not.
    assert set(checked) == set(CONTRACT_LABELS), (
        f"CONTRACT_LABELS out of sync with the assertions actually checked: "
        f"{set(checked) ^ set(CONTRACT_LABELS)}")
    assert len(checked) == len(set(checked)), f"duplicate contract label: {checked}"
    return fails


def _run_main_capture():
    """Run main() under a FRESH stdout buffer. Returns (rc, out).

    `out` is read in `finally`, never discarded on SystemExit. The previous form was
    `except SystemExit: out = ""`, which threw away everything already printed and turned one
    diagnosable argparse failure into two generic contract failures with nothing to read.

    A fresh buffer per call also matters: with one shared buffer, an assertion looking for a
    marker is satisfied by ANY run that emitted it, so a later run printing nothing at all
    passes on an earlier run's output.
    """
    import io, contextlib
    buf = io.StringIO()
    rc = None
    try:
        with contextlib.redirect_stdout(buf):
            rc = main()
    except SystemExit as exc:
        rc = exc.code
    finally:
        out = buf.getvalue()
    return rc, out


# FIXTURE SIZES ARE FROZEN LITERALS, DELIBERATELY NOT DERIVED FROM MIN_N.
# Deriving them (`_AT_FLOOR_N = MIN_N`) would make the test value-invariant: the expectation
# would move with the defect and every MIN_N would pass, which is the failure this pair exists
# to catch. Frozen, they ARE the tracking mechanism -- raise MIN_N to 20 or 10000 without
# touching them and the at-floor run reports NORM-OK-LOWN instead of a verdict, turning the
# suite RED. That is why no separate "operable range" assert is needed, and why one would have
# been worse: a range bound in the production path pages daily via NORM-CHECK-ERROR the moment
# the floor is legitimately raised, and it would itself be uncontrolled (no mutant reaches it).
_AT_FLOOR_N = 10        # == MIN_N today. A verdict must render here.
_BELOW_FLOOR_N = 9      # == MIN_N - 1 today. NORM-OK-LOWN must render here.
_PRE_NORM_N = 3         # pre-norm files in BOTH fixtures, so the cutoff mutant bites on both.


def _main_contract_failures():
    """Assert main()'s OUTPUT, so removing the cutoff CALL is caught.

    Two earlier attempts failed. The first had no test at all. The second extracted
    _select_corpus() and asserted the FUNCTION -- which passes happily while main() simply
    stops calling it, like testing a smoke alarm's battery after someone unplugged the alarm.
    This runs the whole program against a synthetic corpus and reads the number it reports,
    so the call site is what is under test.

    MIN_N IS NO LONGER PATCHED HERE. It used to be forced to 1, which made the NORM-OK-LOWN
    branch unreachable in test -- `MIN_N = 10000` survived the suite GREEN while production
    would have reported "too few to judge" forever and never rendered a verdict. Sizing the
    at-floor fixture at the real floor instead of lowering the floor to the fixture removes the
    patch entirely, which also makes MIN_N a legitimate mutation seam again.
    """
    import tempfile, time
    fails = []
    cutoff_ts = datetime.strptime(NORM_EFFECTIVE["peer-comms"], "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp()

    def rec(name, inp):
        return json.dumps({"message": {"content": [
            {"type": "tool_use", "name": name, "input": inp}]}})

    body = "\n".join([rec("SendMessage", {}),
                      rec("Write", {"file_path": _EX_SHARED + "/real.md"})]) + "\n"

    def _run(n_post):
        """Build an isolated corpus of n_post post-norm + _PRE_NORM_N pre-norm sessions, run
        main() against it, and return (rc, out, flag_after).

        A SEPARATE TemporaryDirectory per run is load-bearing: TRANSCRIPTS is a glob, so one
        shared directory would make the second run see the first run's files and measure a
        denominator that is the sum of both fixtures.
        """
        with tempfile.TemporaryDirectory() as d:
            for i in range(_PRE_NORM_N):
                f = os.path.join(d, f"old{i}.jsonl")
                with open(f, "w") as _fh:
                    _fh.write(body)
                os.utime(f, (cutoff_ts - 86400, cutoff_ts - 86400))
            for i in range(n_post):
                f = os.path.join(d, f"new{i}.jsonl")
                with open(f, "w") as _fh:
                    _fh.write(body)
                os.utime(f, (time.time(), time.time()))

            # _IN_CONTRACT_TEST is snapshotted and RESTORED, not reset to a literal False.
            _orig = {k: globals()[k] for k in ("TRANSCRIPTS", "STATE", "_IN_CONTRACT_TEST")}
            _argv = sys.argv
            try:
                globals()["TRANSCRIPTS"] = os.path.join(d, "*.jsonl")
                globals()["STATE"] = os.path.join(d, "baseline.json")
                sys.argv = ["norm-compliance-monitor"]
                globals()["_IN_CONTRACT_TEST"] = True
                rc, out = _run_main_capture()
            finally:
                _restore_globals(_orig)
                sys.argv = _argv
        return rc, out, globals()["_IN_CONTRACT_TEST"]

    # STRUCTURED assertion, not substrings. An Opus reviewer line-traced six behaviour changes
    # that kept both substrings and stayed GREEN: pct=0.0, renaming the NORM-OK marker,
    # MIN_N=10000, WARN_PCT=0.0, REGRESSION_PP=999.0, and deleting _save_baseline(). main()'s
    # verdict, marker and thresholds were entirely unasserted -- only two numbers inside one
    # string were. NOTE `^NORM-OK:` CARRIES ITS COLON and is matched with re.M: plain
    # `"NORM-OK" in out` is ALSO satisfied by "NORM-OK-LOWN", which would let the
    # "floor always applies" mutant survive; and str.startswith is wrong because a
    # NORM-CHECK-WARN line from _save_baseline can precede the verdict.
    _PARSE = r"^(NORM-\S+):.*?(\d+)/(\d+) = ([\d.]+)%.*?(\d+) pre-norm EXCLUDED"

    def _parse(out):
        m = re.search(_PARSE, out, re.S | re.M)
        return (m.group(1), int(m.group(2)), int(m.group(3)),
                float(m.group(4)), int(m.group(5))) if m else None

    # --- AT THE FLOOR: a verdict must render, and the cutoff must have been applied ---
    rc_at, out_at, flag_at = _run(_AT_FLOOR_N)
    got = _parse(out_at)
    # Label 0 and label 1 are SEPARATE conditions. They used to be appended together under one
    # `if`, i.e. one assertion wearing two names, which can never fail independently and inflates
    # the coverage audit with a phantom.
    if not got or got[4] != _PRE_NORM_N:
        fails.append(MAIN_CONTRACT_LABELS[0])
    if got != ("NORM-OK", _AT_FLOOR_N, _AT_FLOOR_N, 100.0, _PRE_NORM_N) or rc_at != 0:
        fails.append(MAIN_CONTRACT_LABELS[1])

    # --- BELOW THE FLOOR: NORM-OK-LOWN, and still exit 0 (it is not an adverse finding) ---
    rc_lo, out_lo, flag_lo = _run(_BELOW_FLOOR_N)
    got_lo = _parse(out_lo)
    if (not got_lo or got_lo[0] != "NORM-OK-LOWN" or got_lo[2] != _BELOW_FLOOR_N
            or rc_lo != 0):
        fails.append(MAIN_CONTRACT_LABELS[2])

    # --- THE GATE MUST BE RE-ARMED. Without this the fail-open restore is undetectable. ---
    if flag_at is not False or flag_lo is not False:
        fails.append(MAIN_CONTRACT_LABELS[3])

    assert set(MAIN_CONTRACT_LABELS) == {
        MAIN_CONTRACT_LABELS[0], MAIN_CONTRACT_LABELS[1],
        MAIN_CONTRACT_LABELS[2], MAIN_CONTRACT_LABELS[3]}, \
        "MAIN_CONTRACT_LABELS out of sync with this function"
    return fails


def _failing_cases():
    """Labels of predicate cases that currently FAIL. Empty list == suite green."""
    return ([label for label, name, inp, want in _predicate_cases()
             if _is_shared_write(name, inp) != want]
            + _contract_failures()
            + _main_contract_failures())


def mutation_test() -> int:
    """MUTATION CONTROLS — the suite must FAIL when the predicate is deliberately broken.

    WHY (three instances across three concurrent sessions on 2026-08-27, DEC-297 threshold met):
    a passing positive control proves the code handles the cases its author thought of. It is
    silent on the ones they did not. This file's own redirect leg matched `2>/dev/null` and
    `> /dev/null` while every control passed, and the reported rate moved 42 percentage points
    before anyone noticed. A peer session independently hit the same shape: an 11-assertion
    E2E harness at 11/11 PASS whose assertions were state-dependent and green only on a cold run.

    Pattern adopted from that peer: run the mutants on EVERY invocation and refuse to report a
    number if a mutant survives. A green suite proves nothing until a mutant makes it red.
    """
    # Snapshot EVERY global a mutant may patch. Missing one here would leave a mutant live
    # for the rest of the process — a test harness that corrupts production state.
    _MUTABLE = ("_BASH_MUTATES", "_is_shared_path", "_write_targets", "WRITE_TOOLS",
                "_select_corpus", "_classify", "_COUNT_UNREADABLE",
                "_below_floor", "_restore_globals", "_IN_CONTRACT_TEST",
                "_UNPARSED_TARGET_IS_SHARED", "_GIT_WRITE", "_session_start")
    _orig = {k: globals()[k] for k in _MUTABLE}
    # DECLARED BREAK SETS (peer dev-a5, 2026-08-27). Counting breaks is not enough: a mutant
    # that patches a SHARED global can trip assertions it has no business touching, and an
    # EXTRA break looks like better coverage, so a count can never see it. Each mutant declares
    # exactly which assertions it should break; any deviation fails.
    #   MISSING break -> the assertion is dead, or the mutant no longer does what it claims.
    #   EXTRA break   -> the mutant is CONTAMINATING rather than controlling (state leakage,
    #                    over-broad patch). This is the direction that hides.
    # These sets were reviewed on the merits, not transcribed from a run: each mutant breaks
    # exactly the cases whose verdict depends on the global it patches.
    expected = {
        "drop (?!&|/dev/) redirect guard":
            {"2>/dev/null excluded", "> /dev/null excluded"},
        # Contract assertions appear in several sets: a mutant that changes what counts as a
        # shared write NECESSARILY changes the measured buckets. That is correct behaviour,
        # not contamination — dev-a5's rule applied: on a MISMATCH, suspect the declaration
        # before the code. These entries were added after exactly that misdiagnosis.
        "neuter the shared-root allowlist (everything counts as shared)":
            {"cp shared->scratch excluded (target is scratch)",
             "read shared, redirect to scratch, excluded",
             "scratch-only Bash excluded", "scratchpad Write excluded",
             "outside every shared root excluded", "dot-cache excluded",
             "relative Bash target excluded (cwd unknown)",
             "relative cp target excluded (cwd unknown)",
             "CONTRACT wrote counts only shared writes",
             "CONTRACT never counts wrote-without-send"},
        "neuter _BASH_MUTATES entirely":
            {"Bash sed -i counts", "Bash tee counts",
             "cp scratch->shared counts (target is shared)",
             "read scratch, redirect to shared, counts",
             "tee to shared counts even when reading scratch"},
        "revert to any-path-anywhere (ignore write target)":
            {"cp shared->scratch excluded (target is scratch)",
             "read shared, redirect to scratch, excluded"},
        "neuter WRITE_TOOLS (write-tool path blind)":
            # wrote=0 on BOTH fixtures -> main() returns NORM-CHECK-ERROR before the floor
            # branch is reached, so every main()-output label breaks. Correct, not contamination.
            {"CONTRACT main() applies the corpus cutoff", "CONTRACT main() measures only post-cutoff sessions",
             "CONTRACT main() reports NORM-OK-LOWN below the floor",
             "shared Write counts",
             "second shared root counts (~/bin)",
             "third shared root counts (~/.claude/hooks)",
             "CONTRACT wrote counts only shared writes",
             "CONTRACT compliant counts send-before-write",
             "CONTRACT never counts wrote-without-send"},
        "_BASH_MUTATES matches everything (read-only reads as write)":
            # It now also flips _UNPARSED_TARGET_IS_SHARED, so every read-only Bash case with no
            # parseable target reads as a shared write -- including the read-only vcs case.
            {"2>/dev/null excluded", "> /dev/null excluded", "Bash read-only excluded",
             "descriptor dup 2>&1 excluded", "read-only pipeline excluded",
             "vcs status excluded"},
        "treat a non-write tool as a write tool":
            {"non-write tool excluded"},
        "measure never counts a compliant session":
            # pct=0.0 -> NORM-ADVERSE at the floor, so the verdict label breaks. The CUTOFF
            # label does NOT: `excluded` is still 3 in the adverse line. It was only ever
            # credited here because labels 0 and 1 were appended together under one `if` --
            # one assertion wearing two names. Splitting them made the phantom visible.
            # The below-floor run still prints LOWN (9 < 10 returns before the pct branch).
            {"CONTRACT compliant counts send-before-write",
             "CONTRACT main() measures only post-cutoff sessions"},
        "unreadable files silently skipped instead of counted":
            {"CONTRACT unreadable counts a real OSError",
             "CONTRACT scanned counts every readable transcript"},
        "measure misclassifies a late broadcast as a tie":
            {"CONTRACT ties is zero when no send/write share a record"},
        "gut the allowlist to a single root (partial gutting)":
            # Only the roots OTHER than SHARED_ROOTS[0] disappear. Paths outside every root
            # (scratchpad, .cache) are excluded either way and must NOT appear here.
            {"second shared root counts (~/bin)",
             "third shared root counts (~/.claude/hooks)"},
        "corpus filter disabled (reintroduces the 27x undated-denominator error)":
            # It replaces _select_corpus wholesale, so the session-start assertion -- which
            # calls _select_corpus -- necessarily breaks too. Correct, not contamination.
            {"CONTRACT corpus filter keeps only post-cutoff",
             "CONTRACT corpus filter counts exclusions",
             "CONTRACT corpus filter gates on session start, not file mtime",
             "CONTRACT main() applies the corpus cutoff",
             "CONTRACT main() measures only post-cutoff sessions",
             # the 3 pre-norm files re-enter BOTH fixtures, so the below-floor run measures
             # 9+3=12 >= MIN_N and renders a verdict instead of NORM-OK-LOWN.
             "CONTRACT main() reports NORM-OK-LOWN below the floor"},
        # The floor never applies -> the below-floor run renders a verdict. The at-floor run is
        # untouched (10 >= 10 renders a verdict either way), so this breaks exactly one label.
        "low-N floor never applies (NORM-OK-LOWN unreachable)":
            {"CONTRACT main() reports NORM-OK-LOWN below the floor"},
        # The floor always applies -> the at-floor run reports LOWN instead of a verdict. The
        # cutoff label survives: `excluded` is still 3 in the LOWN line.
        "low-N floor always applies (a verdict never renders)":
            {"CONTRACT main() measures only post-cutoff sessions"},
        # The teardown silently drops the re-entry flag -- the fail-open shape that left the
        # suite green when the restore line was DELETED outright.
        "revert corpus gating to file mtime (session-start blind)":
            {"CONTRACT corpus filter gates on session start, not file mtime"},
        "neuter the vcs-write leg (a bare repo mutation is not a shared write)":
            {"bare vcs-commit counts", "bare vcs-push counts"},
        "contract-test teardown never re-arms the mutation gate":
            {"CONTRACT main() re-arms the mutation gate after the contract test"},
    }
    # BASE STATE FIRST. If the unmutated code already fails an assertion, say so plainly and
    # stop. Without this, deleting the corpus cutoff surfaced as "mutant 'drop /dev/ guard'
    # CONTAMINATES" — the right assertion was named, but wrapped in a message that sends the
    # reader after a mutant rather than the deleted call.
    _base = _failing_cases()
    if _base:
        for _f in _base:
            print(f"SUITE FAIL (unmutated): {_f}")
        print("Base assertions fail before any mutant runs — fix the code, not the mutants. "
              "MUTATION PHASE NOT RUN (0 mutants executed).")
        return 2

    mutants = [
        # M1: drop the /dev/ guard — the exact live defect that cost 42pp.
        ("drop (?!&|/dev/) redirect guard",
         lambda: (globals().__setitem__("_BASH_MUTATES", re.compile(
             r"(^|[;&|]|\s)(tee\b|sed\s+-i|cp\b|mv\b|install\b|truncate\b|dd\b)|>>?\s*[^&\s]",
             re.M)),
             globals().__setitem__("_UNPARSED_TARGET_IS_SHARED", True))),
        # M2: neuter the scratchpad exclusion — scratch writes would count as shared.
        ("neuter the shared-root allowlist (everything counts as shared)",
         lambda: globals().__setitem__("_is_shared_path", lambda p: bool(p))),
        # M3: neuter mutation detection entirely — no Bash command is a write.
        ("neuter _BASH_MUTATES entirely",
         lambda: globals().__setitem__("_BASH_MUTATES", re.compile(r"(?!x)x"))),
        # M4: revert to "any non-scratch path in the command counts" — the exact CRITICAL a
        # reviewer found after M1-M3 were already passing. Proof that the mixed-path cases
        # above actually bite, not just that they exist.
        ("revert to any-path-anywhere (ignore write target)",
         lambda: globals().__setitem__("_write_targets",
                                       lambda cmd: re.findall(r"(/\S+)", cmd))),
        # M5-M7 added 2026-08-27 after a coverage audit found 5 of 16 assertions had NO mutant
        # that broke them — "an uncontrolled assertion wearing a green tick" (peer dev-a5,
        # Dart FW6N2r7afxmq). M1-M4 all patched Bash-path globals, so every Write-path and
        # read-only negative was unguarded. The audit is now enforced below, not just fixed.
        ("neuter WRITE_TOOLS (write-tool path blind)",
         lambda: globals().__setitem__("WRITE_TOOLS", frozenset())),
        ("_BASH_MUTATES matches everything (read-only reads as write)",
         lambda: (globals().__setitem__("_BASH_MUTATES", re.compile(r"")),
                  globals().__setitem__("_UNPARSED_TARGET_IS_SHARED", True))),
        # Was SendMessage, which carries NO file_path -- under an allowlist the empty path is
        # simply not shared, so the mutant could not flip any verdict and silently survived.
        # Read carries a real shared path, so promoting it to a write tool is visible.
        ("treat a non-write tool as a write tool",
         lambda: globals().__setitem__("WRITE_TOOLS",
                                       set(_orig["WRITE_TOOLS"]) | {"Read"})),
        # C-mutants: these target the MEASUREMENT, not the predicate. Before the contract
        # assertions existed, all three left the suite green (verified 2026-08-27).
        ("measure never counts a compliant session",
         lambda: globals().__setitem__(
             "_classify", lambda fs, fw: "late" if fs is not None else "never")),
        ("measure misclassifies a late broadcast as a tie",
         lambda: globals().__setitem__(
             "_classify", lambda fs, fw: "never" if fs is None
             else ("compliant" if fs < fw else "tie"))),
        ("unreadable files silently skipped instead of counted",
         lambda: globals().__setitem__("_COUNT_UNREADABLE", False)),
        ("gut the allowlist to a single root (partial gutting)",
         lambda: globals().__setitem__(
             "_is_shared_path",
             lambda p: bool(p) and os.path.realpath(p).startswith(SHARED_ROOTS[0] + os.sep))),
        # THE 27x DEFECT, as a mutant. Previously unreachable: the filter lived inline in
        # main() and no assertion touched it.
        ("corpus filter disabled (reintroduces the 27x undated-denominator error)",
         lambda: globals().__setitem__("_select_corpus", lambda a, c, mtime_fn=None: (list(a), 0))),
        # THE LOW-N FLOOR, in both directions. Previously unreachable: the decision was inline
        # in main() AND the fixture patched MIN_N to 1, so no assertion could see it.
        ("low-N floor never applies (NORM-OK-LOWN unreachable)",
         lambda: globals().__setitem__("_below_floor", lambda wrote: False)),
        ("low-N floor always applies (a verdict never renders)",
         lambda: globals().__setitem__("_below_floor", lambda wrote: True)),
        # THE FAIL-OPEN TEARDOWN. Drops just the re-entry flag from the restore, which is what
        # deleting the old reset line did -- and that deletion left the suite GREEN.
        # The newly-added leg needs its own control, or its green tick is not evidence.
        # The mtime->session-start fix is a behaviour change, so it needs a control that
        # goes red when it is reverted -- otherwise it is a dead change wearing a green tick.
        ("revert corpus gating to file mtime (session-start blind)",
         lambda: globals().__setitem__("_session_start", os.path.getmtime)),
        ("neuter the vcs-write leg (a bare repo mutation is not a shared write)",
         lambda: globals().__setitem__("_GIT_WRITE", re.compile(r"(?!x)x"))),
        ("contract-test teardown never re-arms the mutation gate",
         lambda: globals().__setitem__(
             "_restore_globals",
             lambda snap: globals().update(
                 {k: v for k, v in snap.items() if k != "_IN_CONTRACT_TEST"}))),
    ]  # NOTE: this mutant patches the FUNCTION. main()'s output assertions above are what
       # catch the CALL being removed — the defect two earlier attempts missed.
    # A stale key in `expected` was SILENT: nothing iterated expected.keys(), so a
    # declaration for a deleted mutant sat unnoticed. Renaming was already loud; this closes
    # the reverse direction. (Opus reviewer, verified 2026-08-27.)
    assert set(expected) == {lbl for lbl, _ in mutants}, (
        f"declared break sets out of sync with mutants: "
        f"{set(expected) ^ {lbl for lbl, _ in mutants}}")
    globals()["_MUTANT_COUNT"] = len(mutants)   # derived, never hardcoded in the report
    survived = []
    mismatches = []
    broken_by_some_mutant = set()
    try:
        for label, apply_mutant in mutants:
            globals().update(_orig)          # clean slate before each mutant
            apply_mutant()
            broke = set(_failing_cases())
            broken_by_some_mutant.update(broke)
            if not broke:
                survived.append(label)
            want = expected.get(label)
            if want is None:
                mismatches.append((label, [], [], True))
            elif broke != want:
                mismatches.append((label, sorted(broke - want), sorted(want - broke), False))
    finally:
        globals().update(_orig)
        # Restoration is itself verified — a harness that silently fails to restore is the
        # defect class this whole file documents.
        assert all(globals()[k] is _orig[k] for k in _MUTABLE), \
            "mutation harness failed to restore production globals"

    if survived:
        for label in survived:
            print(f"MUTATION-CONTROL FAIL: suite stayed green under mutant '{label}' — "
                  f"it cannot detect this class of defect")
        return 2

    # CONTAMINATION AUDIT — EXTRA breaks are the direction that hides, because they read as
    # better coverage. A mutant patching a shared global can trip assertions it has no
    # business affecting (state leakage, over-broad patch), and a break COUNT cannot see it.
    if mismatches:
        for label, extra, missing, undeclared in mismatches:
            if undeclared:
                print(f"MUTATION-CONTROL FAIL: mutant '{label}' has NO declared break set — "
                      f"an undeclared mutant cannot be checked for contamination")
                continue
            if extra:
                print(f"MUTATION-CONTROL FAIL: mutant '{label}' CONTAMINATES — it also broke "
                      f"{extra}, which it has no business affecting")
            if missing:
                print(f"MUTATION-CONTROL FAIL: mutant '{label}' did NOT break {missing} — "
                      f"that assertion is dead, or the mutant no longer does what it claims")
        return 2

    # COVERAGE AUDIT. Every assertion must be broken by at least one mutant, or it is an
    # UNCONTROLLED assertion: it passes, but nothing demonstrates it *can* fail, so it
    # provides no evidence. Measured 2026-08-27: 5 of 16 were uncontrolled because all four
    # mutants then existing patched Bash-path globals only. Enforced rather than fixed once,
    # so adding a case without adding a mutant that breaks it fails the suite.
    all_assertions = [label for label, _, _, _ in _predicate_cases()] + list(CONTRACT_LABELS) + list(MAIN_CONTRACT_LABELS)
    uncontrolled = [label for label in all_assertions
                    if label not in broken_by_some_mutant]
    if uncontrolled:
        for label in uncontrolled:
            print(f"MUTATION-CONTROL FAIL: assertion '{label}' is UNCONTROLLED — no mutant "
                  f"breaks it, so its green tick is not evidence")
        return 2
    return 0


def self_test() -> int:
    """Positive AND negative control. A check that cannot be shown failing is not a check."""
    def rec(name, inp):
        return {"message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}

    ok = True

    def check(label, got, want):
        nonlocal ok
        if got != want:
            print(f"SELF-TEST FAIL {label}: got {got!r}, want {want!r}")
            ok = False

    # --- extractor: shape tolerance -------------------------------------------------
    check("positive control (extract SendMessage)",
          list(_tool_uses(rec("SendMessage", {}))), [("SendMessage", {})])
    check("malformed guard (content not a list)",
          list(_tool_uses({"message": {"content": "not-a-list"}})), [])
    _malformed_input = {"message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": "oops"}]}}
    check("malformed guard (input not a dict)",
          list(_tool_uses(_malformed_input)), [("Bash", {})])

    # --- _is_shared_write: the three edge cases, each in BOTH polarities -------------
    # --- predicate cases: LOOP over the single source of truth -------------------
    # Previously 11 of these were hand-duplicated here, so _predicate_cases() was never
    # asserted UNMUTATED — the 5 mixed-path cases (the CRITICAL a reviewer found) appeared
    # ZERO times in self_test, and the "single source of truth" docstring was false as
    # printed. Found by an Opus reviewer, verified by mutation, 2026-08-27.
    for _label, _name, _inp, _want in _predicate_cases():
        check(_label, _is_shared_write(_name, _inp), _want)
    for _f in _contract_failures():
        check(_f, "FAILED", "pass")

    # --- discriminating control: a no-op predicate would score everything the same ---
    if _is_shared_write("Write", {"file_path": _EX_SHARED + "/a.md"}) == \
       _is_shared_write("Write", {"file_path": "/tmp/scratchpad/a.md"}):
        print("SELF-TEST FAIL: predicate does not discriminate shared vs scratch")
        ok = False

    if not ok:
        print("SELF-TEST FAILED")
        return 2
    # A green suite is not evidence until a mutant makes it red.
    if mutation_test() != 0:
        print("SELF-TEST FAILED: assertions pass but mutation controls did not")
        return 2
    print(f"SELF-TEST PASS: {len(_predicate_cases())} predicate cases in both polarities, "
          f"plus {globals().get('_MUTANT_COUNT', '?')} mutation controls each proven to "
          f"turn the suite red")
    return 0


if __name__ == "__main__":
    sys.exit(main())
