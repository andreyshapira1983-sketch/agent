# Self-audit lessons — 2026-07-17

A full multi-model re-audit of the codebase surfaced 13 real issues (bugs,
duplicated logic, dead code, and doc/code mismatches) that had accumulated
because many different models contributed over time. All 13 were fixed, each
guarded by a regression test. This file is the durable record so the agent (and
future self-build/self-repair passes) knows these problem *classes* existed and
how they were resolved.

## Recurring anti-patterns to watch for

These are the patterns that produced multiple findings — check for them first in
any future audit:

1. **Silent `except Exception` that disguises a failure as a different outcome.**
   A raising code path caught and turned into a benign-looking result (e.g. a
   crash reported as "no patch"). Fix: name the error, tag it, and surface it in
   the structured record instead of swallowing it. (Historical: commit 31b8e3f.)
2. **Duplicated logic that drifts.** The same helper/formula/constant copied into
   two or three modules instead of imported once. A later edit to one copy
   silently diverges from the others. Fix: extract to one home and import.
3. **Fuzzy string-matching where an explicit index mapping is available.** Patch
   the exact record by index, never by `line.startswith(...)`/`substring in line`
   — those silently no-op on edge cases.
4. **Type/vocabulary mismatch across a boundary.** One layer emits terms a
   downstream lookup table doesn't recognise, silently falling back to a default.
5. **Dead code that re-introduces a known bug if ever called.** Delete replaced
   implementations; don't leave them as footguns.
6. **A whole module written to fix a live failure mode, but never wired into its
   entry point** — the failure mode stays live. Verify capabilities are actually
   reachable from a CLI/loop, not just unit-tested.

## The 13 findings and their fixes

| # | File | Anti-pattern | Fix | Guard test |
|---|------|--------------|-----|------------|
| 1 | `core/incremental_splitter.py` | no-op line (`span += 0 if …`) undercounted decorated-method line budget | compute `start = min(node.lineno, decorators)` and size the span from it | `tests/test_incremental_splitter.py` |
| 2 | `core/ingestion.py` | `TEXT_EXTENSIONS`/`SKIP_DIR_NAMES` duplicated from `ingestion_utils.py` | import them from `ingestion_utils` | `tests/test_ingestion_helpers.py` |
| 3 | `core/budget_ledger.py` | `budget_limit_label` byte-identical copy of `budget_governor.py` | import the one in `budget_governor` | `tests/test_budget_ledger.py` |
| 4 | `core/confidence_vector.py` | `evidence_score` reimplemented `confidence_gate.compute_confidence` | delegate to `compute_confidence` | `tests/test_confidence_vector.py` |
| 5 | `core/self_apply_lane.py`, `core/repair_proposal.py` | "tests passed" predicate triplicated | import `_tests_passed`/`_test_summary`/`_diff_summary` from `self_repair_utils` | `tests/test_self_apply_lane.py`, `tests/test_repair_proposal.py` |
| 6 | `tests/test_work_session.py` | test swallowed all exceptions → couldn't fail on its stated purpose | `pytest.fail` on the `AttributeError` it exists to catch | (self) |
| 7 | `core/actuation_gateway.py`, `core/campaign.py`, `core/source_registry_store.py`, `core/source_ranker.py`, `app/io.py` | dead alias / doc-behavior mismatch / dead methods reviving a perf bug / unreachable data / duplicate silent-except encoding block | remove/clarify each | existing suites |
| 8 | `core/verifier_core.py` | downgraded-claim annotation patched by fuzzy string-match → user saw `[verified:]` on an internally-distrusted claim | track `examined_annotated_idx` and patch by exact index | `tests/test_receipt_consumer.py::test_verify_demotes_claim_that_starts_with_its_citation` |
| 9 | `core/model_router.py` | `for_task()` sent ComplexityTier name to the usage ledger (priced everything "unknown") | resolve real registry `cost_tier` via `_cost_tier_for_route` | `tests/test_adaptive_routing.py::TestForTaskCostTier` |
| 10 | `core/loop.py` | verify-driven re-plan omitted `llm=`, silently dropping the escalated model | pass `llm=_task_planner_llm` on the verify re-plan | `tests/test_unresolved_citation_replan.py` |
| 11 | `cli/commands_misc.py` | `:team-run --allow-effects` hardcoded `Path(".")` for the subagent sandbox | thread `workspace` through and use it | `tests/test_cli.py::…team_run…` |
| 12 | `api/server.py` | shared `AgentLoop` built racily and driven concurrently under the threadpool | double-checked construction lock + a run lock serialising `/ask` | `tests/test_api_server.py` |
| 13 | `core/episodic_hygiene.py` | pruning module never wired into `:hygiene`, so FIFO distractors stayed live | add `agent.prune_episodic()`, `:hygiene episodic` subcommand, and include it in `:hygiene all` | `tests/test_episodic_hygiene_wiring.py` |

## Procedure for the next audit

1. Grep for `except Exception:` blocks and confirm each is a narrowly-scoped,
   logged best-effort boundary — not a swallow of the primary operation.
2. Look for families of same-named modules (`self_*`, `verifier_*`, `source_*`,
   `memory_*`) and check for duplicated/drifted helpers, formulas, constants.
3. For each capability module, confirm it is actually reached from a CLI command
   or the loop — not merely unit-tested in isolation.
4. Every fix ships with a regression test that FAILS on the old code (verify by
   temporarily reverting the fix) and passes on the new code.
