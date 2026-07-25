"""Characterization tests for the CLI, recorded at commit 9daa9bf.

These tests freeze *currently observed* `main.py` behavior so the incremental
extraction into `cli/` cannot change it by accident. They are a safety net for a
refactor, **not** a statement that every behavior recorded here is desirable
permanent design — see `docs/refactor/CLI_BASELINE.md`, which labels each
observation as a public contract, an implementation detail frozen only for the
extraction, or a known divergence.

Rules for this package:

- every `main()` / CLI execution runs against a `tmp_path` workspace;
- no test constructs a network-backed provider, makes a provider request, or
  depends on the operator's live `.env` (`main.load_dotenv` and
  `main.build_agent` are monkeypatched wherever their real work is not itself
  the behavior under characterization);
- fakes and monkeypatches are preferred over real construction, so a failure
  points at the CLI wiring rather than at an agent subsystem.
"""
