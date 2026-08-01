# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet publish versioned releases. Until it does, changes
> accumulate under **Unreleased**. The authoritative build state remains
> `docs/ROADMAP.md` (capabilities) and `docs/daemon-progress.md` (daemon plan);
> defect status is owned by `docs/audit/MASTER_ISSUE_REGISTRY.md`.

## [Unreleased]

### Added
- Instruction-conflict turnstile. `docs/INSTRUCTION_AUTHORITY.md` ranks
  instruction sources (operator → task contract → test → repo invariant →
  convention → advisor); `core/instruction_conflict_gate.py` blocks every
  state-mutating action on **any** contradiction and emits a six-point operator
  report; `core/directive_extractor.py` recovers requirements from task and
  review text via mutually exclusive stances on known axes;
  `core/conflict_episode.py` banks each stop as append-only procedural memory
  (`инструкция → конфликт → решение`). Wired as the first gate of the
  self-apply lane, with requirements recovered in `core/self_apply_bridge.py`.
- Public/operator documentation layer: `SECURITY.md`, `CONTRIBUTING.md`, this
  `CHANGELOG.md`, `docs/OPERATIONS.md` (operations + troubleshooting + HTTP API),
  and `docs/CONFIGURATION.md` (configuration + data & state).
- `LICENSE` — proprietary, all rights reserved.
- Documentation drift guards wired into CI: `scripts/docs_link_check.py`
  (relative Markdown links resolve) and `scripts/commands_map_check.py` (every
  command dispatched in `main.py` is documented in `docs/COMMANDS_MAP.md`).

### Fixed
- `docs/AGENT_DOCTRINE.md` priority order had a single "explicit user
  instructions" line and no notion of an advisor, so a confidently worded
  review comment outranked the specification it contradicted.
- Restored the `:self-build-supervisor` row and documented four further real
  commands (`:mem`/`:memory`, `:assumptions`/`:assumption-log`, `:quit`/`:exit`,
  the `:budget-killswitch` alias) in `docs/COMMANDS_MAP.md`.
- Broken doctrine-doc links in `docs/INDEX.md` and `README.md` (files live under
  `docs/`, not the repo root).

_Older history is available in the Git log and the pull-request record._
