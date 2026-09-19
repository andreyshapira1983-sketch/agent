# The test suite as an instrument — audit of 2026-08-20

A green suite is a reading, not a proof. This audit points instruments at the
instrument: it breaks working code on purpose and asks whether the suite
notices. Everything below is measured. Where a number is a sample, the
denominator is stated; where something was not measured, it is listed as not
measured rather than left to look fine.

**This is not a census of the suite.** 8240 test cases ran; 80 were read
against a rubric and 74 deliberate breaks were applied to 6 modules of 275.
Nothing here licenses a claim about the other tests.

## The specimen that justifies the exercise

`tools/network_safety.py` re-checks the actual connected peer IP inside the
connection classes its handlers open — the defence against a second DNS answer
returning link-local metadata after the hostname was approved. Pointing those
handlers at unguarded stdlib connections left the whole suite green:
**8237 passed, 3 skipped, protection removed**.

    8237 green ≠ 8237 protected properties.

The two neighbouring tests were both sound: the guard is proven correct in
isolation against a fake socket, and the URL-time policy is proven correct on
hostnames. Neither says the guard sits on the path a request takes, which is
the only thing the attack cares about. Two good halves, an unexamined middle.
That shape recurs below often enough to be the audit's main finding.

## Method

Three instruments, and one rule that made the difference.

1. **Static claim census** over every test function: which assertions can only
   report that something exists.
2. **Per-test coverage contexts**: which production statements any test
   actually executes.
3. **Mutation with a control**: break one line, run the tests, and — this is
   the rule — replay every survivor against the WHOLE suite before calling it
   a blind spot.

The control is not ceremony. In `core/verifier_core.py` ten mutations survived
their slice; all three that were replayed globally were caught by tests in
other files. Reporting slice survivors as blind spots would have invented
seven holes that do not exist.

## Denominators

| | |
|---|---|
| Test files | 552 (541 at the top level, 11 under `tests/characterization`) |
| Test functions | 6727 |
| Cases collected | 8240 (the difference is parametrisation) |
| Production modules | 275 |

Statements, by whether any test executes them — three states, summing to 100%:

| State | Statements | Share |
|---|---|---|
| Executed inside some test | 22 882 | 68.4% |
| Executed only at import | 8 099 | 24.2% |
| Never executed at all | 2 464 | 7.4% |

The import-only class was classified by AST rather than sampled: 8083 of 8099
are declarations, imports, decorators, dataclass fields and module constants.
Sixteen are executable statements, each accounted for — a platform branch, a
docstring, a `__main__` guard, five import-time prompt registrations, and one
regex builder whose branch runs at import for every signal. Caveat: the
coverage data predates that day's edits, so two modified files were excluded
from the per-line attribution.

Structural flags over all 6727 functions — candidates, not verdicts:
existence-only assertions 355 (5.3%), no assertion at all 27 (0.4%),
assertions reached through a helper 24, "integration" tests built on mocks 3.
Every `xfail` in the repo is strict (12 of 12) and there are no unconditional
skips, so a stale expectation cannot sit green.

## The mutation sweep

Thirteen modules of the fourteen selected were measured. `cli/commands_plan.py`
never ran: its isolation worktree was refused as unverifiable and the agent
stopped rather than working in an unknown tree — the correct outcome, and a
gap in the coverage all the same.

| | |
|---|---|
| Breaks applied | 170 |
| Caught by the module's own slice | 70 |
| Survived the slice | 100 |
| — replayed and caught elsewhere | 7 |
| — **survived the entire suite** | 30 |
| — never replayed (UNKNOWN, not "fine") | 63 |

Of the thirty: 6 safety, 16 correctness, 7 cosmetic, 1 equivalent mutant.

Per module, breaks caught by that module's own tests — the spread is the point,
not the average:

| Module | Applied | Caught |
|---|---|---|
| `tools/shell_exec.py` | 14 | 12 |
| `core/self_build_producer.py` | 14 | 9 |
| `core/step_sanitizer.py` | 14 | 9 |
| `core/reflection.py` | 14 | 8 |
| `cli/intent_bridge.py` | 14 | 7 |
| `core/operator_intent_patterns.py` | 14 | 5 |
| `core/verifier_core.py` | 14 | 4 |
| `core/model_router.py` | 14 | 4 |
| `core/architecture_audit.py` | 12 | 4 |
| `core/learning_planner.py` | 14 | 3 |
| `core/memory_policy.py` | 14 | 3 |
| `core/approval.py` | 4 | 1 |
| `core/loop_step_execution.py` | 14 | 1 |

**A sampling bias that limits every number above.** `--limit N` takes the
first N mutations in source order, not a spread. For `core/model_router.py`
that meant 14 of 85 possible mutations, all from lines 32–125 of a 1776-line
file. Even a "measured" module was measured at its head.

### The safety-class breaks that nothing caught

Six of the thirty are safety-class. All six were closed the same day (MIR-113
covers the last four). Production code was correct in every case: what was
missing was a witness.

| Site | The break | What it would mean | State |
|---|---|---|---|
| `core/model_router.py:82` | the admission gate returns True | a disabled spec, or one whose provider is unsupported, becomes selectable, and `require_available` is skipped | closed — `tests/test_an_unsupported_provider_is_never_selectable.py` |
| `core/step_sanitizer.py:22` | userinfo split on the first `@` | the pre-filter judges a different host from the one dialled (MIR-111) | closed — fixed and witnessed |
| `cli/intent_bridge.py:146` | the answer-is-None branch returns True | a message is reported handled although no reply was produced; both call sites then consume the turn | closed — `tests/test_the_bridge_claims_only_what_it_did.py` |
| `cli/intent_bridge.py:197` | the except-branch returns True | the documented fail-safe inverts: when no planner model can be built, the model is treated as having spoken | closed — same file |
| `cli/intent_bridge.py:265` | token overlap inverts | a stop order cancels queued work it does not name, on any six-character coincidence | closed — same file |
| `core/loop_step_execution.py:155` | the risk-probe failure branch returns True | a tool whose risk probe RAISES is declared read-only and joins the concurrent batch — its own docstring promises the opposite | closed — a case added beside its siblings in `tests/test_integration.py` |

The rest of the thirty, by what they touch:

| Severity | Site | The break | What it would mean |
|---|---|---|---|
| correctness | `core/operator_intent_patterns.py:21` | `len(words) < 2` → `< 3` | the one-inserted-word tolerance switches off for every two-word term |
| correctness | `core/learning_planner.py:107` | `score <= 0` → `< 0` | files with no learning value at all become eligible study sources |
| correctness | `core/learning_planner.py:37` | `frozen=True` → `False` | the object that decides what the agent studies becomes mutable and unhashable |
| correctness | `core/learning_planner.py:116` | tie-break key changes | ordering among equally-scored candidates is unpinned |
| cosmetic | `tools/shell_exec.py:211` ×2 | output cap ±1024 / ±64 bytes | the only bound on subprocess output entering the journal is unpinned |
| cosmetic | `core/approval.py:86` ×3, `:87` | preview truncation moves | what the operator is shown before authorising an irreversible action is unwitnessed |

The `core/approval.py` group deserves its own sentence. It is rated cosmetic
because the bytes are few, but no test in the repo passes an output stream to
the approval provider and asserts what the human actually sees. The text on
which a human bases an authorisation has no witness at all.

## Closed on the day, each with a red-before

No existing test was modified or deleted during this audit; the trail is `git
diff` on the range, and it shows additions only. Production changed in exactly
two places, both defects the audit found, neither to make a test pass.

| Property that had no witness | Test added | Evidence |
|---|---|---|
| the safe opener's connections are guarded | `tests/test_the_opener_guards_the_socket_it_opens.py` | 6 of 7 red on the bypass, all 7 green on correct code |
| the lane's git front-end cannot reach a remote | `tests/test_the_lane_cannot_reach_a_remote.py` | 7 of 8 red before the fix |
| the repair gate needs both its preconditions | `tests/test_the_repair_gate_needs_both_its_reasons.py` | 1 of 4 red on the break — the one that pins it |
| the one-wedged-word tolerance | `tests/test_one_wedged_word_keeps_a_consent_request_a_consent_request.py` | 3 of 5 red on the mutation |
| the model-selection admission gate | `tests/test_an_unsupported_provider_is_never_selectable.py` | 2 of 8 red on the mutation |
| the probe names its true refusal reason | `tests/test_the_probe_knows_why_it_refused.py` | 3 of 3 red before the fix |
| the pre-filter parses the host that will be dialled | `tests/test_the_prefilter_sees_the_host_that_will_be_dialled.py` | 4 of 12 red before the fix; 1323 tests in the 47 related files stay green |
| reflection reads its newest logs, not its oldest | `tests/test_reflection_reads_the_newest_logs_not_the_oldest.py` | red on the mutation, green on HEAD, pin holds in both worlds |

Registry entries: MIR-107 (probe misreported its refusal), MIR-108 (four tests
forbade four names while the capability stayed open), MIR-109 (the opener's
only witness watched a class name), MIR-110 (banked — "verified diagnosis"
means "pytest came back"), MIR-111 (two parsers, one URL), MIR-112 (reflection's
recency window, where `max_logs` appeared in 0 of 552 test files).

Two of those deserve to be read together, because they are the same organ:
MIR-110 and MIR-112 are both the learning loop reporting healthy numbers about
the wrong material — one about evidence it never checked, the other about logs
it would never have read.

## What the audit learned about tests

- **Slice survival is not a blind spot.** Only a full-suite replay separates
  "this module's tests missed it" from "nobody noticed". Five of sixteen
  replays flipped.
- **An echo is not a second witness.** Four files asserted `not hasattr(SafeVCS,
  name)` for the same four names. Four copies of one claim, all watching an
  axis the danger does not travel on: the capability was reachable under any
  other name.
- **Two worlds can agree for different reasons.** Most candidate phrases for a
  routing test answered the same in the correct and broken builds — in one
  because a guard fired, in the other because the text reached no branch at
  all. A test built on those is green forever and proves nothing. Finding a
  discriminating input took measurement, not reasoning.
- **A name is not a capability, and a label is not a property.** MIR-108,
  MIR-109 and MIR-110 are the same error in three places: something that
  stands for the property acquired the authority of the property.

## Instrument defects found

- `scripts/mutation_probe.py` read any non-zero pytest exit as "already red".
  Exit 4 means a path does not exist. Four audit agents were sent after tests
  that were never there. Fixed (MIR-107).
- `--limit` is a source-order prefix, so a long file is probed only at its
  head. Recorded, not fixed; it bounds every count in this document.
- The workflow runner places full checkouts under `.claude/worktrees/`, inside
  the repository. Untracked and unignored, they were scanned by every repo-wide
  tool — ruff reported 1249 findings where the real number is 113. Now ignored.
- The auditor's own first sweep was launched with hand-typed test paths, ten of
  eleven of which did not exist. The agents refused and said so. The generated
  list, derived from the imports, contained none. Read from the code, not from
  memory — including when the reader is the one writing the report.

## How to continue

Eight modules were never probed and the six that were are covered only at their
head. Continuing means resuming the run (completed agents replay from cache) or
probing the remaining modules directly, and — for any survivor — replaying it
against the whole suite before it is called anything at all.

## Do the new tests guard a boundary, or decide for the agent?

A suite that grows by pinning the agent's choices stops being an instrument and
becomes a timetable. The distinction applied to every test added by this audit:
a test may pin a limit on power, a causal claim, or a contract the code itself
declares. It may not pin which way the agent should think.

| Test added | Pins | Verdict |
|---|---|---|
| `tests/test_the_probe_knows_why_it_refused.py` | a tool must not report "could not measure" as "measured a failure" | instrument boundary; no agent behaviour involved |
| `tests/test_the_opener_guards_the_socket_it_opens.py` | a private peer at connect time is refused | power boundary |
| `tests/test_the_lane_cannot_reach_a_remote.py` | no remote verb reaches git | power boundary. The implementation is an allowlist, so adding a LOCAL verb is a one-line edit and these tests stay green — the test forbids reaching a remote, not extending the tool |
| `tests/test_an_unsupported_provider_is_never_selectable.py` | a spec cannot reach selection without passing the gate | power boundary. An earlier draft asserted a specific provider was absent from the roster — a configuration pin that would have reddened the day that provider was authorised. Rewritten to use a name no roster will hold |
| `tests/test_the_repair_gate_needs_both_its_reasons.py` | an unverified diagnosis is denied; satisfying the gate yields approval, never permission | power boundary — with a caveat worth naming: it also pins the current verdicts, so deliberately changing the policy means changing this test. That is the intent: such a change should be a decision, not a drift |
| `tests/test_one_wedged_word_keeps_a_consent_request_a_consent_request.py` | a request for the operator's consent is not turned into an inbox query | contract the module declares in its own comment. The closest to the line of the six, because it pins routing on three concrete inputs. It says what must not be silently swallowed, not which route the agent must pick |

None of them says the agent must call a particular function, choose a
particular model, or reach a particular conclusion. The one banked invariant,
MIR-110, is deliberately unprescribed for the same reason: it says a verified
diagnosis must rest on evidence of a matching class, and refuses to name which
class.

The failure mode to watch for in later rounds: a mutation survives, and the
cheapest way to redden it is to assert the current output for the current
input. That closes the mutation and pins a decision. The test that belongs
there is the one naming the property the output was supposed to have.

