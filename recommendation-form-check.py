#!/usr/bin/env python3
"""recommendation-form-check — measure GP-38 recommendation-form conformance on
`You: [decision]` / `[approval]` tails across session transcripts.

WHY: askuserquestion_rec_gate fires on the AskUserQuestion TOOL only. Prose tails —
the majority path — are covered by no gate (Dart FbvfmYiFU9qd, recurrence #4).

The matcher is proven in BOTH polarities before any corpus number is printed; if a
control fails the script ABORTS, so a number can never come from an unproven matcher.
An earlier bare `^You:` anchor silently missed every BOLD `**You:**` tail and
under-counted by 11% — CLAUDE.md's tail convention explicitly accepts bold markup.

Usage: recommendation-form-check.py '~/.claude/projects/-home-ichardart-dev/*.jsonl'
"""
import re, json, glob, sys

TAIL = re.compile(r'^\s*(?:\*\*)?You:?(?:\*\*)?:?\s*\[(decision|approval)\]', re.M)
REC  = re.compile(r'\(Recommended\)')
FENCE= re.compile(r'```.*?```', re.S)

def fires(text):
    """True = NON-CONFORMING (decision/approval handback with no (Recommended) label)."""
    if not TAIL.search(text):
        return False
    stripped = FENCE.sub('', text)          # a label inside a code fence is not a real label
    return not REC.search(stripped)

CONTROLS = [
 # (name, text, expect_fire)
 ("POS conforming-token-missing-label", "work\n\nYou: [decision] Pick a or b — I recommend a.", True),
 ("POS approval-missing-label",         "x\n\nYou: [approval] Authorize the hook edit.", True),
 ("NEG label-present",                  "x\n\nYou: [decision] Pick:\n- a (Recommended) — cheaper\n- b — slower", False),
 ("NEG nothing-token",                  "x\n\nYou: Nothing — complete.", False),
 ("NEG action-token",                   "x\n\nYou: [action] Run the migration.", False),
 ("EDGE label-only-inside-code-fence",  "x\n```\nfoo (Recommended) bar\n```\n\nYou: [decision] a or b?", True),
 ("EDGE bold-tail-missing-label",       "x\n\n**You:** [decision] a or b?", True),
 ("EDGE bold-tail-with-label",          "x\n\n**You:** [decision] a (Recommended) or b", False),
 ("NEG prose-mentions-decision",        "I made a [decision] here. You: Nothing.", False),
]
ok = 0
print("CONTROLS (both polarities):")
for name, text, exp in CONTROLS:
    got = fires(text)
    good = got == exp
    ok += good
    print("  %-38s expect=%-5s got=%-5s %s" % (name, exp, got, "PASS" if good else "*** FAIL ***"))
print("  %d/%d passed" % (ok, len(CONTROLS)))
if ok != len(CONTROLS):
    print("  ABORT: matcher unproven, corpus number would be meaningless."); sys.exit(1)

print("\nCORPUS MEASUREMENT (%d transcripts):" % len(glob.glob(sys.argv[1])))
tot = tokened = nonconf = 0
for f in glob.glob(sys.argv[1]):
    try: lines = open(f, errors='replace').read().splitlines()
    except OSError: continue
    for l in lines:
        if '"You:' not in l and 'You:' not in l: continue
        try: d = json.loads(l)
        except Exception: continue
        if d.get('type') != 'assistant': continue
        c = (d.get('message') or {}).get('content')
        if not isinstance(c, list): continue
        txt = "\n".join(b.get('text','') for b in c if isinstance(b,dict) and b.get('type')=='text')
        if not txt: continue
        tot += 1
        if TAIL.search(txt):
            tokened += 1
            if fires(txt): nonconf += 1
print("  assistant texts containing 'You:' : %d" % tot)
print("  carrying [decision]/[approval]    : %d" % tokened)
print("  NON-CONFORMING (no (Recommended)) : %d" % nonconf)
if tokened: print("  strict non-conformance rate      : %.1f%%" % (100.0*nonconf/tokened))
