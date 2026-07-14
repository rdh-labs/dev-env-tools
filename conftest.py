"""Pytest collection config for the tools repo.

`qc_label_audit/test_qc_label_audit.py` is a self-contained checker with no
pytest dependency (see its module docstring): it runs its assertions at import
time and ends in `sys.exit()`. Its name matches pytest's `test_*.py` discovery
pattern, so collecting it raises SystemExit during import and aborts the whole
run with INTERNALERROR (exit 3) — not a test failure, a collection crash.

Ignoring it here rather than via a CI argument is deliberate: the exclusion is
committed, greppable, and carries its reason. CI still RUNS the file as a
script (see .github/workflows/ci.yml) — it is excluded from pytest, never from
the build.
"""

collect_ignore = ["qc_label_audit/test_qc_label_audit.py"]
