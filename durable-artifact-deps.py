#!/usr/bin/env python3
"""Find DURABLE DOCUMENTS whose function depends on a third party staying up.

THE SHAPE (2026-08-27, SHV audit): an artifact meant to persist — a client deliverable, a
report, an archived analysis — loads its CSS/JS from a CDN at read time. It does not fail
loudly. The day that CDN blocks, changes, or shuts down, the document silently degrades and
the owner learns about it from a confused reader. Found on a live client deliverable whose own
console warns "cdn.tailwindcss.com should not be used in production".

THREE DISTINCTIONS THIS ENCODES, each of which a naive grep gets WRONG. They were derived by
hand-checking a 358-file sweep, where the naive count was 37 and the true population was 3.

1. FUNCTIONAL vs COSMETIC. A Google-Fonts link degrades to a fallback font: the document still
   reads. A Tailwind/Chart.js/d3 link degrades to an unstyled or non-functional page. Only the
   second is a finding. Naive scan: 82 cosmetic files counted as hits.

2. DOCUMENT vs APPLICATION. An app loading a library is normal architecture — it has a build,
   a deploy and a maintainer. A DOCUMENT loading a library is fragile, because nobody is
   maintaining a PDF-equivalent. Heuristic below; it is a heuristic and says so.

3. AUTHORED vs ARCHIVED. A saved copy of legislation.gov.au depending on legislation.gov.au is
   inherent to being a saved page, not a defect we introduced. Evidence folders are excluded.
   Naive scan: 4 archived source copies counted as hits.

WHY A SCANNER AND NOT A GATE. This workspace has 110 of 228 hook modules wired nowhere (measured
2026-08-27, DEC-347). Adding an unwired gate would BE the defect this session catalogued. This
is a script you run and that prints a list; it makes no claim to fire on its own.

Exit: 0 clean · 1 findings present · 2 could not scan (never silently 0).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Functional: page breaks or unstyles without it.
FUNCTIONAL = re.compile(
    r"""<(?:script|link)[^>]+(?:src|href)=["']https?://("""
    r"""cdn\.tailwindcss\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com"""
    r"""|d3js\.org|ajax\.googleapis\.com|code\.jquery\.com|stackpath\.bootstrapcdn\.com"""
    r""")""",
    re.I,
)
# Cosmetic: degrades to a fallback, document still reads. NOT a finding.
COSMETIC = re.compile(r"https?://fonts\.(?:googleapis|gstatic)\.com", re.I)

SKIP_DIRS = ("node_modules", "/.git", "/venv", ".venv", "site-packages",
             "/dist/", "/build/", "__pycache__", "/.next/")
# Distinction 3: archived copies of other people's pages.
ARCHIVED = ("primary-sources", "source-materials", "/raw/", "/downloads/",
            "/fixtures/", "/samples/", "/archive/", "/archives/")
# Distinction 2: an application entry point, not a document.
APPISH = ("/public/", "/src/", "/app/", "index-cloud.html", "test-", ".dev.html")


def is_archived(path: str) -> bool:
    return any(s in path.lower() for s in ARCHIVED)


def is_application(path: str) -> bool:
    """HEURISTIC, deliberately conservative — it can only EXCLUDE, and a wrong exclusion
    loses a finding rather than inventing one. An app has a build and a maintainer; a
    document does not. Bare `index.html` inside a project tree is the ambiguous case and is
    treated as an app, because that is where false positives were observed."""
    p = path.lower()
    return any(s in p for s in APPISH) or os.path.basename(p) == "index.html"


def scan(root: str) -> tuple[list, int, int, int]:
    findings, n, cosmetic, archived = [], 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        if any(s in dirpath for s in SKIP_DIRS):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith((".html", ".htm")):
                continue
            path = os.path.join(dirpath, fn)
            n += 1
            try:
                text = open(path, errors="replace").read()
            except OSError:
                continue
            hosts = sorted(set(FUNCTIONAL.findall(text)))
            if not hosts:
                if COSMETIC.search(text):
                    cosmetic += 1
                continue
            if is_archived(path):
                archived += 1
                continue
            if is_application(path):
                continue
            findings.append((path, hosts))
    return findings, n, cosmetic, archived


def self_check() -> int:
    """BOTH POLARITIES. A positive-only control cannot fail."""
    ok = []
    ok.append(("FIRES on a functional CDN dep",
               bool(FUNCTIONAL.search('<script src="https://cdn.tailwindcss.com"></script>'))))
    ok.append(("FIRES on jsdelivr",
               bool(FUNCTIONAL.search('<script src="https://cdn.jsdelivr.net/npm/chart.js">'))))
    ok.append(("SILENT on Google Fonts (cosmetic, degrades to fallback)",
               not FUNCTIONAL.search('<link href="https://fonts.googleapis.com/css2?family=X">')))
    ok.append(("SILENT on a same-origin local asset",
               not FUNCTIONAL.search('<script src="/assets/app.js"></script>')))
    ok.append(("SILENT on a bare URL in prose (not a script/link tag)",
               not FUNCTIONAL.search("<p>see https://cdn.jsdelivr.net for details</p>")))
    ok.append(("archived path excluded", is_archived("/x/primary-sources/a.html")))
    ok.append(("normal deliverable NOT excluded as archived",
               not is_archived("/x/deliverables/dashboards/a.html")))
    ok.append(("app path excluded", is_application("/x/public/admin/index.html")))
    ok.append(("named document NOT excluded as app",
               not is_application("/x/deliverables/SHV_Dashboard_v3.html")))
    bad = [m for m, good in ok if not good]
    for m in bad:
        print(f"  [FAIL] {m}")
    if not bad:
        print(f"  [PASS] {len(ok)}/{len(ok)} controls — both polarities on every rule")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.expanduser("~/dev"))
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    if args.self_check:
        print("DURABLE ARTIFACT DEPS: self-check")
        return self_check()

    if not os.path.isdir(args.root):
        print(f"ADVERSE: {args.root} is not a directory — cannot scan, which is not a clean result")
        return 2

    findings, n, cosmetic, archived = scan(args.root)
    print(f"scanned {n} HTML file(s) under {args.root}")
    print(f"  excluded: {cosmetic} cosmetic-only (fonts) · {archived} archived source copies")
    if not findings:
        print("  no durable documents with a functional third-party dependency")
        return 0
    print(f"\n{len(findings)} durable document(s) whose function depends on a third party:")
    for path, hosts in findings:
        print(f"  {path.replace(os.path.expanduser('~'), '~')}")
        print(f"      -> {', '.join(hosts)}")
    print("\nEach will silently degrade if that host blocks, changes or shuts down.")
    print("Fix = inline the dependency so the document is self-contained.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
