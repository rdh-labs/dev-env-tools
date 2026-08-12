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

MSGSRC=""; PATHS=(); FORCE_IGNORED=0
while [ $# -gt 0 ]; do
    case "$1" in
        -m) MSGSRC="${2:?-m needs a file or -}"; shift 2 ;;
        --force-ignored) FORCE_IGNORED=1; shift ;;
        --) shift; PATHS=("$@"); break ;;
        *)  printf 'usage: %s [--force-ignored] -m <msgfile|-> -- <path>...\n' "$0" >&2; exit 2 ;;
    esac
done
[ -n "$MSGSRC" ] || { printf 'no -m\n' >&2; exit 2; }
[ "${#PATHS[@]}" -gt 0 ] || { printf 'no paths\n' >&2; exit 2; }

MSGFILE="$(mktemp)"; trap 'rm -f "$MSGFILE"' EXIT
if [ "$MSGSRC" = "-" ]; then cat > "$MSGFILE"; else cat "$MSGSRC" > "$MSGFILE"; fi
[ -s "$MSGFILE" ] || { printf 'empty commit message\n' >&2; exit 2; }

# --- refuse to absorb a peer's staged work (defect 2) ---------------------------------------
# Anything already in the index that is NOT one of MY paths belongs to another session. Adding
# to that index and committing would attribute their work to me.
FOREIGN="$(git diff --cached --name-only 2>/dev/null | while IFS= read -r s; do
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
    rel="$(git ls-files --full-name -- "$p" 2>/dev/null | head -1)"
    [ -n "$rel" ] || rel="$p"
    hit=0
    while IFS= read -r f; do
        case "$f" in "$rel"|"$rel"/*) hit=1; break ;; esac
    done <<< "$present"
    [ "$hit" -eq 1 ] || missing+=("$p")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'VERIFY FAILED: committed, but these paths are absent from the commit: %s\n' \
        "${missing[*]}" >&2
    exit 1
fi

printf 'verified %s  files=%s\n' "$(git rev-parse --short HEAD)" \
    "$(printf '%s\n' "$present" | grep -c .)"
