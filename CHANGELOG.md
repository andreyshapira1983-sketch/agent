# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> This project does not yet publish versioned releases. Until it does, changes
> accumulate under **Unreleased**. The authoritative build state remains
> `docs/ROADMAP.md` (capabilities) and `docs/daemon-progress.md` (daemon plan);
> defect status is owned by `docs/audit/MASTER_ISSUE_REGISTRY.md`.

## [Unreleased]

### Added
- Memory earns its keep causally (MIR-074 phase 1, operator ruling): a
  `[memory:<id>]` citation the verifier confirms increments the record's
  `causal_use` — retrieved, changed the answer, independently checked;
  bare keyword injection stays a near-zero signal. An auto-'fact' record
  with zero causal credit can now go dormant (archived, returnable) —
  the tag floor no longer grants immortality; nothing is ever destroyed.
  Every hygiene pass explains itself in the shared five-point vocabulary
  (`hygiene_explained`). Root fix underneath: `MemoryRecord` now PERSISTS
  its origin (`source`) — it was always lost on save, so the MIR-046
  independence rule demoted every memory citation to topic-only, which is
  why the measured all-history count of verified memory citations was
  zero.
- The agent asks back instead of only philosophising unsupported (MIR-075,
  operator assignment): a self-analysis turn whose answer carries zero
  verified chunks now ends with one canned narrowing question
  («Уточнение: …») — trigger built from measured post-answer numbers, never
  from question wording (the lexical route died in #263); journaled as
  `clarification_ask_back`.
- Every verified answer explains itself in five human points (MIR-069 phase 1):
  `core/verification_summary.py` composes «Проверял / Способ / Доказательство /
  Непроверенным осталось / Уверенность» from the verifier's own numbers, the
  full text is journalled as `verification_explained`, and a compact
  «Проверка: подтверждено X из N…» tail reaches the operator via a new
  rewrite-proof `append` notice channel — with `format_human_response` taught
  not to drop it (found by live run).
- Completion contract, fixed before the work (MIR-067, #258): derived from the
  REQUEST before any tool runs (`core/completion_contract.py`), journalled even
  when empty, and judged at the end as the `acceptance_criteria` obligation
  source — delivery is read only from artifacts, disclosure only from the
  answer.
- Procedure search explains its zero (#260): `search_with_report` counts every
  rejection by reason (`excluded_candidate` / `no_overlap` / `over_limit` /
  `no_query_tokens`), journalled as `procedures_rejected_by` — a present-but-
  gated candidate and a no-match are no longer indistinguishable.
- Memory lifecycle contract to v5 (#259): an honest per-section ledger of what
  is implemented, a phased plan (path B), and a test pinning the ledger to the
  document.
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
- Codacy sweep, one PR (operator's order «исправь всё досконально, одним
  PR»): the `loop_methods2` mixin declares its FULL host contract (28
  "no member" HIGHs -> 0, file rating 0.46 -> 10.00); the four self-build
  digests are marked `usedforsecurity=False`; 184 annotation/deprecated-
  import findings (PEP 585/604, quoted annotations, typing.List/Optional/
  Type) modernised; import blocks sorted across all production code (core/cli/app/
  tools/scripts); the tests/ half of the sorting was carved out to fit
  the reviewers' 300-file ceiling and follows in a companion PR; three
  dead imports dropped. Verified: the secrets/AWS/password findings are test fixtures
  of the secret scanner (zero in production code), the four `exec()` calls
  live only in the splitter's own test, and every production
  `subprocess.run` already carries explicit `shell=False`/`check=False`.
  The 158 broad-except sites are NOT touched here — a behavioural audit
  slice, not a style sweep.
- A tool-set match is not usefulness (#261, operator ruling 2026-08-02): the
  workflow-key merge folds provenance only; counters, confidence and status
  move solely through causally attributed outcome feedback.
- The targetless-change ambiguity rule is retired (#263): measured 8 of 8
  false on 62 live requests — every "targetless" request named its target in
  prose the vocabulary cannot parse. An unparseable request now yields an
  empty contract without a false claim about the operator's clarity.
- Audit fixes on the decomposition series (#251–#254): low-signal confidence
  defaults read from their own constant; the vacuous manifest-drift test
  became an identity pin; three documents stopped attributing host-tools to
  its pre-#246 home; this changelog recorded the #241–#250 series.
- `core/planner.py` decomposed by five bounded pieces (2872 → 516 lines,
  #241, #243–#246): step admission → `core/step_sanitizer.py`; question
  classification and governing-doc routing → `core/doc_routing.py`; the
  system prompt (with its registry registration) → `core/planner_prompt.py`;
  raw-reply parsing → `core/plan_parsing.py`; the host-tools catalog and
  relevance gate → `core/host_tools_context.py`. What remains is genuine
  orchestration; the size ratchet banks each cut.
- One JSON-extraction core (#247, #248, #250): three per-module copies of
  "find the JSON object in a chatty LLM reply" collapsed into
  `core.plan_parsing.extract_json_object` (retired implementations kept as
  test oracles); nine duplicated routing vocabularies and three duplicated
  classifiers now live once in `core/doc_routing.py`, public, with every
  consumer importing the originals.
- `core/loop.py` lint debt paid (#249): 57 of 59 ruff findings fixed — dead
  imports, three never-imported annotation names, mid-import constants; the
  two try/except-in-loop findings are declared semantic (per-record fault
  isolation) and kept.
- `core/loop.py` decomposed by six bounded pieces (4733 → 4047 lines): replan
  vocabulary → `core/replan.py`; file-request classifiers, path parsers and
  the multi-file review decision → `core/file_request_intent.py` (new);
  evidence→prompt renderers → `core/loop_helpers.py`; the trimmed-memory
  rebuilder and the memory-block tags → `core/evidence_budget.py`.

### Fixed
- Сбой наблюдательного сенсора больше не исчезает молча (MIR-077, класс
  `core/loop.py`): одиннадцать мест, где широкий `except` глотал ошибку
  вектора уверенности, поддержки доказательствами, разногласий подсистем и
  прочих наблюдателей, теперь пишут единое событие `sensor_failed` через
  новый компактный модуль `core/sensor_journal.py`; четыре законных
  значения по умолчанию назвали причину. Храповик немотивированных
  молчунов опущен 61 → 46, а сам `core/loop.py` стал короче.
- The conflict detector no longer manufactures conflicts out of deixis or
  agreement-in-other-words (MIR-076, both classes measured live): a subject
  opening with this/этот/данный is context-bound and never grouped across
  sources, and claim values cluster as one when they are the same statement
  after negation canonicalisation or a >=3-token prefix match — two doctrine
  files agreeing now corroborate each other instead of quarantining.
- One evidence block no longer absorbs the whole budget overflow (MIR-073,
  measured live: the file the planner chose to read arrived as 50 of 12204
  chars while four siblings stayed pristine): `apply_total_budget` floors
  non-demoted blocks at a fair share and cascades the surplus largest-first;
  demoted memory still pays first to the absolute floor. When a planned
  source IS starved, the orchestrator now journals the plan-vs-budget
  contradiction (`planner_vs_evidence_budget`) instead of seeing nothing.
- Distinct shell commands get distinct artifact labels (#255, review round
  #257): the loop keys artifacts by label, and sibling commands rendered
  identically, so the second silently overwrote the first (measured twice
  live 2026-08-01); labels beyond two tokens carry a digest of the whole
  argv.
- An empty synthesis draft is not an answer (#256): a success status with no
  visible text (measured live: 14336 output tokens, empty string, banked
  success) now walks the same retry → adapt → honest-partial ladder an
  exception does, classified `blank_answer`.
- A declared non-delivery no longer banks as success (#262): the run's own
  completion declaration outranks chunk counts in the episode outcome —
  "the experiment was not performed" can be perfectly cited and still is not
  a success; the derivation lives in `_derive_episode_outcome`, fail-closed
  for declarations added later.
- Planner-level SSRF pre-filter judges the parsed host (#241): userinfo
  shapes like `http://evil.com@127.0.0.1/` no longer slip past the
  substring check; non-global IPs refused via `ipaddress` — the same rule
  the fetch-time boundary enforces.
- The balanced-object scanner no longer goes blind at an unclosed brace,
  with a restart cap against quadratic cost; a broken `PYTHON_PATH` no
  longer suppresses the Python auto-detection meant to rescue it (#247
  review round).
- The Codacy Security Scan workflow is removed (#242): 102 runs, 102
  failures since birth — its SARIF writer cannot read Cyrillic content and
  no setting reaches its container; CodeQL and Codacy's cloud analysis
  keep guarding. The tracked `.docx` binary is replaced by its extracted
  markdown twin (unreadable to the agent and undiffable in git).
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
