#!/usr/bin/env python3
"""Tests for the Gem-ingest wedge.

These exercise the REAL mechanisms — the real `engram` binary against an isolated
ENGRAM_DATA_DIR, and the real CredentialScanner — never a mock of the path under test
(a mock of the verification path would self-cancel the test). Engram isolation is achieved
by pointing ENGRAM_DATA_DIR at a tmp dir; the subprocess inherits it.

Run:  cd ~/dev/infrastructure/tools/gem-ingest && python3 -m pytest test_gem_ingest.py -v
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import confidentiality as conf
import gem_ingest as gi

# Split so secret-scanners don't flag this test file; reassembled at runtime to exercise the gate.
FAKE_AWS_KEY = "AKIA" + "1234567890ABCDEF"
CRED_SAMPLE = f"leaked config {FAKE_AWS_KEY} here"
ENGRAM = shutil.which("engram") or str(Path.home() / "bin/engram")
HAS_ENGRAM = Path(ENGRAM).exists()
HAS_PANDOC = shutil.which("pandoc") is not None


def _args(**kw):
    base = dict(audit=False, dry_run=False, backlog=False, run=True,
                rebuild_index=False, status=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolate every path the ingester touches + the engram data dir."""
    src = tmp_path / "src"; src.mkdir()
    state = tmp_path / "state"; state.mkdir()
    edata = tmp_path / "engram"; edata.mkdir()
    denylist = tmp_path / "denylist.txt"; denylist.write_text("# empty\n")
    index = tmp_path / "index.md"

    monkeypatch.setattr(gi, "SRC_DIR", src)
    monkeypatch.setattr(gi, "STATE_DIR", state)
    monkeypatch.setattr(gi, "LEDGER_PATH", state / "state.json")
    monkeypatch.setattr(gi, "QUARANTINE_LOG", state / "quarantine.jsonl")
    monkeypatch.setattr(gi, "LAST_RUN_PATH", state / "last-run.json")
    monkeypatch.setattr(gi, "INDEX_PATH", index)
    monkeypatch.setenv("ENGRAM_DATA_DIR", str(edata))
    monkeypatch.setenv("GEM_INGEST_DENYLIST", str(denylist))
    return {"src": src, "state": state, "edata": edata, "denylist": denylist, "index": index}


# ----------------------------------------------------------------- unit: helpers

def test_strip_html():
    assert conf.strip_html("<h3>Hi</h3><p>there &amp; more</p>") == "Hi there & more"


def test_load_denylist(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("# comment\nAcme Corp\nre:\\bWidgetCo\\b\n\n")
    dl = conf.load_denylist(f)
    kinds = {k for k, _ in dl}
    assert "literal" in kinds and "regex" in kinds and len(dl) == 2


def test_eligible_excludes_zone_identifier(sandbox):
    src = sandbox["src"]
    (src / "good.md").write_text("ok")
    (src / "skip.docx:Zone.Identifier").write_text("x")
    (src / ".hidden.md").write_text("x")
    (src / "note.txt").write_text("x")
    names = {p.name for p in gi.eligible_files(src)}
    assert names == {"good.md"}


def test_convert_json(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({
        "title": "T", "exportedAt": "2026-05-28T00:00:00Z",
        "conversation": [{"question": "What is X?", "answer": "<p>X is <b>Y</b></p>"}],
    }))
    out = gi.convert(f)
    assert "What is X?" in out and "X is Y" in out and "T" in out


# ----------------------------------------------------------------- unit: confidentiality gate

def test_assess_clean():
    a = conf.assess("a normal tech eval about SQL Server and Claude Code MCP", denylist=[])
    assert a.verdict == conf.CLEAR and a.scanner_ok


def test_assess_credential_quarantine():
    a = conf.assess(CRED_SAMPLE, denylist=[])
    assert a.verdict == conf.QUARANTINE
    assert any("aws" in r.lower() or "credential" in r.lower() for r in a.hard_reasons)


def test_assess_denylist_quarantine():
    terms = [("literal", "acme corp")]
    a = conf.assess("This eval mentions Acme Corp internal incident.", denylist=terms)
    assert a.verdict == conf.QUARANTINE
    assert any("denylist" in r for r in a.hard_reasons)


def test_assess_ssn_soft_quarantine():
    a = conf.assess("contact record 123-45-6789 in the doc", denylist=[])
    assert a.verdict == conf.QUARANTINE


def test_assess_fail_closed(monkeypatch):
    def boom():
        raise ImportError("scanner gone")
    monkeypatch.setattr(conf, "_load_credential_scanner", boom)
    a = conf.assess("totally clean text", denylist=[])
    assert a.verdict == conf.QUARANTINE and a.scanner_ok is False


def test_assess_own_email_not_flagged():
    a = conf.assess("reach me at rhart@proactive-resolutions.com about the eval", denylist=[])
    assert a.verdict == conf.CLEAR


# ----------------------------------------------------------------- E2E (real engram)

@pytest.mark.skipif(not HAS_ENGRAM, reason="engram binary not found")
def test_e2e_ingest_md(sandbox):
    (sandbox["src"] / "eval_one.md").write_text(
        "# Eval of Tool Foo\n\nVerdict: ADOPT. Foo is a fast CLI for bar with unique-tok-aa11.\n"
    )
    summary = gi.process(_args())
    assert summary["ingested"] == 1 and summary["quarantined"] == 0
    # independent read-back through the real binary
    out = subprocess.run([ENGRAM, "search", "unique-tok-aa11"],
                         capture_output=True, text=True, env=os.environ).stdout
    assert "Found" in out and "unique-tok-aa11" in out
    ledger = json.loads((sandbox["state"] / "state.json").read_text())
    entry = next(iter(ledger["entries"].values()))
    assert entry["verdict"] == gi.INGESTED and entry["verified"] is True
    assert "eval_one.md" in sandbox["index"].read_text()


@pytest.mark.skipif(not HAS_ENGRAM, reason="engram binary not found")
def test_idempotent_replay(sandbox):
    (sandbox["src"] / "eval_two.md").write_text("# Eval Two\n\nADOPT widget-zz22.\n")
    first = gi.process(_args())
    assert first["ingested"] == 1
    second = gi.process(_args())
    assert second["ingested"] == 0 and second["skipped"] >= 1
    # exactly one matching observation exists (no duplicate write on replay)
    out = subprocess.run([ENGRAM, "search", "widget-zz22"],
                         capture_output=True, text=True, env=os.environ).stdout
    assert out.count("widget-zz22") == 1


@pytest.mark.skipif(not HAS_ENGRAM, reason="engram binary not found")
def test_quarantine_blocks_ingest(sandbox):
    (sandbox["src"] / "bad.md").write_text(
        f"# Eval With Secret\n\nleak: {FAKE_AWS_KEY} and token quarantined-tok-bb33\n"
    )
    summary = gi.process(_args())
    assert summary["quarantined"] == 1 and summary["ingested"] == 0
    # the secret-bearing content must NOT be retrievable from the store.
    # (engram echoes the query in its "No memories found for: ..." miss message, so assert on
    #  the hit header "Found N memories:" being absent rather than naive substring absence.)
    out = subprocess.run([ENGRAM, "search", "quarantined-tok-bb33"],
                         capture_output=True, text=True, env=os.environ).stdout
    assert "Found " not in out, f"expected no hit, got: {out!r}"
    qlog = (sandbox["state"] / "quarantine.jsonl").read_text()
    assert "bad.md" in qlog


@pytest.mark.skipif(not (HAS_ENGRAM and HAS_PANDOC), reason="engram or pandoc missing")
def test_e2e_ingest_docx(sandbox, tmp_path):
    md = tmp_path / "seed.md"
    md.write_text("# Docx Eval\n\nVerdict ADOPT. token docx-tok-cc44 present.\n")
    docx = sandbox["src"] / "real_eval.docx"
    subprocess.run(["pandoc", str(md), "-o", str(docx)], check=True)
    summary = gi.process(_args())
    assert summary["ingested"] == 1
    out = subprocess.run([ENGRAM, "search", "docx-tok-cc44"],
                         capture_output=True, text=True, env=os.environ).stdout
    assert "docx-tok-cc44" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
