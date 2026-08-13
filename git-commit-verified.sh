#!/usr/bin/env bash
# Commit, then PROVE the commit contains what you staged.
#
# WHY THIS EXISTS, measured not assumed. ~/dev/share takes ~62 commits per 12h from three
# concurrent sessions plus a session-end auto-commit. Two failure modes were observed on
# 2026-08-12:
#
#   LOUD   -- `.git/index.lock` exists; the commit refuses. Recoverable, and I hit it twice.
#   SILENT -- a peer's `git add`/reset lands between YOUR stage and YOUR commit, so the commit
#             succeeds with nothing in it. Commit 8015cf6 in this repo has a tree BYTE-IDENTICAL
#             to its parent while its message reads "handoff: point at the recovery document
#             (context hit 100%)". Someone believed they had recorded a handoff at the moment
#             their context ran out. Nothing was recorded, and git reported success.
#
# The silent mode is the dangerous one and it is invisible to every existing check: `git commit`
# exits 0, the message is in the log, and only the TREE reveals that nothing changed. This is
# the same substitution this workspace keeps finding -- the operation's own return value stood
# in for the outcome (§12 / GP-52).
#
# THREE DEFECTS FOUND BY AUDIT 2026-08-12, all in the verifier itself:
#
#   1. BASENAME MATCHING. The presence check compared `basename "$p"` against the commit's file
#      list, so committing `b/notes.md` satisfied a request for `a/notes.md`. The tool built to
#      prove "the paths you asked for are in the commit" could pass on a DIFFERENT file. This is
#      the workspace's own JOIN-error class -- mismatched populations, here basename vs path --
#      inside the control meant to catch it. Now compares repo-relative paths exactly.
#
#   2. FOREIGN STAGED WORK. `git add` on top of an index a PEER has already staged sweeps their
#      work into your commit under your message and your authorship. Three sessions share this
#      working tree. CLAUDE.md's working-tree provenance rule (L-517) forbids committing content
#      you did not author; nothing enforced it. Now aborts on foreign staged paths.
#
#   3. IGNORED-BUT-UNTRACKED PATHS. Plain `git add` silently skips an untracked file under an
#      ignored directory -- and the old presence check, matching on basename, could still pass.
#      Deliberately NOT fixed with a blanket `-f`: forcing past a user's ignore rule is a
#      permission decision, not a convenience. Detected and refused unless --force-ignored is
#      passed explicitly.
#
# Usage: git-commit-verified.sh [--force-ignored] -m <message-file|-> -- <path> [<path>...]
#        message from a FILE or stdin, never an argv string: a commit message containing
#        backticks has already been mangled by shell substitution in this workspace once.
set -uo pipefail

MSGSRC=""; PATHS=(); FORCE_IGNORED=0; PRIOR_ART=""
while [ $# -gt 0 ]; do
    case "$1" in
        -m) MSGSRC="${2:?-m needs a file or -}"; shift 2 ;;
        --force-ignored) FORCE_IGNORED=1; shift ;;
        --prior-art) PRIOR_ART="${2:?--prior-art needs a value}"; shift 2 ;;
        --) shift; PATHS=("$@"); break ;;
        *)  printf 'usage: %s [--force-ignored] [--prior-art <what>] -m <msgfile|-> -- <path>...\n' "$0" >&2; exit 2 ;;
    esac
done
[ -n "$MSGSRC" ] || { printf 'no -m\n' >&2; exit 2; }
[ "${#PATHS[@]}" -gt 0 ] || { printf 'no paths\n' >&2; exit 2; }

MSGFILE="$(mktemp)"; trap 'rm -f "$MSGFILE"' EXIT
if [ "$MSGSRC" = "-" ]; then cat > "$MSGFILE"; else cat "$MSGSRC" > "$MSGFILE"; fi
[ -s "$MSGFILE" ] || { printf 'empty commit message\n' >&2; exit 2; }

# --- SESSION ATTRIBUTION AS A GIT TRAILER --------------------------------------------------
# The first version of this wrote session attribution ONLY to a ledger under ~/.metrics. That
# was a dual-write of information belonging to the commit, and it fails exactly where it is
# needed: outside the repo, so never pushed, never cloned, gone with the machine -- and this
# session lost its own memory three separate ways today.
#
# Git TRAILERS are the established mechanism (and there is prior art specifically for AI session
# attribution, which one search would have found before I built the ledger). A trailer travels
# with the commit across forks and mirrors, is readable by any git client with no proprietary
# tooling, and `git interpret-trailers` parses it. So the trailer is AUTHORITATIVE and the
# ledger below is a rebuildable cross-repo INDEX -- the same split SLSA draws between an
# attestation and its distribution. Write one system, derive the other.
SID="${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-unknown}}"
if ! grep -q '^Claude-Session:' "$MSGFILE"; then
    git interpret-trailers --in-place --trailer "Claude-Session: $SID" "$MSGFILE" 2>/dev/null \
        || printf '\nClaude-Session: %s\n' "$SID" >> "$MSGFILE"
fi

# --- PRIOR-ART PROVENANCE ------------------------------------------------------------------
# WHY. Measured 2026-08-13: eleven build commits landed before ANY prior-art check ran, and the
# checks that did run were retroactive, prompted by the user rather than by the build. The
# omission left no artifact, so compliance was unmeasurable until someone hand-joined commit
# timestamps against a discovery log -- and that join is a heuristic, since the discovery log
# carries no session key and no link to a commit.
#
# A trailer makes it DERIVABLE instead: `git log --grep='^Prior-Art:'` answers "was prior art
# consulted for this change" from the commit itself, with no heuristic and no second system to
# keep in sync. SLSA models the same idea as an attestation's `materials`/`resolvedDependencies`
# -- the inputs a build consumed; this is that idea at commit granularity.
#
# DELIBERATELY NOT MANDATORY HERE. Requiring it would raise the cost of the compliant path,
# which is the pressure that produces the skipping (Reason's GEMS: these are violations, not
# lapses). It records what was done so the omission becomes VISIBLE and countable. Making it
# blocking is a separate, user-authorised decision.
if [ -n "$PRIOR_ART" ] && ! grep -q '^Prior-Art:' "$MSGFILE"; then
    git interpret-trailers --in-place --trailer "Prior-Art: $PRIOR_ART" "$MSGFILE" 2>/dev/null \
        || printf 'Prior-Art: %s\n' "$PRIOR_ART" >> "$MSGFILE"
fi

# --- refuse to absorb a peer's staged work (defect 2) ---------------------------------------
# Anything already in the index that is NOT one of MY paths belongs to another session. Adding
# to that index and committing would attribute their work to me.
#
# THIS GUARD MUST FAIL CLOSED. An external review flagged `pipefail` here as a maintainability
# nit; tracing it found the real defect pointing the other way. If `git diff --cached` fails --
# and index.lock contention, the very condition this tool exists for, is the likeliest cause --
# a captured-output check reads back EMPTY, "no foreign paths" is inferred from silence, and the
# guard evaporates exactly when it is needed. Absence of evidence read as evidence of absence,
# in the control against absorbing a peer's work. Read the status explicitly and refuse.
CACHED="$(git diff --cached --name-only 2>/dev/null)"; CACHED_RC=$?
if [ "$CACHED_RC" -ne 0 ]; then
    printf 'ABORT: cannot read the index to check for foreign staged work (rc=%s).\n' "$CACHED_RC" >&2
    printf 'UNKNOWN is never a pass -- refusing rather than committing blind.\n' >&2
    exit 1
fi
FOREIGN="$(printf '%s\n' "$CACHED" | while IFS= read -r s; do
    [ -n "$s" ] || continue
    for p in "${PATHS[@]}"; do
        case "$s" in "$p"|"$p"/*) continue 2 ;; esac
    done
    printf '%s\n' "$s"
done)"
if [ -n "$FOREIGN" ]; then
    printf 'ABORT: the index already holds staged paths that are not yours:\n%s\n' "$FOREIGN" >&2
    printf 'A peer staged these. Committing would attribute their work to you (L-517).\n' >&2
    printf 'Resolve deliberately -- this tool will not guess.\n' >&2
    exit 1
fi

# --- refuse to silently skip ignored-but-untracked paths (defect 3) -------------------------
SKIPPED=""
for p in "${PATHS[@]}"; do
    [ -e "$p" ] || continue
    git ls-files --error-unmatch -- "$p" >/dev/null 2>&1 && continue   # already tracked: fine
    git check-ignore -q -- "$p" && SKIPPED="$SKIPPED $p"
done
if [ -n "$SKIPPED" ] && [ "$FORCE_IGNORED" -eq 0 ]; then
    printf 'ABORT: untracked and IGNORED, so `git add` would skip them silently:%s\n' "$SKIPPED" >&2
    printf 'Re-run with --force-ignored ONLY if overriding the ignore rule is intended.\n' >&2
    exit 1
fi
ADDFLAGS=(--); [ "$FORCE_IGNORED" -eq 1 ] && ADDFLAGS=(-f --)

# --- stage, retrying only the LOUD failure -------------------------------------------------
staged=0
for attempt in 1 2 3 4 5; do
    if git add "${ADDFLAGS[@]}" "${PATHS[@]}" 2>/dev/null; then staged=1; break; fi
    if [ -e "$(git rev-parse --git-dir)/index.lock" ]; then
        sleep $((attempt * 2))       # a peer holds it; back off
        continue
    fi
    break                            # a real error, not contention
done
[ "$staged" -eq 1 ] || { printf 'STAGE FAILED after retries\n' >&2; exit 1; }

BEFORE_TREE="$(git rev-parse HEAD^{tree} 2>/dev/null || echo none)"
BEFORE_HEAD="$(git rev-parse HEAD 2>/dev/null || echo none)"

git commit -q -F "$MSGFILE" 2>&1 || { printf 'COMMIT FAILED (hook or nothing staged)\n' >&2; exit 1; }

# --- VERIFY: the commit's own exit code proves nothing -------------------------------------
AFTER_TREE="$(git rev-parse HEAD^{tree})"
AFTER_HEAD="$(git rev-parse HEAD)"

if [ "$AFTER_HEAD" = "$BEFORE_HEAD" ]; then
    printf 'VERIFY FAILED: HEAD did not move\n' >&2; exit 1
fi
if [ "$AFTER_TREE" = "$BEFORE_TREE" ]; then
    printf 'VERIFY FAILED: EMPTY COMMIT -- tree identical to parent. This is the 8015cf6 mode:\n' >&2
    printf '  git reported success and recorded nothing. HEAD=%s\n' "$AFTER_HEAD" >&2
    exit 1
fi

# Every requested path must appear in the commit, or the stage was clobbered mid-flight.
# Compare REPO-RELATIVE PATHS, never basenames (defect 1): `b/notes.md` must not satisfy a
# request for `a/notes.md`. A path may name a directory, so a prefix match counts.
missing=()
present="$(git show --name-only --format="" HEAD)"
for p in "${PATHS[@]}"; do
    # A DIRECTORY IS PRESENT IF *ANY* FILE UNDER IT IS IN THE COMMIT. The previous version took
    # `git ls-files -- "$p" | head -1` -- the alphabetically FIRST tracked file under the
    # directory -- and tested only that one. Committing a directory in which some other file
    # changed then reported the directory ABSENT. A /simplify review flagged this, and the tool
    # demonstrated it on the very commit that registered the finding: both files were committed
    # correctly and it still exited 1. A false alarm on a successful commit is worse than no
    # check, because it trains the operator to ignore the verifier.
    mapfile -t rels < <(git ls-files --full-name -- "$p" 2>/dev/null)
    [ "${#rels[@]}" -gt 0 ] || rels=("$p")
    hit=0
    for rel in "${rels[@]}"; do
        while IFS= read -r f; do
            case "$f" in "$rel"|"$rel"/*) hit=1; break 2 ;; esac
        done <<< "$present"
    done
    [ "$hit" -eq 1 ] || missing+=("$p")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'VERIFY FAILED: committed, but these paths are absent from the commit: %s\n' \
        "${missing[*]}" >&2
    exit 1
fi

# --- SESSION-ATTRIBUTED OUTPUT LEDGER ------------------------------------------------------
# WHY. Compaction dropped this session's knowledge that it had BUILT `~/bin/discovery-audit`.
# I learned of it from a peer whose weekly cron had come to depend on it, and had to verify my
# own authorship from git. A conversation summary is a lossy dual-write of what a session
# produced; git is the durable record and it is not session-attributed. This closes that:
# "what did this session build" becomes a question answered by READING, not by remembering, and
# it survives compaction, restart, and an unexpected termination -- all three of which happened
# today.
#
# It also supplies the join key a peer measured as missing estate-wide: logs record occurrence
# without attribution, so they cannot be asked "did session X do this?". Commits could not be
# either, until now.
LEDGER="$HOME/.metrics/commits-by-session.jsonl"
mkdir -p "$(dirname "$LEDGER")" 2>/dev/null || true
SHORT="$(git rev-parse --short HEAD)"
python3 - "$LEDGER" "$SID" "$(git rev-parse --show-toplevel)" "$(git rev-parse HEAD)" \
         "$(printf '%s\n' "$present" | grep -c .)" "${PATHS[@]}" <<'EOP' 2>/dev/null || true
import json, sys, datetime
led, sid, repo, sha, nfiles, *paths = sys.argv[1:]
with open(led, "a") as f:
    f.write(json.dumps({
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": sid, "repo": repo, "sha": sha,
        "files": int(nfiles), "paths": paths}) + "\n")
EOP

# IMMEDIATE CONSUMER, not a log for later, and it reads the AUTHORITATIVE source. Counting the
# ledger would have made the ledger the truth and reintroduced the dual-write; counting the
# trailers in git means the number cannot disagree with the commits. `--grep` must precede any
# `--`, or git silently ignores it.
# NOT `-F`. Fixed-strings makes `^` and `$` literal characters, so the anchored pattern below
# matches nothing and the counter reports 0 forever -- a consumer that looks alive and is dead,
# which is this session's whole subject. Caught by a fixture that compared the tool's number
# against `git log`'s own; it would not have been caught by reading the line.
PRIOR="$(git log --grep="^Claude-Session: $SID\$" --format=%H 2>/dev/null | grep -c . || echo 0)"
printf 'verified %s  files=%s  [session commit %s]\n' "$SHORT" \
    "$(printf '%s\n' "$present" | grep -c .)" "${PRIOR:-?}"
