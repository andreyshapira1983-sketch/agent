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

## Findings journal — the exact address of each mistake

The table below removes the search: file and line are named. This is a SHARED
journal — both the assistant and the autonomous agent write into it and read
from it.

The "Where handled" column is history, not status: **defect status is owned by
`MASTER_ISSUE_REGISTRY.md`**, and duplicating it here is forbidden by the
"one question, one owner" rule (see `docs/INDEX.md`).

| # | File:line | What is there | Found by | Where handled |
|---|---|---|---|---|
| 3 | [core/self_build_memory.py:119](../core/self_build_memory.py#L119) | rejection lesson: the veto cause is not distinguished ("bad candidate" vs "broken pipeline") | assistant, 2026-08-04 | not handled |
| 2 | [core/smart_memory.py:1546](../core/smart_memory.py#L1546) | episodes are written wrapped in `{_integrity, payload}` — a top-level search returns a false zero | assistant, 2026-08-04 | reading trap, not a defect |
| 3 | [core/self_build_memory.py:165](../core/self_build_memory.py#L165) | avoid list: also filled by tool breakages | assistant, 2026-08-04 | not handled |
| 4 | [core/plan_parsing.py:242](../core/plan_parsing.py#L242) | rescuing JSON from a lone `\` — an example of how to fix this class | assistant, 2026-08-04 | PR #303 |
| 5 | [core/self_build_producer.py:320](../core/self_build_producer.py#L320) | a rejection now names the cause, the position and the fragment | assistant, 2026-08-04 | PR #303 |
| 6 | [core/code_state.py:97](../core/code_state.py#L97) | fingerprint of the checked code — commit, branch, divergence | assistant, 2026-08-04 | PR #301 |
| 6 | [core/autonomous_runtime.py:684](../core/autonomous_runtime.py#L684) | the autonomous test report carries the fingerprint | assistant, 2026-08-04 | PR #302 |
| 11 | [core/self_build_producer.py:409](../core/self_build_producer.py#L409) | the builder calls the model directly: the result never reaches the verifier | assistant, 2026-08-04 | not handled |
| 12 | [core/task_complexity.py:108](../core/task_complexity.py#L108) | a ~180-character threshold decides which model answers | assistant, 2026-08-04 | PR #301, partly |
| 15 | [core/self_build_producer.py:110](../core/self_build_producer.py#L110) | two consecutive replies broke at 32 326 and 32 440 — "code inside JSON" is a fragile format | assistant, 2026-08-04 | not handled |
| 16 | [docs/MISTAKE_NOTEBOOK.md:318](../docs/MISTAKE_NOTEBOOK.md#L318) | an invented example path in the address rule — caught by the docs conformance check | assistant, 2026-08-04 | PR #306 |
| 17 | [tests/test_mistake_notebook_links.py:30](../tests/test_mistake_notebook_links.py#L30) | absoluteness judged by shape, not by `Path.is_absolute` — the host must not change the verdict | CI, 2026-08-04 | PR #306 |
| 18 | [docs/MISTAKE_NOTEBOOK.md:271](../docs/MISTAKE_NOTEBOOK.md#L271) | section 15 contradicted its own table — merged before the review was read | reviewers, 2026-08-04 | this PR |
| 19 | [core/evidence_budget.py:304](../core/evidence_budget.py#L304) | the 50-char floor for demoted blocks is below any whole memory record, so memory is erased, not trimmed | assistant, 2026-08-04 | not handled |
| — | [core/self_build_producer.py:110](../core/self_build_producer.py#L110) | the builder's ceiling is 16 000 tokens, yet a live reply took 20 509 — the limit is not honoured | assistant, 2026-08-04 | not investigated |
| 20 | [core/loop_attempt.py:576](../core/loop_attempt.py#L576) | the `@staticmethod` that an AST `lineno` cut left behind — restored here, and the whole move is now compared decorator by decorator | assistant, 2026-08-04 | this change |
| 21 | [core/loop_run_tail.py:36](../core/loop_run_tail.py#L36) | the import a dead `ruff --select E999` failed to miss; the sensor's `except Exception` hid the `NameError` | assistant, 2026-08-04 | this change |
| 21 | [tests/test_loop_split_wiring.py:112](../tests/test_loop_split_wiring.py#L112) | `if TYPE_CHECKING` host contracts were checked by nobody — two mixins declared fields they never touch | assistant, 2026-08-04 | this change |
| 22 | [tests/test_loop_split_wiring.py:279](../tests/test_loop_split_wiring.py#L279) | 21 cross-mixin borrows worked through the MRO with nothing recording them; the contract is now checked in both directions | assistant, 2026-08-04 | this change |
| 22 | [tests/test_loop_split_wiring.py:345](../tests/test_loop_split_wiring.py#L345) | a check whose docstring claimed more than it did — it passed a probe it said it would catch | assistant, 2026-08-04 | this change |
| 23 | [cli/app.py:95](../cli/app.py#L95) | `if args.resume:` — truthiness where argparse's `None`/`""` needs identity; an explicitly empty flag silently started a new run | assistant, 2026-08-04 | this change |
| 24 | [core/loop.py:76](../core/loop.py#L76) | three re-export seams deleted by `PLC0414` then `F401` in successive batches; restored under `# noqa: F401` | assistant, 2026-08-04 | this change |
| 25 | [core/task_queue.py:223](../core/task_queue.py#L223) | `add` took any `kind` and wrote it; only `from_dict` validated, so the row was durable and unreadable | assistant, 2026-08-04 | MIR-080 |
| 25 | [core/task_queue.py:308](../core/task_queue.py#L308) | an unparseable queue row was skipped with a bare `continue` — a task nobody will ever run again | assistant, 2026-08-04 | MIR-080 |
| 26 | [core/task_queue.py:386](../core/task_queue.py#L386) | `mark_running` claimed without checking the task was still `pending` — two processes ran one task | assistant, 2026-08-04 | MIR-081 |
| 26 | [agent_tick.py:928](../agent_tick.py#L928) | `except Exception: continue  # another process already claimed it` — a guard for an exception nobody raised | assistant, 2026-08-04 | MIR-081 |
| 27 | [core/learning_planner.py:142](../core/learning_planner.py#L142) | the file named as the weak spot scored 0 for being named; a filename with "architecture" scored 95 | assistant, 2026-08-04 | MIR-082 |
| 27 | [core/autonomous_runtime.py:1247](../core/autonomous_runtime.py#L1247) | the branch that makes the agent study its weak spots had no test at all | coverage, 2026-08-04 | MIR-082 |

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
