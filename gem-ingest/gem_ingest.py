#!/usr/bin/env python3
"""gem_ingest.py — Idempotent Gemini-Gems eval -> Engram ingestion wedge.

Phase B of the approved process design (plan v2.1, ~/.claude/plans/sorted-prancing-blum.md).

Turns Richard's *existing* "Export to Docs / download into ~/dev/share/Gemini-reviews/" step
into an automatic, content-scanned, searchable KB entry — with ZERO new manual steps.

Pipeline per file:
    eligibility filter (skip *:Zone.Identifier sidecars, dotfiles, unknown ext)
    -> content-scan (Phase A confidentiality gate; QUARANTINE on any hit, never ingested)
    -> convert (read-docx / pandoc for .docx; HTML-strip for .json; read for .md)
    -> engram save (canonical store, project=gemini-evals) + read-back verify (§12)
    -> append to the markdown index (greppable mirror; regenerated from the ledger)

IDEMPOTENT (per QC — the trigger can double-fire): each item is keyed by sha256(content).
Already-INGESTED / already-QUARANTINED hashes are skipped; failed saves are marked RETRY and
retried next run. Safe to replay.

CANONICAL HOME (DEC-303): Engram is the one canonical write target. The markdown index is a
greppable mirror, NOT a second home. NotebookLM (if used later) is a non-canonical read lens.

Modes:
    (default / --run)   real ingest of new/eligible files
    --audit / --dry-run assess + extract metadata, print a per-file table, write NOTHING
    --backlog           real ingest with strict-mode confidentiality (conservative first pass)
    --rebuild-index     regenerate the markdown index from the ledger only
    --status            print last-run summary
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import confidentiality as conf  # noqa: E402

HOME = Path.home()
SRC_DIR = Path(os.environ.get("GEM_INGEST_SRC", HOME / "dev/share/Gemini-reviews"))
STATE_DIR = Path(os.environ.get("GEM_INGEST_STATE_DIR", HOME / ".local/share/gem-ingest"))
LEDGER_PATH = STATE_DIR / "state.json"
QUARANTINE_LOG = STATE_DIR / "quarantine.jsonl"
LAST_RUN_PATH = STATE_DIR / "last-run.json"
INDEX_PATH = Path(os.environ.get("GEM_INGEST_INDEX", HOME / "dev/share/gem-evals-index.md"))
PROJECT = os.environ.get("GEM_INGEST_PROJECT", "gemini-evals")
ENGRAM_TYPE = "discovery"

READ_DOCX = shutil.which("read-docx") or str(HOME / "bin/read-docx")
ENGRAM = shutil.which("engram") or str(HOME / "bin/engram")

ELIGIBLE_EXT = {".docx", ".md", ".json"}
SUMMARY_CHARS = 1500          # body excerpt stored in Engram (searchable, not full doc)
VERDICT_CHARS = 600           # leading summary/verdict excerpt

INGESTED = "INGESTED"
QUARANTINED = "QUARANTINED"
RETRY = "RETRY"


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ----------------------------------------------------------------------------- conversion

def _convert_json(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return path.read_text(encoding="utf-8", errors="replace")
    parts: List[str] = []
    if isinstance(data, dict):
        if data.get("title"):
            parts.append(f"# {data['title']}")
        if data.get("exportedAt"):
            parts.append(f"_exported: {data['exportedAt']}_")
        conv = data.get("conversation")
        if isinstance(conv, list):
            for turn in conv:
                if not isinstance(turn, dict):
                    continue
                q = conf.strip_html(str(turn.get("question", "")))
                ans = conf.strip_html(str(turn.get("answer", "")))
                if q:
                    parts.append(f"\n**Q:** {q}")
                if ans:
                    parts.append(f"\n**A:** {ans}")
        else:
            parts.append(json.dumps(data, indent=1)[:8000])
    else:
        parts.append(json.dumps(data, indent=1)[:8000])
    return "\n".join(parts)


def convert(path: Path) -> str:
    """Convert any supported file to plain text/markdown."""
    ext = path.suffix.lower()
    if ext == ".md":
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".json":
        return _convert_json(path)
    if ext == ".docx":
        for cmd in ([READ_DOCX, str(path)],
                    ["pandoc", str(path), "--to=markdown", "--wrap=none"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout
            except (OSError, subprocess.SubprocessError):
                continue
        raise RuntimeError(f"docx conversion failed: {path.name}")
    return path.read_text(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------------- metadata

def _first_heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"#{1,3}\s+(.*)", line)
        if m and m.group(1).strip():
            return m.group(1).strip()[:120]
        m = re.match(r"\*\*(.+?)\*\*\s*$", line)
        if m and len(m.group(1)) > 8:
            return m.group(1).strip()[:120]
    return None


def _first_paragraph(text: str, limit: int) -> str:
    chunk: List[str] = []
    for line in text.splitlines():
        s = line.strip().lstrip("#*_> ").strip()
        if not s:
            if chunk:
                break
            continue
        if s.startswith(("|", "---", "===")):
            continue
        chunk.append(s)
        if sum(len(c) for c in chunk) > limit:
            break
    return " ".join(chunk)[:limit]


def extract_meta(text: str, path: Path) -> Dict[str, str]:
    stem = path.stem.replace("_", " ").strip()
    topic = _first_heading(text) or stem
    date = _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    # JSON exports carry an authoritative exportedAt
    if path.suffix.lower() == ".json":
        m = re.search(r"_exported:\s*([0-9T:\-]+)", text)
        if m:
            date = m.group(1)[:10]
    summary = _first_paragraph(text, VERDICT_CHARS)
    return {"topic": topic, "date": date, "summary": summary, "source": path.name}


# ----------------------------------------------------------------------------- engram

def engram_save(title: str, content: str) -> Tuple[Optional[int], Optional[str]]:
    try:
        r = subprocess.run(
            [ENGRAM, "save", title, content, "--type", ENGRAM_TYPE, "--project", PROJECT],
            capture_output=True, text=True, timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if r.returncode != 0:
        return None, (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")
    m = re.search(r"#(\d+)", r.stdout)
    return (int(m.group(1)) if m else -1), None


def engram_search(query: str) -> str:
    try:
        r = subprocess.run([ENGRAM, "search", query], capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def build_observation(meta: Dict[str, str], text: str, marker: str) -> Tuple[str, str]:
    title = f"Gem eval: {meta['topic']}"[:120]
    body = (
        f"{marker}\n"
        f"Source: {meta['source']}\n"
        f"Date: {meta['date']}\n"
        f"Topic: {meta['topic']}\n"
        f"Summary: {meta['summary']}\n"
        f"---\n"
        f"{text[:SUMMARY_CHARS]}\n"
        f"[full document: {SRC_DIR / meta['source']}]"
    )
    return title, body


# ----------------------------------------------------------------------------- ledger / index

def load_ledger() -> Dict:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "entries": {}}


def save_ledger(ledger: Dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=1))
    os.replace(tmp, LEDGER_PATH)


def regenerate_index(ledger: Dict) -> None:
    entries = list(ledger.get("entries", {}).values())
    ingested = sorted([e for e in entries if e.get("verdict") == INGESTED],
                      key=lambda e: e.get("date", ""), reverse=True)
    quarantined = [e for e in entries if e.get("verdict") == QUARANTINED]
    lines = [
        "# Gemini-Gems Evaluations — KB Index",
        "",
        "_Auto-generated by gem-ingest (do not edit by hand). Canonical store: **Engram** "
        f"(`engram search \"<topic>\" --project {PROJECT}`). This file is a greppable mirror, "
        "not a second home (DEC-303)._",
        "",
        f"_Last updated: {_now()} · {len(ingested)} ingested · {len(quarantined)} quarantined_",
        "",
        "| Date | Topic | Source | Engram | Verified |",
        "|------|-------|--------|--------|----------|",
    ]
    def _cell(s: str) -> str:  # neutralize table/backtick injection from filenames or topics
        return (s or "").replace("|", "\\|").replace("`", "'").replace("\n", " ")
    for e in ingested:
        oid = f"#{e['obs_id']}" if e.get("obs_id") and e["obs_id"] > 0 else "-"
        ver = "yes" if e.get("verified") else "PENDING"
        topic = _cell(e.get("topic"))[:70]
        lines.append(f"| {e.get('date','')} | {topic} | `{_cell(e.get('source'))}` | {oid} | {ver} |")
    if quarantined:
        lines += ["", "## Quarantined (NOT ingested — manual review required)", ""]
        for e in quarantined:
            reasons = _cell("; ".join(e.get("reasons", [])))[:120]
            lines.append(f"- `{_cell(e.get('source'))}` — {reasons}")
    lines.append("")
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text("\n".join(lines))


# ----------------------------------------------------------------------------- core

def eligible_files(src: Path) -> List[Path]:
    out: List[Path] = []
    for p in sorted(src.iterdir()):
        if p.is_symlink():        # don't follow a symlink out of the watched folder
            continue
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if ":Zone.Identifier" in p.name:   # browser-download provenance sidecar — never content
            continue
        if p.suffix.lower() not in ELIGIBLE_EXT:
            continue
        out.append(p)
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def process(args) -> Dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_DIR, 0o700)  # quarantine log may hold partial client-name hints — user-only
    except OSError:
        pass
    ledger = load_ledger()
    entries = ledger.setdefault("entries", {})
    summary = {"ingested": 0, "quarantined": 0, "skipped": 0, "failed": 0,
               "audited": 0, "errors": [], "ts": _now()}
    audit_rows: List[Dict] = []
    denylist = conf.load_denylist()
    strict = bool(args.backlog)
    dry = bool(args.audit or args.dry_run)

    files = eligible_files(SRC_DIR)
    for path in files:
        try:
            sha = sha256_of(path)
        except OSError as exc:
            summary["failed"] += 1
            summary["errors"].append(f"{path.name}: read error {exc}")
            continue

        prior = entries.get(sha)
        if prior and prior.get("verdict") in (INGESTED, QUARANTINED) and not dry:
            summary["skipped"] += 1
            continue

        try:
            text = convert(path)
        except Exception as exc:  # noqa: BLE001
            summary["failed"] += 1
            summary["errors"].append(f"{path.name}: convert failed: {exc}")
            continue

        a = conf.assess(text, source_name=path.name, denylist=denylist, strict=strict)
        meta = extract_meta(text, path)
        row = {"source": path.name, "verdict": a.verdict, "topic": meta["topic"],
               "date": meta["date"], "reasons": a.all_reasons, "scanner_ok": a.scanner_ok}
        audit_rows.append(row)
        summary["audited"] += 1

        if dry:
            continue

        if a.verdict == conf.QUARANTINE:
            entries[sha] = {"source": path.name, "verdict": QUARANTINED, "topic": meta["topic"],
                            "date": meta["date"], "reasons": a.all_reasons, "ts": _now()}
            with open(QUARANTINE_LOG, "a") as fh:
                fh.write(json.dumps({"ts": _now(), "source": path.name,
                                     "reasons": a.all_reasons, "scanner_ok": a.scanner_ok}) + "\n")
            try:
                os.chmod(QUARANTINE_LOG, 0o600)
            except OSError:
                pass
            summary["quarantined"] += 1
            save_ledger(ledger)
            continue

        marker = f"gemref{sha[:8]}"
        title, body = build_observation(meta, text, marker)
        obs_id, err = engram_save(title, body)
        if obs_id is None:
            entries[sha] = {"source": path.name, "verdict": RETRY, "topic": meta["topic"],
                            "date": meta["date"], "reasons": [f"engram save failed: {err}"], "ts": _now()}
            summary["failed"] += 1
            summary["errors"].append(f"{path.name}: engram save failed: {err}")
            save_ledger(ledger)
            continue

        # §12 verify-then-report: independent read-back of the resulting state.
        verified = marker in engram_search(marker)
        entries[sha] = {"source": path.name, "verdict": INGESTED, "obs_id": obs_id,
                        "verified": verified, "topic": meta["topic"], "date": meta["date"],
                        "marker": marker, "ts": _now()}
        summary["ingested"] += 1
        if not verified:
            summary["errors"].append(f"{path.name}: saved obs #{obs_id} but read-back did not confirm")
        save_ledger(ledger)

    if not dry:
        save_ledger(ledger)
        regenerate_index(ledger)
        LAST_RUN_PATH.write_text(json.dumps(summary, indent=1))

    summary["_audit_rows"] = audit_rows
    return summary


# ----------------------------------------------------------------------------- cli

def print_audit(summary: Dict) -> None:
    rows = summary.get("_audit_rows", [])
    print(f"\n=== gem-ingest audit · {len(rows)} files · {summary['ts']} ===")
    print(f"{'VERDICT':<11} {'DATE':<11} SOURCE")
    print("-" * 72)
    for r in rows:
        flag = "QUARANTINE" if r["verdict"] == conf.QUARANTINE else "clear"
        print(f"{flag:<11} {r['date']:<11} {r['source']}")
        for reason in r["reasons"]:
            print(f"            └─ {reason}")
    nq = sum(1 for r in rows if r["verdict"] == conf.QUARANTINE)
    print("-" * 72)
    print(f"clear: {len(rows)-nq}  quarantine: {nq}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Idempotent Gemini-Gems eval -> Engram ingester")
    ap.add_argument("--audit", action="store_true", help="assess + print table, write nothing")
    ap.add_argument("--dry-run", action="store_true", help="alias for --audit")
    ap.add_argument("--backlog", action="store_true", help="real ingest, strict confidentiality (first pass)")
    ap.add_argument("--run", action="store_true", help="real ingest (default)")
    ap.add_argument("--rebuild-index", action="store_true", help="regenerate the index from the ledger")
    ap.add_argument("--status", action="store_true", help="print last-run summary")
    args = ap.parse_args(argv)

    if args.status:
        if LAST_RUN_PATH.exists():
            print(LAST_RUN_PATH.read_text())
        else:
            print("no run recorded yet")
        return 0

    if args.rebuild_index:
        regenerate_index(load_ledger())
        print(f"index regenerated: {INDEX_PATH}")
        return 0

    summary = process(args)

    if args.audit or args.dry_run:
        print_audit(summary)
        return 0

    print(f"gem-ingest: ingested={summary['ingested']} quarantined={summary['quarantined']} "
          f"skipped={summary['skipped']} failed={summary['failed']}")
    for e in summary["errors"]:
        print(f"  ! {e}")
    print(f"index: {INDEX_PATH}")
    # Non-zero exit on hard failures so the systemd unit surfaces them in journald.
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
