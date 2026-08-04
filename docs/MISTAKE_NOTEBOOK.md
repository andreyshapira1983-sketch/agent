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
`core/loop_methods2.py` (confidence 0.85), but the reply failed to parse over a
single character. Memory kept "critic_veto on core/loop_methods2.py" and the
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
time; the position barely does.

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
`gh api graphql` on `reviewThreads { isResolved }` answers in one call. A
comment that arrived while the checks were still running is easy to miss
precisely because the checks turned green first.

**What to do.** Read the threads, then merge. A green suite proves the code
runs; it proves nothing about the claims made around it.

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
| 16 | [docs/MISTAKE_NOTEBOOK.md:1](../docs/MISTAKE_NOTEBOOK.md#L1) | an invented example path in the address rule — caught by the docs conformance check | assistant, 2026-08-04 | PR #306 |
| 17 | [tests/test_mistake_notebook_links.py:30](../tests/test_mistake_notebook_links.py#L30) | absoluteness judged by shape, not by `Path.is_absolute` — the host must not change the verdict | CI, 2026-08-04 | PR #306 |
| 18 | [docs/MISTAKE_NOTEBOOK.md:1](../docs/MISTAKE_NOTEBOOK.md#L1) | section 15 contradicted its own table — merged before the review was read | reviewers, 2026-08-04 | this PR |
| — | [core/self_build_producer.py:110](../core/self_build_producer.py#L110) | the builder's ceiling is 16 000 tokens, yet a live reply took 20 509 — the limit is not honoured | assistant, 2026-08-04 | not investigated |

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
