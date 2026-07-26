# Contributing

This is a proprietary project (see [`LICENSE`](LICENSE)); external distribution
and reuse require the copyright holder's permission. This guide describes the
development workflow and the checks every change must pass.

Behavioural rules for in-repo agents live in [`docs/AGENTS.md`](docs/AGENTS.md)
and [`docs/AGENT_DOCTRINE.md`](docs/AGENT_DOCTRINE.md).

## Prerequisites

- **Python 3.11** (matches CI and the Docker image).

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   POSIX: source .venv/bin/activate
python -m pip install --upgrade pip
pip install --require-hashes -r requirements.lock
# Optional HTTP API extras (not in the hash-locked core set):
pip install fastapi "uvicorn[standard]"
```

Copy [`.env.example`](.env.example) to `.env`. The default `AGENT_PROVIDER=mock`
needs no keys and makes tests and local runs deterministic.

## Tests

```bash
coverage run --branch -m pytest
coverage report --fail-under=85
```

- Branch coverage must stay **≥ 85%** (CI enforces it).
- When you fix a bug, add a regression test and confirm it **fails on the old
  code** before the fix — an unproven regression test is not trusted.

## Documentation discipline

The doc set is governed, not free-form:

- One question → one owning file. Update the **existing** owner; do not create a
  new doc per pass. The routing map is [`docs/INDEX.md`](docs/INDEX.md).
- Register any new document as a row in `docs/INDEX.md`.
- These read-only guards must stay green (they run via `pytest` in CI):
  `scripts/docs_link_check.py` (links resolve), `scripts/commands_map_check.py`
  (every `main.py` command is in `COMMANDS_MAP.md`),
  `scripts/agent_anatomy_check.py` (the `core/` index),
  `scripts/docs_code_conformance.py` (code references in prose resolve),
  `scripts/architecture_invariants.py` (the architecture's load-bearing claims:
  core imports downward only, no orphaned deciders, documented env flags exist,
  every verifier verdict is bucketed), and `scripts/registry_tally.py` (the
  issue registry).
- **Code wins.** When a doc and the code disagree, fix the doc.

## Dependency changes

Direct dependencies are declared in `requirements.in` and compiled to a
hash-locked `requirements.lock`:

```bash
pip-compile --generate-hashes requirements.in -o requirements.lock
python scripts/generate_sbom.py        # regenerate the SBOM; CI checks it is in sync
```

## Change process

1. Make the smallest safe change.
2. Add or update tests when behaviour changes.
3. Run the tests and the doc guards locally.
4. Open a PR. CI gates: gitleaks secret scan, hash-locked install, `pip check`,
   SBOM sync, release/supply-chain audit, and tests with branch coverage.

Prefer small, localized, reviewable changes. Keep `README.md` limited to
navigation; put decisions in the document that owns them.
