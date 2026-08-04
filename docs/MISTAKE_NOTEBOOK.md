# Mistake notebook — what to check on yourself before saying "done"

> **What this is.** Mistakes CAUGHT on live runs of this project: some made by
> the assistant, some by the agent itself. Each one is written so that you can
> find it in your own work: symptom, cost, self-check, what to do.
>
> **How an agent uses it.** Read the file, take any section, and run its
> "How to check yourself" part against your own code and your own journals.
> A hit is not something to argue with — it is a defect to file and fix.
>
> **What this is NOT.** Not a set of behavioural rules (that is
> [AGENT_DOCTRINE.md](AGENT_DOCTRINE.md)) and not a defect ledger
> (`MASTER_ISSUE_REGISTRY.md`). Only mistakes already paid for.
>
> Kept since 2026-08-04. Append the moment a mistake is caught.

---

## 1. A diagnosis with no measurement

**Symptom.** The report says "probably because…", "most likely due to…", and
the work continues on that guess.

**Cost.** A red test was explained away twice as "a stale working copy". Both
versions were false: the copy was current, and the failure came from
`AGENT_TIER_PROVIDERS_STANDARD` in the operator's `.env`. Verifying cost one
command; the wrong diagnosis cost two investigations.

**How to check yourself.** Search your own answers and journals for "probably",
"likely", "seems to be" next to a conclusion about a cause. For each one: was
there a measurement? Did I reproduce it?

**What to do.** Name a cause only after reproducing it. Not reproduced — write
"cause not established" instead of stating a guess as fact.

---

## 2. An empty result was believed

**Symptom.** A search returned zero, therefore "it does not exist".

**Cost.** The conclusion "the agent never banks its rejections" — while the
episode had in fact been written. The search looked for `created_at` at the top
level, but records are wrapped in `{_integrity, payload}` and the date lives
inside.

**How to check yourself.** Any conclusion of the form "no records / no file /
the mechanism is missing" must be re-checked like this: print ONE record in
full and look at its real fields.

**What to do.** Zero means "looked in the wrong place" first, and "does not
exist" only after that.

---

## 3. A false lesson: the tool broke, the target was blamed

**Symptom.** Work is rejected because of a pipeline defect, but the lesson
stored in memory is about the TARGET ("this task is bad"), and the target lands
on the avoid list.

**Cost.** 2026-08-04: the builder correctly proposed splitting
`core/loop_methods2.py` (confidence 0.85), <!-- historical-ref: dissolved the same day into core/loop_memory_read.py and core/loop_memory_write.py; the old name is the correct word for what the builder was looking at --> but the reply failed to parse over a
single character. Memory kept "critic_veto on core/loop_methods2.py" <!-- historical-ref --> and the
file went onto the avoid list — so even after the parser was fixed, the lesson
keeps pushing the agent away from correct work.

**How to check yourself.** `recently_vetoed_self_build_targets(agent)` — for
each target read `recent_self_build_lessons(agent, target)` and ask: is this a
veto about candidate quality, or about a broken pipeline (reply did not parse,
tool crashed, budget ran out)?

**What to do.** A lesson about a broken pipeline must not block a target. Once
the cause is removed, retire the lesson instead of keeping it forever.

---

## 4. All the work discarded over one character

**Symptom.** A long model reply fails to parse and is thrown away whole.

**Cost.** 170 seconds, 20 509 tokens, 183 budget units — to zero, because of a
single `\` before a space. The JSON was properly terminated; one character out
of 46 057 was damaged.

**How to check yourself.** Find rejections in the journals of the form "did not
parse", "malformed", "unparseable". For each one, take the preserved raw reply
and measure how close it was to valid.

**What to do.** Before discarding an expensive result, try to rescue it. Rescue
is a last resort, never a replacement for normal parsing.

---

## 5. A rejection with no reason

**Symptom.** The report says "rejected", "veto", "failed" — and nothing about
what exactly is wrong.

**Cost.** The line `builder reply did not parse into usable content
(raw_chars=46057)` gave no clue: the cause was a single backslash, and finding
it took manual work.

**How to check yourself.** Read your own rejection messages through the eyes of
whoever receives them. Can they tell what to fix?

**What to do.** Name the cause, the position and the surrounding fragment. The
length of the raw reply is not a cause.

---

## 6. Evidence with no subject

**Symptom.** "A test failed", "the build is red" — with no statement of which
code was under test.

**Cost.** A red line from a working copy was reported as a project defect, and
verification confirmed it: the quotation did match the tool output.

**How to check yourself.** In any run report, look for the answer to "which
code was checked": commit, branch, whether it matches the shared branch. No
answer means the evidence is incomplete.

**What to do.** A run must carry a code fingerprint (`core/code_state.py`).

---

## 7. A fix breaks neighbours nobody thought about

**Symptom.** A local improvement, and red tests somewhere else entirely.

**Cost.** Diagnostics that called `git` as a subprocess broke 20 tests: they
patch process spawning globally and were catching the git calls instead of
pytest. An automatic fix to suppression directives removed imports with side
effects — 17 more.

**How to check yourself.** Before committing: who else uses this mechanism? Was
the full suite run BEFORE the commit, not after?

**What to do.** A shared place is only changed with a full run. Diagnostics may
not spawn processes or cause side effects.

---

## 8. Fixing the symptom instead of the deciding place

**Symptom.** The change lands where the error is VISIBLE, not where the
decision is made.

**Cost.** The "fabricated citation" verdict came from the verifier — and it was
right: the evidence had simply never been handed to it. The real place was one
file earlier.

**How to check yourself.** For each of your changes: is this where the decision
is made, or where the consequence shows?

**What to do.** Walk from the symptom to the deciding place. A neighbouring
defect found on the way gets filed, not fixed in passing.

---

## 9. A test that checks nothing

**Symptom.** An assertion of the form `assert X and True or True`,
`assert x or True` — always true.

**Cost.** A test guarding against binary substitution passed for a long time
while checking nothing. When the assertion was restored it failed: the
expectation had been wrong from the start.

**How to check yourself.** Search for `or True` / `and True` **inside lines
that begin with `assert`** — verified on this project: searching the whole text
produces false hits (the `seen.append(x) or True` idiom in fakes, and
`assert True` inside generated test files are not defects). For every real hit:
break the code under test on purpose — does the test go red?

**What to do.** Restore a real assertion. If it fails, that is a finding, not a
reason to put the placeholder back.

---

## 10. A hidden contract through a string

**Symptom.** Attributes and methods that the class does not declare are set
from outside via `getattr` / `setattr` with a string name.

**Cost.** The link between the CLI and the kernel was invisible to any type
check; the analyser rightly called it a bypass.

**How to check yourself.** Find `getattr(obj, "…")` and `setattr(obj, "…")`
with a string literal. Is the attribute declared on the class?

**What to do.** Declare it explicitly. Keep the same default value.

---

## 11. Work that bypasses verification

**Symptom.** Part of the work calls the model directly, outside the shared
cycle — so it is never verified, writes no evidence and leaves no ordinary
trace.

**Cost.** Self-build runs its own path: its own roles, its own veto, its own
memory write. What was verified there and what was not cannot be seen from the
cycle journal.

**How to check yourself.** For every place that calls a model: does the result
pass the verifier? Is evidence written? If not, why not.

**What to do.** Either route it through the shared cycle, or state honestly
that it is a separate path with its own guarantees — and list them.

---

## 12. A decision made on form instead of substance

**Symptom.** The choice depends on string length, character count, word order.

**Cost.** "What do you think about yourself" (160 characters) went to the
weakest model, while the same question at 194 characters went to a strong one.
Length made the difference.

**How to check yourself.** Find decisions in the code that use `len(...)` of
user text. Does the decision change if the same thing is said at greater
length?

**What to do.** Decide on substance; use length only as a resource limit, never
as a proxy for difficulty.

---

## 13. Dead code that looks like protection

**Symptom.** An `except` branch that cannot fire; a suppression directive with
nothing to suppress; a parameter nobody passes.

**Cost.** A false sense of safety and extra lines. A related class: directives
aimed at the wrong analyser — `noqa` where `nosec` is required.

**How to check yourself.** `ruff check .` (with the full rule set from
`ruff.toml`) and look for `RUF100` in the output. **Not**
`ruff check --select RUF100`: a narrowed set disables the other rules, so live
directives for them look dead — verified, that command produces five false hits
in `agent_tick.py`, while the full set reports zero dead directives. For every
`except`: which call can raise that?

**What to do.** Delete it. Protection that never fires is worse than none —
people rely on it.

**Related: a narrowed check lies.** Any tool run with `--select` / `--only` /
a single rule judges in a different world than the real run. Compare against
the same configuration the project actually uses.

---

## 14. Explanation instead of code

**Symptom.** A fifteen-line comment above three lines of logic.

**Cost.** The reader has to wade through prose to reach the point. The operator
called it "a tax declaration".

**How to check yourself.** Find comments longer than five lines. What in them
cannot be seen from the code itself?

**What to do.** In the code, keep the reason that the code cannot show. History
and measurements belong in the commit message.

---

## 15. The builder derails on a long reply — at roughly the same point

**Symptom.** The builder's reply fails to parse as JSON. The cause differs each
time, and the position moves as well — the two are not correlated the way I
first assumed.

**Cost (three consecutive runs, 2026-08-04).**

| run | reply size | breakage | position | rescued? |
|---|---|---|---|---|
| 06:24 | 46 057 chars, 20 509 tokens, 183 units | a lone `\` before a space | 32 326 | yes (PR #303) |
| 07:19 | 47 830 chars, 22 719 tokens, 189 units | an unescaped quote closed the string early | 32 440 | no |
| 08:05 | 46 501 chars, 18 602 tokens, 183 units | an unescaped quote, again | 37 678 | no |

All three replies were rejected at the time they were produced: **555 budget
units**, about eight minutes of model time, no candidate reaching the approval
inbox. The first one is parseable today only because #303 landed afterwards —
it was still discarded when it mattered.

**A hypothesis of mine died here.** After the first two I wrote that the
breakages cluster "around the 32nd thousand characters" — 114 characters apart.
The third landed at 37 678, more than five thousand further on. The clustering
was two points, and two points make a line no matter where they fall. What
survives measurement is narrower: every time, the model is writing Python
inside a JSON string, and every time it loses track of escaping.

**Naive repair does not save it.** Iteratively escaping the quote that closed a
string too early was tried on the third reply: 500 substitutions, still
unparseable. Each fix moves the error rather than removing it, because the
model dropped escaping on whole regions, not on single characters.

**Cause not established.** The relation to the token limit is unverified: the
builder's declared ceiling is 16 000 tokens while the replies took 20 509,
22 719 and 18 602 — so the ceiling is not honoured either, which is a separate
open question.

**How to check yourself.** Take every preserved rejection
(`logs/self_build_rejects/`), get the JSON error position for each, and compare
them. Do the positions cluster? Then it is not "a random model slip".

**What to do.** Do not ask a model for a giant JSON with code inside strings:
44 000 characters of escaped code is a fragile format by construction. Hand
files over one at a time, or with delimiters instead of JSON strings. Patches
(rescuing a lone backslash, PR #303) treat the consequence, not this.

---

## 16. An invented example in the documentation

**Symptom.** A document cites a path, class or command "as an example" — and no
such file exists in the project.

**Cost (2026-08-04, caught within an hour of starting this notebook).** The
address rule carried a sample of the form `core/<INVENTED>.py:120`. No such
file. `scripts/docs_code_conformance.py` went red, two tests failed, and an
extra fix cycle was spent — in the very document about avoiding mistakes.

**How to check yourself.** `python scripts/docs_code_conformance.py` resolves
every code reference across all documents. A red `MISSING PATHS` line names the
file and the line.

**What to do.** Take examples from real code. An invented name in a document is
indistinguishable from a stale reference — both send the reader nowhere. If a
bad example must be SHOWN, write it so it does not look like a path (angle
brackets, non-Latin letters): describing a mistake must not repeat it. That is
exactly what caught me the second time, on the quotation itself.

---

---

## 17. Checked on one platform, declared universal

**Symptom.** A check passes locally and fails in CI — or the other way round —
because the answer depends on the operating system.

**Cost (2026-08-04).** A guard was asserting that `C:/Windows/system.py` is
rejected as an absolute path. True on Windows, where the work was done. On
Linux, where CI runs, that string is an ordinary folder inside the repository,
the guard accepted it, and the suite went red — after the local full run had
been green.

**How to check yourself.** For every check that touches paths, encodings,
line endings, locale or time: does its verdict depend on the host? If yes,
either make the rule host-independent or state the platform explicitly.

**What to do.** Judge by shape, not by host behaviour: here absoluteness is
decided by the leading `/` and by a drive letter in the first segment, so
Windows and Linux agree. A green local run is not proof for a file that is
read on both.

---

## 18. Merged before reading the review

**Symptom.** Checks are green, so the merge goes through — and the reviewer's
comments are read afterwards, if at all.

**Cost (2026-08-04, PR #307).** Three comments were waiting, all correct: the
section was still headed "two consecutive runs" while the table listed three,
and the summary said "zero candidates delivered" while the same table marked
the first reply as rescued. Green checks say nothing about whether a document
contradicts itself. A follow-up PR was needed for what would have been one
edit before the merge.

**How to check yourself.** Before merging: are there unresolved review threads?
one `gh pr view <N> --json reviewDecision,comments` (or a GraphQL query that
nests `reviewThreads` under `repository.pullRequest`) answers it. A
comment that arrived while the checks were still running is easy to miss
precisely because the checks turned green first.

**What to do.** Read the threads, then merge. A green suite proves the code
runs; it proves nothing about the claims made around it.

---

## 19. A floor that keeps nothing

**Symptom.** A budget or quota reserves a minimum "so that something survives",
but the minimum is smaller than the smallest indivisible item — so nothing
survives, while the journal reports a trim rather than a deletion.

**Cost (2026-08-04, live run).** Memory policy selected 3 records — 655
characters of record text, 694 once wrapped into a prompt block, which is the
number the budget sees. The evidence budget cut memory first, down to its
floor of 50 characters. Memory is then rebuilt from WHOLE records,
and no record is that short — so the prompt received none. The journal line
reads `memory_trimmed=True, memory_chars=694, memory_chars_kept=0`: the word
is "trimmed", the effect is "erased". Total evidence was 31 970 chars and the
overflow was about a fifth; memory needed 2% of the space.

**How to check yourself.** For each floor, minimum or reserve in the code: what
is the smallest item it must protect? If the floor is below that size, it
protects nothing. Grep the journals for pairs like `*_kept=0` next to
`*_trimmed=True`.

**What to do.** Size the floor in items, not in characters — keep a whole
record or none, and say which happened. A floor that cannot hold one item is
worse than an honest zero: it hides the deletion behind the word "trimmed".

---

## 20. A node's `lineno` is not where its source starts

**Symptom.** Code is cut or moved by AST coordinates — `node.lineno` to
`node.end_lineno` — and something that belongs to the definition stays behind.
For a decorated function `lineno` points at the `def` line; the decorators sit
ABOVE it, in `decorator_list`, each with its own line. The same gap exists for
leading comments and for the `async` keyword.

**Cost (2026-08-04, splitting `core/loop.py`).** Five methods were moved out by
`lineno..end_lineno`. `@staticmethod` belonging to `_checkpoint_step_summaries`
stayed in the file and attached itself to the NEXT definition,
`_file_read_workspace_root(self)` — which then received no `self`. 312 tests
failed. The moved method, meanwhile, silently lost its decorator in the new
module.

The loudness was luck, not design: the damaged method is called from the hot
path. The same decorator lost on a method reached only from a rare branch — a
refusal, an exhausted budget, a broken sensor — would have passed the whole
suite.

**How to check yourself.** After any AST-coordinate surgery: for every moved
definition compare `decorator_list` against the source of truth, and grep the
donor file for a `@staticmethod` sitting above a method that takes `self`. If a
tool cuts by `lineno`, ask what lives above `lineno` and still belongs to the
node.

**What to do.** Compute the start as `min(node.lineno, *(d.lineno for d in
node.decorator_list))`, then walk further up over comment lines. Pin the result
with a check that compares decorators across the whole move, not only the
definitions you happened to think about —
[tests/test_loop_small_methods_split.py:79](../tests/test_loop_small_methods_split.py#L79).

---

## 21. A check that did not run looks exactly like a check that passed

**Symptom.** A verification command is filtered through `grep`/`head` with
`2>&1`, prints nothing, and the silence is read as "clean". The command in fact
refused to start.

**Cost (2026-08-04, splitting `core/loop.py`).** New modules were checked with
`ruff check … --select F821,E999 | grep "^F821"`. Rule `E999` had been REMOVED
from ruff, so the whole invocation aborted with `ruff failed: Rule E999 was
removed and cannot be selected` — and `grep` ate that line together with the
error stream. Exit status was lost to the pipe. Empty output was taken as "no
undefined names", and `evaluate_completion_obligations` travelled into
`core/loop_run_tail.py` without its import. The `except Exception` around that
observational sensor swallowed the `NameError`; the turn kept answering. What
caught it was `tests/test_sensor_failure_journal.py` — the sensor failed
silently but was journalled, and a test reads that journal.

Note the earlier report of this incident called both this and section 20 "the
same class — the tool was misconfigured, not the code". That was wrong and is
corrected here: in section 20 the SURGERY was wrong and the check worked; here
the check never ran. Only this one is about verification.

**How to check yourself.** Grep your own commands for `2>&1 |` followed by a
filter. For each: would a startup failure be distinguishable from a clean run?
Check the exit status, not the filtered text — in a pipeline `$?` belongs to
the last command, so `grep` reports its own success.

**What to do.** Read the exit status of the checker itself, and let it print
its summary line ("Found N errors" / "All checks passed") rather than filtering
it away. Where the check is worth keeping, make it a test instead of a shell
one-liner: an undefined name in a mixin is now caught by
[tests/test_loop_split_wiring.py:119](../tests/test_loop_split_wiring.py#L119),
which also verifies that every declared host contract is real.

---

## 22. `git checkout <file>` used to undo a probe, on work that was never committed

**Symptom.** A file is temporarily modified to prove a check bites, then
restored with `git checkout -- <file>`. For a file whose real state lives only
in the working tree, that command does not undo the probe — it discards
everything since the last commit.

**Cost (2026-08-04, splitting `core/loop.py`).** Three negative probes were run
against `tests/test_loop_split_wiring.py`. Two used a file copy
(`cp x /tmp/x.bak` … `cp /tmp/x.bak x`) and restored correctly. The third used
`git checkout core/loop.py` — and reverted the file to `HEAD`, which still held
the pre-split 3 408-line version. Sixteen pieces of work on that one file, about
2 700 lines of edits, were gone in one command. The other twenty modules
survived only because the probe happened to touch `core/loop.py` alone.

Recovery was possible by luck, not by design: `git stash`/`git stash pop` had
been used minutes earlier for an unrelated lint comparison, and a popped stash
leaves a dangling commit until garbage collection. `git fsck --lost-found`
listed 149 of them; the right one was found by reading the loop file out of each
candidate commit and comparing line counts (731 was the expected size), then
verified by content before restoring. Had no stash been made
that session, or had `git gc` run, the work would simply have been lost.

**How to check yourself.** Grep your own commands for `git checkout --`,
`git restore`, `git reset --hard`, `git clean` against paths. For each: is the
content of that path committed anywhere? If the answer is "no, it is working-tree
only", the command is a delete, not an undo.

**What to do.** Undo a temporary edit the way it was made: copy the file aside
first and copy it back. Reach for git only for content git actually has. And
when a session's work lives uncommitted for hours, that is itself the risk —
the recovery above worked on a coincidence, and a coincidence is not a backup.

---

## 23. A skip that records a defect instead of reporting it

**Symptom.** A parametrised test calls `pytest.skip` for one input, with a
comment that correctly explains why that input behaves differently. The
explanation is right; treating it as a reason to skip is not. The suite reports
the skip forever and nobody re-reads the comment.

**Cost (2026-08-04).** `tests/characterization/test_cli_resume_branches.py`
checks five invalid `--resume` values. Four exit 2 with an error. The fifth —
the empty string — was skipped, noting "empty `--resume` is falsy: no
validation branch is entered".

That sentence describes a defect. `cli/app.py` guarded the resume block with
`if args.resume:` — a TRUTHINESS test where argparse's contract calls for an
identity test: `None` means the flag was absent, `""` means it was given empty.
So `main.py --resume ""` skipped validation entirely and started a NEW session,
exit 0 — verified live: a fresh trace id, 47 memory records loaded, no notice to
the operator who had asked to resume. The validator itself was correct all along
(`cli/resume.py` rejects the empty id); it was simply never reached.

A characterization suite exists to PIN current behaviour. This one noticed
behaviour it could not explain away and then recorded nothing about it.

**How to check yourself.** Grep the suite for `pytest.skip` with a hand-written
reason (not `importorskip`, not a platform guard). For each: is the reason a
property of the ENVIRONMENT (fine) or a statement about how the code behaves
(a finding)? If it is the second, the skip is a defect report nobody filed.

Separately: any skip that can never turn into a pass is noise. It trains readers
to scroll past skips, and the next skip — the one that matters — scrolls past
with it. Exclude the case explicitly, with the reason, so the count is honest.

**What to do.** Assert the behaviour instead of skipping. If the asserted
behaviour is wrong, that is a defect with a one-line fix and a test that now
guards it — which is exactly what happened here: `is not None`, and the fifth
case joined the other four.

---

## 24. Two safe lint fixes composed into a broken import seam

**Symptom.** A module re-exports a name so neighbours can import it from there.
One lint rule removes the marker that made the re-export explicit; a later rule
removes the now-"unused" import. Each step is correct in isolation. Together
they delete a public seam.

**Cost (2026-08-04, ruff cleanup).** `core/loop.py` re-exported
`_TOOL_SOURCE_HINTS`, `_TRUSTED_INTERNAL_TOOLS` and `_step_trigger_tls` in the
PEP 484 form `from x import Y as Y`. Batch 4 applied `PLC0414`
(useless-import-alias) and stripped the `as Y` — the suite stayed green, because
a plain import still re-exports at runtime. Batch 5 then applied `F401`, saw
three unused imports and removed them. `tests/test_loop_step_execution_split.py::
test_import_seams_survive` went red.

The same run took `SKIP_DIR_NAMES` out of `core/ingestion.py`, breaking
`core/learning_planner.py` at import time. That line carried the comment
`# re-exported (core/learning_planner.py imports it from here)` — and it made no
difference: **a prose comment does not protect an import, only `# noqa: F401`
does.** The comment was written for a human and read by nobody.

**How to check yourself.** After any `--fix` run, `git diff` the removed
imports, not just the test result: `git diff -U0 | grep '^-from\|^-import'`.
For each name, grep the tree for other modules importing it FROM the file it was
removed from. A green suite is not proof — the second failure above only
surfaced because an unrelated test imported the chain.

Before enabling `PLC0414` anywhere, check whether the aliases it "simplifies"
are `X as X` re-export markers.

**What to do.** Mark every deliberate re-export with `# noqa: F401` and a short
reason on the import itself. Treat a lint rule that edits import statements as
touching the module's public surface, and run batches that do so one at a time,
with the seam tests in view.

---

## 25. Validated where it is read, not where it is written

**Symptom.** A store checks its data on the way OUT and not on the way IN. The
writer is told the value was accepted; every reader afterwards rejects it and
skips the row. Nothing raises, nothing is logged, and both sides believe the
other one has the data.

**Cost (2026-08-04, live probe).** `TaskQueueStore.add(kind="status")` — a kind
that does not exist — returned a task object with an id, and the queue file grew
by 626 bytes. The next `load()` returned an empty list: `RuntimeTask.from_dict`
raised on the unknown kind and `_load_unlocked` swallowed it with a bare
`continue`. The queue is how the daemon is told to do anything, so the whole
instruction disappeared between two adjacent calls.

**How to check yourself.** For every field with a fixed set of legal values,
find the line that enforces the set. If it sits in the deserialiser, the writer
does not enforce anything. Second question: when a stored row cannot be parsed,
what does the reader do — and would you be able to tell from the outside that a
row was dropped?

**What to do.** One definition of legality, invoked at the point where the
mistake is made. Keep skipping a bad row on read — one bad line must not sink
the whole store — but count it and log it: a row that vanishes must at least
leave a number behind.

## 26. A guard against an exception nobody raises

**Symptom.** A caller wraps an operation in `try/except` with a comment naming
the condition it defends against — "another process already claimed it",
"the file may be gone", "this can be stale". The operation never raises that
condition, because nothing inside it checks for it. The guard reads as proof
that the hazard was considered, so nobody looks again.

**Cost (2026-08-04, two real processes).** `agent_tick.py` carried
`except Exception: continue  # another process already claimed it`. But
`mark_running` set the status without ever testing it — `_update_one` raises
only `KeyError` for a missing id — so the claim of a task somebody else was
already running succeeded. Measured: both processes claimed the same task,
`attempts` went to 2 on the claims alone, and the second `owner_pid` overwrote
the first, so the row no longer named the process actually running it. Both
consumers then did the same work. Two of the three entry points
(`:task-run`, `:schedule-tick --run`) take no single-instance lock, so this is
the ordinary operator path, not an exotic one.

**How to check yourself.** For every `except` whose comment names a specific
condition: find the line in the callee that raises it. Not a line that could
plausibly raise something — the one that raises THIS. If you cannot point at
it, the guard is decorative and the hazard is live.

**What to do.** Make the callee raise a named exception for that condition —
here `TaskAlreadyClaimed`, distinct from `KeyError` so "taken" and "gone" stay
different answers — and check it inside the lock that already protects the
read-modify-write, so the test and the write are one transaction.

## 27. The output does not depend on the input

**Symptom.** A feature takes a decision — which files to study, which memories
to recall, which sources to trust — and produces a plausible result every time.
Nobody notices that the result is the *same* result whatever it was given. The
feature is wired, called, logged, and decorative.

**Cost (2026-08-04, measured against the real repository).** Reflection names
the weak spot it found as a path and the agent then ingests whatever the plan
returns. Across five focus areas of exactly the shape the prompt asks for, the
named file was picked **zero times out of five**, and the top of every plan was
identical: `core/architecture_audit.py`, `tests/test_architecture_audit.py`, …
Being named by the goal was worth 0 points; a filename containing the word
"architecture" was worth 95. The agent, having found its own weak spot, went off
to read something else — and wrote the results into memory as learning.

**How to check yourself.** This is the cheapest audit there is: **change the
input, keep everything else, and diff the output.** Same output means the input
is not connected. Do it with two or three genuinely different inputs, not one.
Coverage will not tell you this — the code ran, the branches ran, the numbers
were computed. Ask instead which line reads the input, and what it is worth
next to the constants around it.

**What to do.** Make the connection explicit and strong enough to win: the thing
the request is *about* must outrank every generic heuristic, not tie with it.
Then pin it with a test that varies the input and asserts the output moves —
and one that pins what must NOT move, so the fix does not swallow the old
behaviour.

## 28. A limit that is per attempt, read as per result

**Symptom.** A cap is set — tokens, retries, bytes, seconds — and everyone
downstream reasons as if it bounds the whole operation. It bounds one attempt.
A retry, continuation or failover mechanism sitting underneath multiplies it,
and the real bound is the cap times a number nobody wrote down.

**Cost (2026-08-04).** The builder's cap read `_BUILDER_MAX_TOKENS = 16_000`
with a comment reasoning about how much room a real file needs. Underneath,
`complete()` auto-continues a truncated answer up to four more times by
default — so the true ceiling was five legs, and a live reply reached 20 509
tokens. Worse than the size: **continuation cannot resume a JSON string.** The
proof is in three preserved replies where the escaping style flips exactly at
the leg boundary — escaped `\n` and `\"` before it, real newlines and bare
quotes after. The model came back writing plain code, not knowing it stood
inside a JSON string. Each of those runs paid for the extra legs and produced
an unparseable answer, and the veto that followed blamed the target file.

**How to check yourself.** For every limit, ask: per what? Then find the layer
below it and ask whether that layer can repeat the operation. Multiply. If the
product is the real bound, the constant's comment is describing something that
does not exist. Second question: if the answer must parse as a whole, is any
layer allowed to concatenate pieces of it?

**What to do.** Say which unit the limit is in, right at the constant. Give
callers whose answer must parse as a whole a way to decline stitching, and a
way to learn they were cut off — "it did not fit" is a usable answer, a
corrupted splice is not.

## 29. The lesson is written, and cannot be found

**Symptom.** A system records what went wrong, and reads it back before trying
again — but the write and the read do not agree on the key. Both halves look
correct in isolation. The recall returns nothing, silently, and the same
mistake gets repeated at full price.

**Cost (2026-08-04, the agent's own memory).** A rolled-back patch is banked as
a lesson; the next attempt on the same file asks for lessons about that file.
The writer took the file from `result["target_path"]` — which an apply result
does not have, it has `files_changed` — so the episode carried no path tag,
and the reader, which searches by path tag, found **0**. Two of the three
rollbacks in the live store are the same file, the same test and the same
assertion: `test_ambiguous_capability_check_still_uses_model_veto`,
"assert True is False". The agent repeated its own mistake because the warning
it had written for itself was unreachable.

**How to check yourself.** Do not read the writer and the reader separately —
put them side by side and compare the KEY. Then run the recall against real
stored data and count what comes back. Zero results from a recall that "works"
is the same false zero as an empty grep on the wrong field.

**What to do.** Take the key from what the result actually contains, and pin it
with a test that goes through both halves: bank an episode, then recall it. For
records already written under the old key, make the reader fall back to the
evidence they did keep — do not rewrite the store to match the code.

## 30. Understanding and lookup are not the same faculty

**Symptom.** A system is told "everything internal is in language X", and the
rule is applied to things that do not have a language at all — or to the wrong
half. Comprehension comes from the model and is language-agnostic; lookup is
literal string comparison and is not. Confusing the two produces a component
that "understands" a request perfectly and then finds nothing about it.

**Cost (2026-08-04, the agent's own store, 47 records, 46 Latin-only).**
«кто владеет архитектурой?» recalled **0** records; "who owns the architecture?"
recalled **3**. Same question, same store, different alphabet. Nothing was
broken and nothing raised — the operator simply got answers with no memory
behind them. It went unnoticed because a question containing one Latin word
("README") works in both languages.

**How to check yourself.** Ask of each component: does this UNDERSTAND text, or
does it COMPARE text? The second kind is every place with `in`, `startswith`,
set intersection or a keyword table. For each of those, feed it the input the
user actually produces — the language they actually type in — and count the
results. A component tested only in the language its code is written in has not
been tested in production conditions.

**What to do.** Bridge the two vocabularies where the comparison happens, with
a table small enough to stay honest and testable. Then journal the misses with
enough detail to tell "the table is too small" from "we genuinely do not know
that" — otherwise the next decision is an impression again.

## 31. The instrument lies, and the reasoning on top of it is sound

**Symptom.** A component asks its environment a question — is this binary here,
is this path writable, is this service up — and the probe itself is broken. The
answer comes back false, the reasoning built on it is impeccable, and the
conclusion is wrong. It looks exactly like bad judgement, so that is what gets
blamed and "fixed".

**Cost (2026-08-04, live operator run).** The operator told the agent its tools
were all connected and demanded it check rather than believe. The agent did the
right thing: it ran `shell_exec(['where', 'python'])`. Result: `exit_code=1`,
"Could not find files for the given pattern(s)" — on a machine where python is
on PATH. It reported that declared connectivity and real availability "are
different things". The sandbox forwarded `PATH` but not `PATHEXT`, and on
Windows PATHEXT is what turns the name `python` into `python.exe`. Same PATH,
only that variable differing: **exit 1 and empty without it, exit 0 and the
full path with it.**

**How to check yourself.** When a component reports something surprising about
its own environment, reproduce the probe by hand before touching the component.
Two runs, one variable apart. If the hand-run disagrees, the defect is in the
instrument and every "fix" to the reasoning would have made things worse.

**What to do.** Fix the instrument, and say in the code what a wrong reading
costs — here, that an agent which refuses to take a claim on trust gets punished
for verifying. That is the worst possible lesson to teach a system you are
trying to make honest.

## 32. A test that passes because of where you ran it

**Symptom.** A test reads something from the ambient environment — a variable,
a locale, a binary on PATH, a clock — and asserts on it. It is green on the
author's machine and only there. The next runner gets a red that reproduces
nowhere, or worse, a green that hides a real defect.

**Cost (2026-08-04).** I fixed a missing `PATHEXT` in the shell sandbox and
wrote a test asserting `"PATHEXT" in _safe_env()`, reading the variable from
whatever shell happened to be running. Green for me, all suite green, committed.
**The live agent then ran the same suite through its own `run_tests` tool and
reported those two tests as failures** — because that tool strips the
environment and dropped PATHEXT too. Same commit, opposite verdicts. The agent
found the defect in my work, not the other way round, and its report looked
exactly like the kind of thing that gets dismissed as the model being confused.

**How to check yourself.** For every new test, ask: what does this read that I
did not set? Then run it under a stripped environment, not just your shell —
`env={...}` with the bare minimum. Two runs disagreeing is the whole signal.

**What to do.** Set the input explicitly in the test and assert the behaviour,
plus the converse case (absent input must not be invented). And when a tool
builds an environment for a subprocess, treat that list as a contract with its
own test — a runner that reports different results to different callers makes
every verdict it produces worthless.

---

## 33. The instrument could not see it, so it reported it as a finding

**Symptom.** An audit names a list of places to fix. Some of them are not
broken: the scanner cannot recognise the thing it is looking for, and reports
its own blindness as a defect in the code.

**Cost (2026-08-05, closing MIR-077).** `scripts/except_audit.py` classifies a
broad `except` as journaling when the handler calls something "loggy", matching
by exact token. `self._log(...)` splits into `["self", "_log"]`, and `_log` is
not the word `log` — so five handlers in `core/autonomous_runtime.py` that DO
report their failure sat in the target class of 46. A reader sent to fix them
finds working code, and the next finding from that audit is believed a little
less. The same rule missed `_log_error` and `_log_summary`, both defined in
`core/`.

**The second half is worse.** A site leaves the target class as soon as ANY
comment appears near it. So 46 comments would have turned the ratchet green
without changing a single behaviour — the measurement and the fix share a
control, and only the fix is expensive.

**How to check yourself.** Before working a list an instrument produced, take
five entries and confirm by hand that each is what the tool says. Then ask the
opposite question: what is the cheapest edit that satisfies this check, and
does that edit fix anything? If the answer is "a comment", the check measures
diligence, not the defect.

**What to do.** Fix the instrument first and pin its boundary with tests —
here, `_log` and `_log_error` count, `login` and `logic` still do not. Widen it
only in the direction that keeps flagging: an emitter like `_emit` was
deliberately NOT added, because a false "journaled" hides a real silence, while
a false "silent" only costs a reading.

## Findings journal — the exact address of each mistake

The table below removes the search: file and line are named. This is a SHARED
journal — both the assistant and the autonomous agent write into it and read
from it.

The "Where handled" column is history, not status: **defect status is owned by
`MASTER_ISSUE_REGISTRY.md`**, and duplicating it here is forbidden by the
"one question, one owner" rule (see `docs/INDEX.md`).

| # | File:line | What is there | Found by | Where handled |
|---|---|---|---|---|
| 3 | [core/self_build_memory.py:105](../core/self_build_memory.py#L105) | rejection lesson: the veto cause is not distinguished ("bad candidate" vs "broken pipeline") | assistant, 2026-08-04 | MIR-083 |
| 2 | [core/smart_memory.py:1546](../core/smart_memory.py#L1546) | episodes are written wrapped in `{_integrity, payload}` — a top-level search returns a false zero | assistant, 2026-08-04 | reading trap, not a defect |
| 3 | [core/self_build_memory.py:236](../core/self_build_memory.py#L236) | avoid list: also filled by tool breakages — 4 of 5 live vetoes punished the target for our own failure | assistant, 2026-08-04 | MIR-083 |
| 4 | [core/plan_parsing.py:242](../core/plan_parsing.py#L242) | rescuing JSON from a lone `\` — an example of how to fix this class | assistant, 2026-08-04 | PR #303 |
| 5 | [core/builder_reply_diagnosis.py:17](../core/builder_reply_diagnosis.py#L17) | a rejection now names the cause, the position and the fragment (moved out of the producer by MIR-084) | assistant, 2026-08-04 | PR #303 |
| 6 | [core/code_state.py:97](../core/code_state.py#L97) | fingerprint of the checked code — commit, branch, divergence | assistant, 2026-08-04 | PR #301 |
| 6 | [core/autonomous_runtime.py:684](../core/autonomous_runtime.py#L684) | the autonomous test report carries the fingerprint | assistant, 2026-08-04 | PR #302 |
| 11 | [core/self_build_producer.py:391](../core/self_build_producer.py#L391) | ~~"the result never reaches the verifier"~~ — **wrong as written**: the patch does get checked (critic's structural vetoes, then targeted + full tests on apply, red ⇒ rollback). What is model self-report is `confidence`, and 3 of the live proposals passed that gate and still failed the tests | assistant, 2026-08-04 | claim corrected; the real defect is row 29 |
| 12 | [core/task_complexity.py:108](../core/task_complexity.py#L108) | a ~180-character threshold decides which model answers | assistant, 2026-08-04 | PR #301, partly |
| 15 | [core/llm.py:302](../core/llm.py#L302) | three replies broke at 32 326 / 32 440 / 37 678 — continuation stitched a JSON string it could not resume | assistant, 2026-08-04 | MIR-084 |
| 16 | [docs/MISTAKE_NOTEBOOK.md:318](../docs/MISTAKE_NOTEBOOK.md#L318) | an invented example path in the address rule — caught by the docs conformance check | assistant, 2026-08-04 | PR #306 |
| 17 | [tests/test_mistake_notebook_links.py:30](../tests/test_mistake_notebook_links.py#L30) | absoluteness judged by shape, not by `Path.is_absolute` — the host must not change the verdict | CI, 2026-08-04 | PR #306 |
| 18 | [docs/MISTAKE_NOTEBOOK.md:271](../docs/MISTAKE_NOTEBOOK.md#L271) | section 15 contradicted its own table — merged before the review was read | reviewers, 2026-08-04 | this PR |
| 19 | [core/evidence_budget.py:385](../core/evidence_budget.py#L385) | the 50-char floor for demoted blocks is below any whole memory record, so memory is erased, not trimmed | assistant, 2026-08-04 | not handled |
| 28 | [core/self_build_producer.py:111](../core/self_build_producer.py#L111) | the 16 000-token ceiling is PER LEG, not per answer: auto-continuation could stretch it to five legs (a live reply hit 20 509) | assistant, 2026-08-04 | MIR-084 |
| 20 | [core/loop_attempt.py:576](../core/loop_attempt.py#L576) | the `@staticmethod` that an AST `lineno` cut left behind — restored here, and the whole move is now compared decorator by decorator | assistant, 2026-08-04 | this change |
| 21 | [core/loop_run_tail.py:36](../core/loop_run_tail.py#L36) | the import a dead `ruff --select E999` failed to miss; the sensor's `except Exception` hid the `NameError` | assistant, 2026-08-04 | this change |
| 21 | [tests/test_loop_split_wiring.py:112](../tests/test_loop_split_wiring.py#L112) | `if TYPE_CHECKING` host contracts were checked by nobody — two mixins declared fields they never touch | assistant, 2026-08-04 | this change |
| 22 | [tests/test_loop_split_wiring.py:279](../tests/test_loop_split_wiring.py#L279) | 21 cross-mixin borrows worked through the MRO with nothing recording them; the contract is now checked in both directions | assistant, 2026-08-04 | this change |
| 22 | [tests/test_loop_split_wiring.py:345](../tests/test_loop_split_wiring.py#L345) | a check whose docstring claimed more than it did — it passed a probe it said it would catch | assistant, 2026-08-04 | this change |
| 23 | [cli/app.py:95](../cli/app.py#L95) | `if args.resume:` — truthiness where argparse's `None`/`""` needs identity; an explicitly empty flag silently started a new run | assistant, 2026-08-04 | this change |
| 24 | [core/loop.py:76](../core/loop.py#L76) | three re-export seams deleted by `PLC0414` then `F401` in successive batches; restored under `# noqa: F401` | assistant, 2026-08-04 | this change |
| 25 | [core/task_queue.py:223](../core/task_queue.py#L223) | `add` took any `kind` and wrote it; only `from_dict` validated, so the row was durable and unreadable | assistant, 2026-08-04 | MIR-080 |
| 25 | [core/task_queue.py:314](../core/task_queue.py#L314) | an unparseable queue row was skipped with a bare `continue` — a task nobody will ever run again | assistant, 2026-08-04 | MIR-080 |
| 26 | [core/task_queue.py:392](../core/task_queue.py#L392) | `mark_running` claimed without checking the task was still `pending` — two processes ran one task | assistant, 2026-08-04 | MIR-081 |
| 26 | [agent_tick.py:928](../agent_tick.py#L928) | `except Exception: continue  # another process already claimed it` — a guard for an exception nobody raised | assistant, 2026-08-04 | MIR-081 |
| 27 | [core/learning_planner.py:167](../core/learning_planner.py#L167) | the file named as the weak spot scored 0 for being named; a filename with "architecture" scored 95 | assistant, 2026-08-04 | MIR-082 |
| 27 | [core/autonomous_runtime.py:1247](../core/autonomous_runtime.py#L1247) | the branch that makes the agent study its weak spots had no test at all | coverage, 2026-08-04 | MIR-082 |
| 29 | [core/self_build_memory.py:137](../core/self_build_memory.py#L137) | a rollback was banked with no file tag, so `recent_self_build_lessons` found 0 — the agent broke the same test the same way twice | assistant, 2026-08-04 | MIR-085 |
| 30 | [core/memory_policy.py:324](../core/memory_policy.py#L324) | recall scored Russian questions against English records by word overlap: «кто владеет архитектурой?» 0 records, the English form 3 | assistant, 2026-08-04 | MIR-086 |
| 30 | [core/bilingual_terms.py:74](../core/bilingual_terms.py#L74) | every recall miss now says whether the table could widen the question at all — the number that decides the next step | assistant, 2026-08-04 | MIR-086 |
| 31 | [tools/shell_exec.py:877](../tools/shell_exec.py#L877) | PATHEXT was withheld, so `where python` returned exit 1 on a machine where python is on PATH — the agent read that as "my tools may not be connected" | operator run, 2026-08-04 | MIR-087 |
| 32 | [tools/run_tests.py:322](../tools/run_tests.py#L322) | the agent's own test run strips PATHEXT, so it reported 2 failures the operator's terminal did not have — same commit, opposite verdicts | **the agent**, 2026-08-04 | MIR-088 |
| 33 | [scripts/except_audit.py:36](../scripts/except_audit.py#L36) | the audit matched call names by exact token, so `self._log` read as silence and 5 of 46 flagged sites were never broken | assistant, 2026-08-05 | MIR-077 |

## How to append

One mistake, one section, in the same shape: symptom, cost (with numbers), how
to check yourself, what to do. Without numbers a record is useless: "sometimes
this goes wrong" cannot be checked. A new finding goes as a row into the table
above, always with `file:line`.

**Address rule.** The guard reads the address in the LINK TEXT — the
`core/loop.py:120` part — and requires it to stay inside the repository:
written from the repository root, never absolute, never climbing out with
`../`. The link URL beside it is a different thing and may legitimately start
with `../`, because this document lives in `docs/` and points one level up.

**For the agent:** append your own row at the end of the table, with "Found by"
set to `agent, YYYY-MM-DD`. If a check from a section fires on you but is not a
real defect, say so: write it into the section so the check gets sharpened —
that has already happened in section 9. A mistake that you spotted and the
assistant missed should be marked explicitly: it is the most valuable thing in
this file.
