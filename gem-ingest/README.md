# gem-ingest — Gemini-Gems eval → Engram ingestion wedge

Turns Richard's existing *"Export to Docs / download into `~/dev/share/Gemini-reviews/`"* step into an
automatic, content-scanned, searchable KB entry — with **zero new manual steps**. Phase A→B of the
approved process design (`~/.claude/plans/sorted-prancing-blum.md`, v2.1).

## What it does (per file)
1. **Eligibility** — `.docx/.md/.json` only; skips `*:Zone.Identifier` browser sidecars and dotfiles.
2. **Confidentiality gate (Phase A)** — `confidentiality.py`: credential scan (reused from the security
   hook) + client-name denylist + soft PII/legal signals. Any hard hit → **QUARANTINE** (never ingested).
   **Fails closed** if the scanner can't load.
3. **Convert** — `read-docx`/pandoc for `.docx`; HTML-strip for Gemini `.json`; read `.md`.
4. **Capture** — `engram save … --project gemini-evals` (the ONE canonical store, DEC-303), then an
   **independent read-back** (`engram search`) confirms it landed (§12 verify-then-report).
5. **Index** — regenerates `~/dev/share/gem-evals-index.md`, a greppable mirror (not a second home).

**Idempotent:** keyed on `sha256(content)`. Already-ingested/quarantined hashes are skipped; failed saves
become `RETRY` and retry next run. Safe to replay / double-fire.

## Usage
```
python3 gem_ingest.py --audit      # assess + per-file table, writes NOTHING (the FP/FN audit)
python3 gem_ingest.py --backlog    # real ingest, STRICT confidentiality (cautious first pass)
python3 gem_ingest.py --run        # real ingest, normal gate (what the timer runs)
python3 gem_ingest.py --rebuild-index
python3 gem_ingest.py --status
```

## Trigger (single, per plan)
A **systemd `--user` timer** (`~/.config/systemd/user/gem-ingest.{service,timer}`), every 30 min +
`OnBootSec`, with **`Persistent=true`** so runs missed while the laptop was off are caught up on next
boot — the property plain cron lacks (see `~/.claude` memory on laptop offage).

Enable (requires the user — it is a persistence action):
```
systemctl --user daemon-reload && systemctl --user enable --now gem-ingest.timer
```
Status / logs: `systemctl --user list-timers gem-ingest.timer` · `journalctl --user -u gem-ingest.service`

## Confidentiality denylist
Live file: `~/.config/gem-ingest/denylist.txt` (chmod 600, **outside git**). Format template:
`client-denylist.example.txt`. One term/line; `re:` prefix for regex. Populate with client names,
incident IDs, matter codes — anything that would make an eval confidential.

## Config (env overrides)
`GEM_INGEST_SRC` · `GEM_INGEST_STATE_DIR` · `GEM_INGEST_INDEX` · `GEM_INGEST_DENYLIST` ·
`GEM_INGEST_PROJECT` · `ENGRAM_DATA_DIR` (used by the test suite to isolate a throwaway DB).

## Tests
```
python3 -m pytest test_gem_ingest.py -v
```
Exercises the **real** `engram` binary (isolated `ENGRAM_DATA_DIR`) and the **real** credential scanner —
no mock of the path under test. Covers the gate, idempotent replay, quarantine-blocks-write, and a full
`.docx`→Engram→retrieval E2E.

## State / observability
- `~/.local/share/gem-ingest/state.json` — idempotency ledger (per-file verdict, obs id, verified).
- `~/.local/share/gem-ingest/quarantine.jsonl` — quarantined items + reasons.
- `~/.local/share/gem-ingest/last-run.json` — last-run summary (`--status`).
- journald (`gem-ingest.service`) — per-run logs; non-zero exit on hard failure surfaces there.
