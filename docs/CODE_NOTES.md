# Code notes — what was done to these files and why

> **Оператору, по-русски — прочтите эти пять строк, дальше можно не читать.**
> Этот файл — память агента, а не документация программы. Нужен он одному
> читателю: агенту в следующей сессии, который откроет код, не помня прошлого
> разговора. **Программа от его удаления не сломается**: ни один тест, ни одна
> проверка на него не завязаны — потеряется только время на повторное
> разбирательство. Заведён 2026-08-06 по вашей просьбе, взамен длинных
> пояснений внутри файлов кода. Удалять можно молча.

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
- `run_repl` is still 126 lines with three input modes inline. Next:
  `_collect_continuation`, then `_collect_operator_task_block`.

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
