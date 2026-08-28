#!/usr/bin/env python3
"""empty-corpus-guard-check — find counting scripts whose INPUT SET is never proven non-empty.

Shape (2026-08-28): a script globs a file set, derives a count, and prints it. When the glob
matches nothing the output is 0 -- shape-identical to a clean pass. Guards get written for the
MATCHER (is the predicate right?) and not for the CORPUS (was anything read at all?).

Regex cannot answer this. A sweep using `if not <name>:` as the guard signal excluded 49 of 54
candidate files; hand-inspection showed those matches were guarding a function argument, a regex
match object, or a findings list -- never the globbed set. That sweep had the very defect it was
sweeping for. This walks the AST instead and asks one precise question:

    is the NAME BOUND TO THE GLOB ever tested for emptiness before it is counted?

Usage: empty-corpus-guard-check.py <dir> [<dir> ...]
Exit 0 = report printed.  Exit 1 = predicate unproven.  Exit 2 = nothing scanned.
"""
import ast
import sys
import pathlib

GLOBBERS = {"glob", "iglob", "rglob", "listdir", "iterdir", "scandir", "walk"}


def _calls_globber(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in GLOBBERS:
                return True
            if isinstance(f, ast.Name) and f.id in GLOBBERS:
                return True
    return False


def analyse(src):
    """Return (glob_bound_names, emptiness_tested_names, counted_names)."""
    tree = ast.parse(src)
    glob_names, tested, counted = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            val = n.value
            if val is not None and _calls_globber(val):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        glob_names.add(t.id)
        # Any appearance in a boolean position counts as an emptiness test: `if not X`,
        # `if X`, `if len(X) == 0`, `while X`, `assert X`, `X or default`.
        if isinstance(n, (ast.If, ast.While, ast.Assert, ast.IfExp)):
            for sub in ast.walk(n.test):
                if isinstance(sub, ast.Name):
                    tested.add(sub.id)
        if isinstance(n, ast.BoolOp):
            for sub in ast.walk(n):
                if isinstance(sub, ast.Name):
                    tested.add(sub.id)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("len", "sum"):
            for a in n.args:
                if isinstance(a, ast.Name):
                    counted.add(a.id)
    return glob_names, tested, counted


def fires(src):
    g, t, c = analyse(src)
    return bool((g & c) - t)


CONTROLS = [
    ("POS unguarded-count", "import glob\nfs = glob.glob(p)\nprint(len(fs))", True),
    ("POS pathlib-rglob", "import pathlib\nfs = list(pathlib.Path('.').rglob('*.py'))\nprint(len(fs))", True),
    ("NEG guarded-by-not", "import glob\nfs = glob.glob(p)\nif not fs: raise SystemExit(2)\nprint(len(fs))", False),
    ("NEG guarded-by-len", "import glob\nfs = glob.glob(p)\nif len(fs) == 0: raise SystemExit(2)\nprint(len(fs))", False),
    ("NEG guarded-truthy", "import glob\nfs = glob.glob(p)\nif fs:\n    print(len(fs))", False),
    ("NEG asserted", "import glob\nfs = glob.glob(p)\nassert fs\nprint(len(fs))", False),
    ("NEG not-counted", "import glob\nfs = glob.glob(p)\nfor f in fs: print(f)", False),
    ("NEG no-glob", "fs = [1, 2]\nprint(len(fs))", False),
]


def main():
    ok = 0
    print("CONTROLS (both polarities):")
    for name, src, exp in CONTROLS:
        got = fires(src)
        good = got == exp
        ok += good
        print("  %-22s expect=%-5s got=%-5s %s" % (name, exp, got, "PASS" if good else "*** FAIL ***"))
    print("  %d/%d passed" % (ok, len(CONTROLS)))
    if ok != len(CONTROLS):
        print("  ABORT: predicate unproven; a scan number would be meaningless.")
        return 1

    if len(sys.argv) < 2:
        print("usage: empty-corpus-guard-check.py <dir> [<dir> ...]")
        return 2

    roots = [pathlib.Path(a).expanduser() for a in sys.argv[1:]]
    scanned, unparsed, flagged = 0, 0, []
    for r in roots:
        for p in sorted(r.rglob("*.py")):
            s = str(p)
            if "site-packages" in s or "/.venv" in s or "/tests/" in s or p.name.startswith("test_"):
                continue
            try:
                src = p.read_text(errors="replace")
            except OSError:
                continue
            try:
                g, t, c = analyse(src)
            except SyntaxError:
                unparsed += 1
                continue
            scanned += 1
            bad = (g & c) - t
            if bad:
                flagged.append((p, sorted(bad)))

    # This tool's own empty-corpus guard -- the defect it exists to find.
    if scanned == 0:
        print("\n  ABORT: 0 files scanned. A zero here would read as 'no defects found' "
              "when it means 'nothing was examined'.")
        return 2

    print("\nSCAN: %d files parsed (%d unparseable)" % (scanned, unparsed))
    print("FLAGGED (glob-bound name is counted but never emptiness-tested): %d" % len(flagged))
    for p, names in flagged:
        print("  %s  <- %s" % (str(p).replace(str(pathlib.Path.home()), "~"), ", ".join(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
