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
# Usage: git-commit-verified.sh -m <message-file|-> -- <path> [<path>...]
#        message from a FILE or stdin, never an argv string: a commit message containing
#        backticks has already been mangled by shell substitution in this workspace once.
set -uo pipefail

MSGSRC=""; PATHS=()
while [ $# -gt 0 ]; do
    case "$1" in
        -m) MSGSRC="${2:?-m needs a file or -}"; shift 2 ;;
        --) shift; PATHS=("$@"); break ;;
        *)  printf 'usage: %s -m <msgfile|-> -- <path>...\n' "$0" >&2; exit 2 ;;
    esac
done
[ -n "$MSGSRC" ] || { printf 'no -m\n' >&2; exit 2; }
[ "${#PATHS[@]}" -gt 0 ] || { printf 'no paths\n' >&2; exit 2; }

MSGFILE="$(mktemp)"; trap 'rm -f "$MSGFILE"' EXIT
if [ "$MSGSRC" = "-" ]; then cat > "$MSGFILE"; else cat "$MSGSRC" > "$MSGFILE"; fi
[ -s "$MSGFILE" ] || { printf 'empty commit message\n' >&2; exit 2; }

# --- stage, retrying only the LOUD failure -------------------------------------------------
staged=0
for attempt in 1 2 3 4 5; do
    if git add -- "${PATHS[@]}" 2>/dev/null; then staged=1; break; fi
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

# every requested path must appear in the commit, or the stage was clobbered mid-flight
missing=()
present="$(git show --name-only --format="" HEAD)"
for p in "${PATHS[@]}"; do
    printf '%s\n' "$present" | grep -qF "$(basename "$p")" || missing+=("$p")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'VERIFY FAILED: committed, but these paths are absent from the commit: %s\n' \
        "${missing[*]}" >&2
    exit 1
fi

printf 'verified %s  files=%s\n' "$(git rev-parse --short HEAD)" \
    "$(printf '%s\n' "$present" | grep -c .)"
