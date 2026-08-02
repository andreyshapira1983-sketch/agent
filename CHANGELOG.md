# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet publish versioned releases. Until it does, changes
> accumulate under **Unreleased**. The authoritative build state remains
> `docs/ROADMAP.md` (capabilities) and `docs/daemon-progress.md` (daemon plan);
> defect status is owned by `docs/audit/MASTER_ISSUE_REGISTRY.md`.

## [Unreleased]

### Added
- The agent's memory records what went wrong and what a procedure was for:
  `EpisodeRecord.defect_signals` banks the run's own sensor faults; an unmet
  obligation on a declared-achieved run lowers the completion verdict and
  withholds procedure credit (`completion_override` keeps the divergence
  readable); procedures lead with the situation and carry capped `lessons`.
  The completion axis is settled at the save boundary (operator's D-6 ruling):
  new records can no longer impersonate pre-axis legacy rows.
- Trim notices teach the recovery move: a truncated file now tells the model
  to grep the window instead of guessing from the fragment.

### Changed
- `core/loop.py` decomposed by six bounded pieces (4733 → 4047 lines): replan
  vocabulary → `core/replan.py`; file-request classifiers, path parsers and
  the multi-file review decision → `core/file_request_intent.py` (new);
  evidence→prompt renderers → `core/loop_helpers.py`; the trimmed-memory
  rebuilder and the memory-block tags → `core/evidence_budget.py`.

### Fixed
- Two quadratic path-mention scans on attacker-shaped input (CodeQL #11/#5):
  replaced by a linear scanner with the retired regexes kept as test oracles —
  2.8–3.7 s worst cases now under 10 ms.
- Secret-scanner review debt from the closed-and-replaced PR #207: Stripe
  publishable keys no longer quarantined as secrets, webhook/key patterns
  anchored, stateless GitHub tokens redacted whole.
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

- `docs/AGENT_DOCTRINE.md` priority order had a single "explicit user
  instructions" line and no notion of an advisor, so a confidently worded
  review comment outranked the specification it contradicted.
- Restored the `:self-build-supervisor` row and documented four further real
  commands (`:mem`/`:memory`, `:assumptions`/`:assumption-log`, `:quit`/`:exit`,
  the `:budget-killswitch` alias) in `docs/COMMANDS_MAP.md`.
- Broken doctrine-doc links in `docs/INDEX.md` and `README.md` (files live under
  `docs/`, not the repo root).

_Older history is available in the Git log and the pull-request record._
