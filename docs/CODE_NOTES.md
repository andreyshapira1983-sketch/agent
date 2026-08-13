# Code notes — what was done to these files and why

> Audience: the agent. Deleting this file breaks nothing — no test and no check
> reads it. The operator-facing index of these documents, in Russian, is
> [OPERATOR_NOTES.ru.md](OPERATOR_NOTES.ru.md).

Working notes kept by the agent, for the agent. A session starts with no memory
of the last one: everything known about a file is what is written down. This is
where the *why* lives so the code itself can stay short.

**Not documentation of the program.** Nothing here is a requirement, a spec or
an interface. Deleting this file breaks nothing — say so at the top of any file
like it, because a document whose purpose nobody stated is a document that gets
deleted, and rightly so.

**Rules for this file**

- The code carries the contract (one or two lines: what goes in, what comes out,
  what is raised). Everything else — history, measurements, rejected options,
  the reason a line exists at all — comes here.
- Link to files with Markdown links. `scripts/docs_link_check.py` verifies every
  relative link in `docs/` against the filesystem and runs in CI, so a note that
  points at a file that moved becomes a red test rather than a lie. Links **from
  `.py` files are checked by nothing** — that is how a doc deleted in `f6d071a`
  left twenty references behind it, none of which failed anything.
- Date every entry. A note without a date cannot be judged stale.

---

## [cli/repl.py](../cli/repl.py)

### What it is

Everything that happens between the operator's keyboard and the agent, for an
interactive session. Started from [main.py](../main.py) → [cli/app.py](../cli/app.py)
→ here. The one-shot `--ask` path goes to [cli/one_shot.py](../cli/one_shot.py)
instead and does not touch this file.

Two halves:

1. `_StdinLineReader` — a single owner for stdin. One daemon thread performs the
   blocking reads and feeds a queue; every consumer (top-level prompt, block
   modes, approval prompt) pulls from that queue. Single ownership is what keeps
   them from racing each other.
2. `run_repl` — the dialogue loop: read a message, recognise the input modes and
   the `:commands`, otherwise spend a rate-limit token and call the agent.

### Why a thread and a queue at all

Line-buffered input delivers a pasted block as many separate lines. Before this
design, one pasted spec became 12 fragmentary questions — each executed as its
own run. Terminal features do not help: Windows cooked-mode input does not
surface bracketed-paste markers. So the reader coalesces lines that arrive
back-to-back (within `PASTE_COALESCE_GAP_SECONDS`, 0.05s) into ONE message. A
human typing pauses far longer than that, so separately typed lines stay
separate.

### Contracts worth knowing before changing anything

- **The pump must always put its EOF marker.** A reader thread that ends without
  it leaves every later read blocked forever. Both a real end-of-input and a
  failed read end the reader and surface as `EOFError`; `read_error` is what
  tells them apart afterwards.
- **The pump thread must stay a daemon.** A non-daemon thread parked on
  `stdin.readline()` keeps the interpreter alive at exit — the suite passes and
  the process never returns.
- **Collectors propagate, they do not decide.** `_collect_instruction_buffer`,
  `_collect_pasted_block` and the modes still inline in the loop all let
  `EOFError`/`KeyboardInterrupt` reach `run_repl`, which prints a newline and
  returns 0. An empty result is returned as `""`; whether that means "discard"
  is the loop's judgement, not the collector's.
- **An empty message must never reach the agent.** It costs a rate-limit token
  and asks the model nothing. Three paths lead into the message and all three
  refuse it now (top of the loop, `<<<` block, backslash continuation).
- **`.strip()` on the joined block is load-bearing**, not cosmetic: it removes
  the empty remainder a bare `>>>` leaves behind, which is why one terminator
  check covers both the bare and the glued form.

### 2026-08-06 — what was done

Read line by line, then measured rather than reviewed. In order:

| commit | what |
|---|---|
| `318b8ad` | a missing EOF fails the suite instead of hanging it (two reads got `timeout=5`) |
| `ad6a1a2` | three more breaks in the reader now name themselves; `call_without_blocking` added to [tests/conftest.py](../tests/conftest.py) |
| `38bea48` | an empty backslash continuation no longer reaches the agent |
| `87267eb` | `_stdin_is_interactive` catches a broken console, not a bug in this module |
| `0f0ff37` | the block terminator had a branch that could not be broken; removed |
| `ec5d1b0` | `_collect_pasted_block` extracted from the loop |

### Measurements behind those changes

**Mutation probe over the whole file** (`scripts/mutation_probe.py`, 31 breaks):
28 were caught by a red test. Three were not caught and not survivors either —
each HUNG the suite for the full 90s timeout:

    repl.py:131  self._started = False -> True   pump never starts
    repl.py:143  daemon=True -> False            green run, no exit
    repl.py:146  while True -> while False       pump ends without an EOF

After `ad6a1a2` all three fail with a name in 3–8s. A hang in CI is a timeout
with no test attached; that is the worst shape a defect can take, and the
probe's own `run_tests` has no wall clock, so an interrupted run once left a
mutant on disk (recovered from git, nothing lost).

**Route breaks before extracting** (`<<<` mode, six details broken one at a
time): five were named by a test within a second; `stripped == ">>>"` was not.
It could not be broken observably — a bare `>>>` also ends with `>>>`, so the
next branch caught it and `.strip()` erased the difference. Removed in
`0f0ff37`.

**Wiring breaks after extracting** (nine, split deliberately): the four
collector breaks fail through `TestCollectPastedBlock` in
[tests/test_cli.py](../tests/test_cli.py); the five wiring breaks — result not
assigned, wrong prompt, empty block not discarded, EOF not exiting, notice
changed — fail through the characterization tests that go via `main()`. A
collector can be perfect and still be connected wrongly; only the second group
would catch that.

### Still open in this file

- The module docstring describes a structure that no longer exists: it says the
  startup wiring "stays in `main()`" and that suites patch `main`. Both moved to
  [cli/app.py](../cli/app.py) two refactors ago. The same stale claim sits in
  [app/operator_task.py](../app/operator_task.py) ("`main.py` re-exports
  `_handle_operator_task`" — it does not).
- The paste-coalescing explanation is written twice, in the module docstring and
  again as a comment above `PASTE_COALESCE_GAP_SECONDS`.
- One comment still points at a doc deleted in `f6d071a`; nineteen more such
  pointers sit in fourteen other files, two of them as entries in a checker's
  exclusion list. Being removed, not restored — the doc was deleted on purpose.
- `run_repl` is 104 lines with one input mode still inline. Next:
  `_collect_operator_task_block`.

---

## [cli/one_shot.py](../cli/one_shot.py) — walked 2026-08-07

The other branch of the `--ask` fork: one question in, one answer out, exit 0.
No memory of either kind, approval per `--auto-approve` (where `off` means *no
provider wired*, so escalated tools stay blocked), explicit `:commands` ahead of
intent routing, deep escalation only with `--reason`/`--expect`, `stream=False`
because the formatted print is the sole output.

**Fourteen breaks, fourteen named.** The only file walked on this branch where
the measurement found nothing: memory flags, all three approval branches,
command precedence, the bare `?`, the unknown-command message, both intent-router
short-circuits, escalation, streaming, the question passed through, and the
`lstrip()` before the `:` check. It is also the only one with a test file
written *for it as a contract*
([test_cli_one_shot_policy.py](../tests/characterization/test_cli_one_shot_policy.py))
rather than copied from a neighbour — which is the same pattern seen from the
other side in [cli/repl.py](../cli/repl.py).

What was wrong was the docstring: it claimed `build_agent` is patched "on `main`
in 22 places" and that startup "moves in the next Phase 7 step". Measured: zero
patches on `main`, 21 on `cli/app.py`, and the move happened long ago. The seam
itself is real and stays; only its account of the world was stale.

**A harness lesson, paid for here.** The first break run reported all fourteen
as held with *zero* failing tests — the selection named a file that does not
exist, pytest exited non-zero before collecting anything, and the script read
"non-zero" as "a test caught it". Fourteen meaningless green ticks. The script
now separates "caught", "not caught" and "the run never happened".

## EpisodeRecord — route-dependent field semantics (schema debt, 2026-08-07)

Found while walking the orchestrator, not while working on memory. Recorded and
left alone: it is not a runtime defect, and fixing it is a migration, not a
detail of the audit that surfaced it.

`EpisodeRecord.goal` and `.question` mean different things depending on which
producer wrote the row. Four producers, four conventions:

| producer | `goal` | `question` |
|---|---|---|
| the question path (`loop_memory_write:205`) | `"Answer the question: {question}"` — derived | the question |
| an aborted run (`loop_memory_write:404`) | `"(run aborted before completion)"` — a status marker | the question |
| self-build (`self_build_memory:140`) | the REAL task goal, truncated to 500 | `kind` — a category |
| self-repair (`self_repair:510`) | `"repair"` — a category | `"fix {path}"` — the object |

In the third row the fields swap roles outright.

**No behavioural dependency was found.** Episodic search compares only
`question` (stated in `smart_memory`: "Only the stored `question` field is
compared, not goal/summary/tags"), and every `.goal` read elsewhere belongs to
an autonomous run's config, not to an episode. Breaking the question path's
description to `"x"` left all 7139 tests green — correctly, since nothing
decides on it.

Two things follow, and they matter more than the redundancy:

* the system already routes AROUND the pair. Provenance is recognised by TAGS
  (`search_by_tags(["self-build", "failed"])`), because the two fields do not
  carry enough meaning to tell writers apart;
* the canonical facts survive regardless: `question` and `run_id` stay intact,
  so a corrupted description degrades the presentation, never the recoverable
  history.

The risk is latent rather than active: the first person — human or agent — who
reads `episode.goal` expecting "the goal" will step on a mine, because in three
of four routes it is not one.

## Memory tags — contract change, 2026-08-07

The contract, as the operator stated it:

1. `:remember` always keeps the operator's labels verbatim.
2. Reserved tags are recognised only by exact match against the known set
   (case-insensitively — the policy lowercases before comparing).
3. An unknown label does not affect the write-policy decision, in either
   direction.
4. `source="user-explicit"` is by itself sufficient to admit a write.

**Why the old rule existed and why it does not apply.** ASCII-only is a real
policy in this codebase for identifiers — write paths, shell argv, URLs, trace
ids — where a non-ASCII value is a genuine hazard. A tag is none of those: it
never becomes a filename, an argument or a URL; it lives in
[core/models.py](../core/models.py) as a plain `list[str]` and is persisted to
JSONL written with `ensure_ascii=False`. The filter sat in exactly one place,
[cli/parsers.py](../cli/parsers.py), and it dropped a non-ASCII tag in silence,
substituting `user-approved` — handing the write policy a *consent tag the
operator never typed*.

**What is still reserved**, in [core/memory_policy.py](../core/memory_policy.py):
the consent set (`preference`, `fact`, `decision`, `insight`, `user-approved`,
`project`) and the blocking set (`transient`, `temporary`, `do-not-save`,
`ephemeral`). These are rule names, not English words that could be translated:
renaming them means migrating every record already written with them.

**Migration.** None is possible for records already on disk. A label dropped
before this change was never stored, so there is nothing to restore; those
records simply carry `user-approved` where the operator wrote something else.
Records written from now on carry what was typed. No schema change: the field
always allowed any string.

**Boundaries pinned** (`TestUserLabelsVersusReservedTags`): an explicit write
needs no tag; a reserved consent tag still admits an agent write; a label does
not; a reserved blocking tag still blocks even an explicit write; a label that
resembles one does not block; reserved words match whole, not as substrings,
and in any case. The last one was added after breaking the policy's
`.lower()` and finding nothing went red.

---

## [tests/test_cli.py](../tests/test_cli.py)

**2026-08-06.** Carried ten ruff findings, all predating this work: nine about
launching `git` as a subprocess without the in-line suppression this project
requires (per [ruff.toml](../ruff.toml): path-based ignores are not applied by
Codacy's remote runner, so suppressions live in the lines themselves), and one
cosmetic.

Healed by deletion rather than annotation: three identical eight-line copies of
"make a temporary git repo" were replaced with `run_git` from
[tests/conftest.py](../tests/conftest.py), which already carries the
suppressions and captures output. File is now clean.

The rest of the test tree still holds 66 findings of the same family —
`tests/test_shell_exec.py` (10), `tests/test_learning_planner.py` (9) and a long
tail. Untouched: unrelated to this work.

---

## [app/budget_guard.py](../app/budget_guard.py) — retiring a resumed pause

**2026-08-08.** A successfully resumed run used to leave its pause record
behind forever: nothing wrote `paused -> done` for `resume_checkpoint` tasks,
and the resumed run carries a fresh trace_id that joined to nothing. Measured
by probe before fixing (pause -> resume -> success -> still listed resumable).

The join is now carried explicitly and in one direction only:
`ResumeDecision.resumed_paused_trace` (set solely by the paused branch of
`resolve_resume`) -> `run_one_shot(resumed_from=...)` ->
`_run_agent_with_budget_guard` -> `_retire_resumed_pause` on the no-exception
path. Design decision, tested on both poles: retirement happens only when the
resumed run completes without a new budget stop; a resume that pauses again
retires nothing — the work is still not done, so the old record stays truthful
and the second stop queues its own task under the fresh trace.

Retirement is best-effort (`except: pass`) like the rest of the module: queue
trouble must not eat the answer the user is owed. The retired task keeps its
report and gains `resumed_by=<new trace_id>`, so the audit trail shows which
run closed it (`resumable_task_retired` in the journal).

---

## [core/model_router.py](../core/model_router.py) — `UsageTrackedLLM.stream_complete`

**2026-08-08.** The wrapper defined only `complete`; `__getattr__` handed
`stream_complete` to the raw provider LLM, so every streamed synthesis call
skipped `assert_can_start`, `log_start` and `record`. Streaming is the REPL
default: the primary interactive mode under-billed every turn, enforced no cap
at synthesis, and the synthesis pause checkpoint was unreachable there.
Measured by probe before fixing (ledger showed only the planner record; a
budget of one call did not stop a streamed run).

The fix is a real `stream_complete` on the wrapper mirroring `complete`'s
billing. One deliberate difference, decided at review: **no provider failover
for streamed calls.** By the time a stream dies, chunks may already be on the
user's screen; replaying from a substitute provider would emit the answer
twice. The error is recorded (`status=error`) and re-raised — the synthesis
ladder owns what happens next. A provider double without `stream_complete`
falls back to the billed `complete`, the same rule `core/llm.py:384` applies
inside `LLM` for non-streaming providers.

The file-size ratchet ceiling moved 1800 → 1860 for this: the billed method
must live on the wrapper, so the growth is the fix, not drift.

## [cli/intent_bridge.py](../cli/intent_bridge.py) — the route answered, and erased the turn

**2026-08-12,** live session `trace_d322a875`. The operator sent six messages.
Two were caught by the deterministic operator-intent matcher: a request to
locate the episodic store went to `implementation_plan` (its text contains a
`/` and the stem «реализац», which is the whole match), and a request to
inspect the store's constructor went to `smart_memory_status` (the literal
"episodic memory"). Neither answered what was asked — that is a routing defect
in its own right, and it is not what this note is about.

What this note is about is what happened next. The session history has exactly
one writer: the run tail (`core/loop_run_tail.py`, `memory.record_turn`). A
routed message never reaches it — the handler answers, `handle_conversational_
operator_input` returns `True`, and `cli/repl.py` does `continue`. So the
message was answered and then did not exist. The next planner saw
`turns_visible=1` after three operator messages, wrote "the previous step is
not visible in context", and the dependent experiment stalled with nothing
wrong on its own side. Asked afterwards to list every request in the session,
the agent counted **four** where there had been six — and the two it lost were
precisely the two a route had handled.

Invariant: *an operator message and the reply given to it belong to the session
record regardless of which path answered.* Choosing a route may change who
answers; it may not delete the exchange.

`_record_routed_turn` closes it at the boundary that owns the gap — the bridge
is the only place that knows both the operator's text and that something
answered outside the loop. It writes through the existing `record_turn` and
emits the existing `memory_write` event with `answered_outside_the_loop: True`,
so the two producers stay distinguishable in the journal.

**What it carries, and what it does not.** For a command route it records the
question and *which command answered* — not the report body. The handlers print
to stdout, and capturing that stream would hide the prompts of the handlers
that wait for operator input. That is enough for both observed failures: the
next planner sees the question again, and it also sees why no
`POSSIBLE/IMPOSSIBLE/UNKNOWN` verdict exists in the conversation. The local
"reply only with:" path is different — its answer text is known right there, so
the real words are recorded.

Scope deliberately stops at conversational messages. An explicitly typed `:`
command (`cli/command_dispatch.py`) is not a conversational turn and stays out.

## REFUTED is a polarity, not a shade of unverified — 2026-08-12

Live case, `trace_d322a875`, turn 3: the verifier's content gates proved three
claims did not follow from the evidence they cited (`claims_refuted_by_content`)
— and the answer shipped unchanged, the episode banked `success` with
`usage_eligible=True`, and a procedural candidate was minted from it. The
session's own audit then located the exact loss: a refuted chunk was demoted
into `topic_supported_but_claim_unverified`, the same class as merely-unchecked,
and from there every decider read only verified/weak ratios. The operator named
the invariant: *five good claims do not make one known lie less false.*

The class boundary already existed in the code and was reused, not invented:
`ClaimReason` is set only by the gates that PROVE a contradiction (arithmetic
over the cited excerpt, a salient literal absent from it, an absence claim its
own excerpt refutes). Demotions that carry no proof — memory independence,
unsupported figures — keep their old class: an incomplete excerpt does not
prove falsehood.

What changed, all at existing owners:

- `core/verifier_core.py` — a chunk whose `ClaimReason` survives gets verdict
  `refuted`, the marker `[claim-refuted]` (the old `[claim-figure-unverified]`
  read as "unchecked" to the operator — polarity lost in the user-visible
  channel too), and its own `refuted_chunks` counter. A claim verified by
  ANOTHER of its citations clears the reason: the gate compared against one
  source of two, and a residual reason would have made
  `_replan_on_refuted_claims` count verified chunks as refuted.
- `core/loop_run_tail.py` — `refuted_chunks` joins `weak_chunks` (for the
  outcome ratio) and, separately, raises the defect signal `content_refuted`:
  the fact, not the fraction.
- `core/smart_memory.py` — `content_refuted` joins
  `DISQUALIFYING_DEFECT_SIGNALS`, so both learning gates (episode eligibility
  and procedure credit) refuse the run in one shared place.
- `core/low_evidence_policy.py` — refuted counts toward `unverified_total`;
  those chunks sat in `topic_supported` before, and splitting the polarity out
  must not have lifted the evidence floor.
- `core/verification_summary.py` — the new verdict gets its Russian line; the
  wording guard would rightly have refused a bucket that explains nothing.

Deliberately NOT done here: no unconditional replan on refutation. The trigger
`claim_refuted` still only shapes a replan another failure starts. Whether a
proven-false claim should force a retry is a separate decision with a real
cost model (every refutation would buy another model call); what could not
wait is the learning contamination — a known lie banking as reusable
experience.

The numeric hole stays open and is now precisely mapped: a bare-number claim
("the limit is 42" against `limit: 17`) passes every gate and lands `verified`
with an amplifying marker — arithmetic gate does not parse that shape, salient
literals exclude bare numbers by design. MIR-060 xfail continues to document
the class; extending `claim_arithmetic` to key-value equality is its own
repair with its own fail-before.

## Refresh before adapt — the catalog outage that silently swapped every model (R7, 2026-08-12)

Blind acceptance run probe_r1, proven by an exhaustive scan of every retained
trace journal: **all 34 model calls in the window executed on
`claude-sonnet-4-5` and `gpt-4o-mini`, zero on the configured
`claude-sonnet-5`** — while `session_start` advertised sonnet-5 and
`config/model_catalog.json` correctly named it the standard-tier model.

The authority chain that produced this, every edge measured:

1. the catalog's `updated_at` was 15 days old against a 7-day TTL, so
   `_load_catalog()` refused it (`core/model_catalog.py:211`) — the catalog was
   CORRECT and expired, not stale in content;
2. `tier_model_for` returned "" and, because the operator names tier providers
   (`AGENT_TIER_PROVIDERS_*` in the daemon .env — the same vars
   `tests/conftest.py` strips per test), the router fell to
   `_declared_model_for(provider)`;
3. that helper took the FIRST registry spec for the provider, and the registry
   is assembled builtins-first (`model_router.py:291`), so the hardcoded
   `anthropic-default: claude-sonnet-4-5` and `openai-default-small:
   gpt-4o-mini` shadowed the operator's own `config/model_registry.json`
   sonnet-5 declarations;
4. the route reason (`complexity:standard:anthropic`) looked IDENTICAL whether
   the model came from a live catalog or from the builtin fallback, so the
   whole degradation was invisible in the journal — the deep tier has an
   honest `deep_downgraded:catalog_expired` diagnosis, light/standard had
   nothing.

Operator ruling: an autonomous agent maintains its own catalog freshness.
**Refresh → verify → adapt**, in that order; adapting to a dead list is the
disorder («бардак») this note exists to prevent recurring.

What changed, at existing owners:

- `core/model_catalog.py` — `ensure_fresh_catalog()`: a DEAD (expired or
  missing) catalog triggers ONE refresh attempt per process, only when
  credentials exist, with an `AGENT_CATALOG_AUTOREFRESH=0` kill-switch;
  `tier_model_for` consults it before falling through. The test suite runs
  with the switch off (`tests/conftest.py`), autorefresh tests re-enable it
  against stubbed `refresh_catalog`.
- `core/model_router.py` `_declared_model_for` — operator-declared specs now
  outrank builtins, and within each group models inside `AGENT_MODEL_MAX_COST`
  outrank models the ceiling locks away. The second key was measured, not
  guessed: with builtins merely demoted, the first operator spec became the
  frontier Opus that the registry deliberately parks behind the ceiling
  ("raise the ceiling to release it").
- `core/model_router.py` `_resolve_tier_provider` — a declared-fallback model
  confesses itself: `…|model_source:declared` in the route reason, so the next
  catalog outage is a visible event in the ledger instead of a silent swap.

Live counterfactual on the real config files (probe environment shape:
expired catalog, named tier providers, cost ceiling `medium`): standard
resolves `claude-sonnet-5` with the confessing reason — the same inputs that
produced 26 live sonnet-4-5 calls before the fix. The full end-to-end proof is
one live run away: the first `for_task` with real keys refreshes the catalog
stamp and routing follows the catalog itself.

Not claimed: no evidence the agent modified its own routing; the builtins are
repo code that simply never moved when the config did. The two probe env vars
are confirmed by the conftest comment documenting the same variables breaking
routing tests from the operator's .env.

## R2/R3: the count that contradicted itself, and the cross-source claim refuted twice (2026-08-13)

probe_r1, B1 (`trace_4f295f8f`): the conclusion claimed "пять полок" listing
four, and "три позиции" listing two with the third rejected in the same
sentence — both shipped `unverified`, banked success/eligible. Three separate
roots closed at their owners:

- `verifier_utils.enumeration_count_reason` (R2) — a claimed cardinal (digit
  or RU/EN number word) contradicting the chunk's OWN parenthesized
  enumeration is `count_mismatch` → `refuted`. Items the sentence itself
  rejects («не подходит») are excluded from the count. The claim is internal,
  so a resolved citation does NOT clear it (`verifier_core` keeps the reason
  through `any_matched`): the evidence knows nothing about the sentence's
  argument with itself.
- `_SENTENCE_SPLIT_RE` (verifier_patterns) — a dot before a LOWERCASE letter,
  Latin or Cyrillic, is an abbreviation, not a sentence boundary. «шт. не»
  was split mid-parenthetical, hiding B1's enumeration from any chunk-level
  gate — the same cross-script blindness class as the relevance axis.
- `verifier_utils.literal_covered_by_union` (R3, live B3 `trace_9cd331c`) —
  the absent-literal gate now steps aside when the missing literal is covered
  by the UNION of the chunk's cited evidences (excerpt + source_id). A
  cross-source claim cites two sources for two halves; testing each citation
  against the whole chunk refuted a true statement twice, mirror-wise, and —
  after the REFUTED polarity repair — quarantined a correct answer.

Known-open in the same family: reformatted literals (a date rewritten
`01.08.2026` for `2026-08-01`) would still fire the literal gate; the numeric
key:value hole (MIR-060 xfail) stays documented.

## R8/R9: honesty must not be punished (2026-08-13, traces bd02fff1 / ac75fc92)

Two of my own organs turned their teeth on honest answers the day they went
live end-to-end.

R8 — `verifier_utils.absent_literal_reason` refuted TRUE claims against
budget-TRIMMED excerpts: `evidence_budget` cut the part carrying the literal
(`kept 9617 of 12191`), and "absent from the cut" read as "absent from the
source" — seven false REFUTED in one turn, honest answer quarantined. The
fifth gate's own principle applied: an incomplete search proves no absence. A
trimmed excerpt (the `[INTENT-BUDGET:`/`[TOTAL-BUDGET:` notice IS the flag,
one definition in `evidence_budget`) may still prove presence, never absence.

R9 — `answer_contradiction.contradicted_claims` matched SUBJECTS, not
propositions: "файл есть в листинге" (Facts) plus "содержимое не читал"
(Unverified) scored self_contradicted×4, the enforcement rewrote the body,
and the epistemically best answer of the day banked partial/quarantined — the
more honestly the agent scoped its ignorance, the harder it was punished. A
knowledge-boundary line («не читал», «данные не передавались», no data) is
not a denial; a subject appearing ONLY in boundary lines is not a dispute.
The 2026-08-10 true-positive class («не подтвердилось, может быть неверным»)
stays caught — the guard test carries both directions.

## R1/R6: units survive a broken paste, and a task is not an order for a plan (2026-08-13)

R1 — probe_r1's multi-line cards reached `observe` already corrupted: the
markdown `#` markers were absent and the final unit body was missing
(`observe` verbatim). Who corrupted them — terminal, paste, REPL, shell — is
a separate open question; what is proven is that the corruption occurred
upstream of `completion_contract`. The
unit extractor knew only markdown headers, so «Три части… S1 — … S2 — …
S3 —» carried zero units, obligations=[], and the visibly EMPTY «S3 —
Сравнение» died silently: achieved 4/4, usage_eligible=True — a channel-Б
FALSE DONE. Now `completion_contract` reads full-line plain labels (two or
more — a single labelled line stays prose), and a unit declared with no body
becomes an ambiguity → `needs_clarification`, through the existing wiring.

R6 — three live hijacks stopped at the matchers, positive controls pinned:
comparison of concrete file operands without an explicit planning/review
word must not classify as a control-plane planning request (the proven class;
not a universal routing doctrine); the stem «реализац» left the implementation-plan
loose branch («в текущей реализации» is an adverbial, not an order); and
code-shape markers («конструктор», «.py», «в коде»…) veto the
smart-memory-status route before its strong phrases fire.

## R3 addendum: the union searched a comma-glued needle (2026-08-13, trace a5813910)

`absent_literal_reason` joins up to three missing literals with ", " into one
`expected` string. `literal_covered_by_union` searched that GLUED string as a
single substring — it could never match, so the union cleared nothing, and a
live self-inspection run took four false `[claim-refuted]` on true claims that
merely named several files. Each literal is now sought separately across the
union (excerpt + source_id); all must be covered to lift the reason. Known
still open (R8b, next session): chain excerpts capped at CREATION carry no
budget notice, so the trimmed-excerpt guard cannot see that truncation.

## R3 addendum 2: an address of the chain covers a name (2026-08-13, trace 0d88ba79)

A chunk citing ONE file while naming other modules read in the SAME cycle was
still refuted: the union deliberately spans only the chunk's own citations.
Rule added at the same owner: a missing literal matching the SOURCE_ID of any
chain evidence is covered — its referent was actually opened this cycle.
Addresses only, never foreign excerpts' text: excerpt contents would launder
values back in (MIR-060). Remaining false-positive class stays R8b: excerpts
capped at creation carry no budget notice (`disqualifying_defect_signals,
mir-057, mir-060` all live in smart_memory.py yet sat past the cut).

## R8b closed: the creation-time cap had its own marker all along (2026-08-13, trace e488cd85)

Three straight self-inspection runs kept refuting TRUE claims (`mir-003`,
`disqualifying_defect_signals`…) whose literals live past the excerpt cut.
The cut at CREATION (`evidence.py:_truncate`, MAX_EXCERPT_CHARS=800) appends
`...[truncated]` — a third marker the R8 guard did not know. One line: the
absence gate now also stands aside for `...[truncated]` tails. Refuted counts
across the three runs: 7 → 5 → 3 → expected 0-1 of this family.

## R9 addendum: the boundary vocabulary learns the operator's fourth run (2026-08-13, trace 51596f82)

Refuted counts converged 7 → 5 → 3 → 0 across four identical self-inspection
runs — and the last false signal standing was R9 in new words: «дан с
пропущенными разделами (omitted) — часть логики не видна» flagged
self_contradicted×2 and quarantined the series' best answer. The boundary
vocabulary now knows «не видн», «не показан», «пропущенн», «не предоставл»,
«omitted», «not shown». The 2026-08-10 true-positive class stays caught.

## R4/R5: fabricated citations are terminal, and the resolver wakes up (2026-08-13, operator rulings)

R4 — a citation the evidence chain cannot resolve (fabricated ==
`cited_but_unmatched`, one definition in `evidence_support`) is now TERMINAL
at the acceptance boundary (`unsupported_claims`): the body is withheld and
replaced by an honest note, mode-independently — fabrication is a property of
the text, not a rollout heuristic. The live warrant: R-E1's «36 × 3 = 108»
with two [dialogue:previous] citations to a dialogue that never existed
shipped untouched. The outcome bridges to `citation_fabricated`
(loop_response_deciders) which joins DISQUALIFYING_DEFECT_SIGNALS.

R5 — `referent_resolver_mode` defaults to **on** (off/shadow stay operator
keys), and a fifth gate `_prior_step_gate` (its own gate, NOT an insertion
into the verbatim-pinned `_clarification_gate` body) asks before planning
when the turn references a previous step and the session has none.

Enabling the dormant organ immediately exposed why it likely stayed off:
a critique verb plus the TASK'S OWN CLAUSE resolved to `user_text`
(«проанализируй архитектуру проекта…» → target = its own object), and the
local-critique path ate a genuine tool-needing task — no role context, no
tools, no memory. `is_local_critique_eligible` now requires supplied-material
markers for `user_text` (newline, quotes, or a presenter colon that survives
directive stripping). Both directions pinned: a task clause is not supplied
text; «Вот фрагмент: …» still is.
