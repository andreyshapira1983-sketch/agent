# CLI Baseline — recorded before the `main.py` extraction

> **What this file is:** the observed behavior of `python main.py` at commit
> **`9daa9bf`** (`9daa9bfc79e8922f7320c478597abf352c7649a3`), recorded as the
> reference point for the incremental extraction of `main.py` into a thin CLI
> launcher. Every claim below is backed by a `file:line` anchor and by a test in
> `tests/characterization/`.
>
> **What this file is NOT:** a design document. Sections 2 and 3 describe what the
> code *does today*, including things that are probably wrong. Recording a
> behavior here freezes it against *accidental* change during extraction; it does
> not endorse it and does not make it permanent. Deliberate changes belong to a
> separate, explicitly-approved behavior-change phase.
>
> **Precedence:** current code wins. If the code and this file disagree, the code
> is right and this file must be corrected.

Suite: `python -m pytest tests/characterization -q` → **142 passed, 1 skipped**
at `9daa9bf`. The skip is documented in §4.

---

## 1. Verified public/behavioral contracts

These are the operator- and script-facing guarantees. Extraction must reproduce
them exactly.

### 1.1 Argument surface

Seven public flags plus argparse's `-h/--help` (`main.py:1859-1919`):

| Flag | Default | Notes |
|---|---|---|
| `--ask ASK` | `None` | Present → one-shot; absent → interactive REPL. |
| `--file FILE` | `None` | File hint offered to the planner. |
| `--workspace WORKSPACE` | `"."` | Resolved via `Path(...).resolve()` (`main.py:1924`). |
| `--auto-approve {off,approve,deny}` | `off` | Choices enforced by argparse. |
| `--resume TRACE_ID` | `None` | Metavar is `TRACE_ID`, not `RESUME`. |
| `--reason REASON` | `None` | Deep-escalation reason (one-shot only). |
| `--expect EXPECT` | `None` | Expected deep output, used with `--reason`. |

Usage errors (unknown flag, missing value, invalid choice) exit **2** via
argparse. Tests: `test_cli_argparse_surface.py`.

### 1.2 Exit codes

| Code | Condition | Anchor |
|---|---|---|
| `0` | successful one-shot, cached-answer replay, REPL EOF/`Ctrl+C`, unknown command | `main.py:2029`, `1980`, `2105`, `2244` |
| `2` | `--resume` trace ID fails the allowlist | `main.py:1945-1951` |
| `2` | `--file` hint does not exist | `main.py:1994-1997` |
| `0` | `:quit` / `:exit` — raised as `SystemExit(0)` from the dispatcher | `main.py:1849-1850` |

`main()` returns the integer; `raise SystemExit(main())` (`main.py:2247-2248`)
turns it into the process code. Proved in-process **and** in a real subprocess by
`test_cli_resume_branches.py::test_invalid_trace_id_exit_code_is_two_in_a_real_process`.

### 1.3 Stream placement

**stdout carries answers only.** Answers use the wrapper
`print("\n" + format_human_response(answer) + "\n")`, so captured stdout is
`"\n" + rendered + "\n\n"` (`main.py:1979`, `2056`, `2216`, `2244`).

Everything else goes to **stderr**: the readiness banner, `[resume]` notices,
`:help`, `(unknown command: …)`, rate-limit warnings, block-mode notices, and the
approval prompt (`main.py:2072`). The REPL's own `"> "` / `"... "` prompts are
written by `_StdinLineReader` to its `out`, which defaults to **stdout**
(`main.py:1243`, `1278-1288`). Tests: `test_cli_stream_placement.py`.

### 1.4 One-shot memory and approval policy

One-shot is memory-free: `build_agent(workspace, with_memory=False,
with_persistent=False, …)` (`main.py:2015-2020`) — the docstring's "no memory,
fresh session" promise covers persistent memory too.

Approval policy from `--auto-approve` (`main.py:2002-2009`): `approve` /
`deny` → `AutoApprover` with that default; `off` → **`None`**, meaning no provider
is wired and escalated tools are blocked. Tests: `test_cli_one_shot_policy.py`.

### 1.5 Resume semantics

Trace IDs must match `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$` (`main.py:1944`), which
mirrors `CheckpointWriter._SAFE_TRACE_ID` (`core/checkpoint.py:117`) and blocks
path traversal. Branch order as implemented (`main.py:1938-1992`):

1. invalid trace ID → stderr message, **exit 2**;
2. loader returns `None` → "Running fresh.", normal run;
3. `last_phase == paused` **and** `paused` data → restore question via
   `_resume_question_from_checkpoint` + file hint, then run;
4. `answer is not None` → replay cached answer to stdout, **return 0, no agent
   built**;
5. otherwise → "synthesis incomplete", restore question, re-run from scratch.

Step 3 is tested *before* step 4, so a paused last phase wins over a cached
answer. Explicit `--ask` / `--file` always win over restored values. Tests:
`test_cli_resume_branches.py`.

### 1.6 Explicit-command precedence

In both modes a message whose first token starts with `:` (or is exactly `?`) is
dispatched as an explicit command and is never offered to the keyword/intent
router — one-shot at `main.py:2024-2029`, REPL at `main.py:2218-2226`. Leading
whitespace is stripped first (`lstrip()` / `strip()`). Tests:
`test_cli_command_precedence.py`.

### 1.7 REPL input behavior

- One background reader owns stdin; the top-level prompt, both block modes and
  the approval prompt all pull from it (`main.py:2064`, `2071-2076`). Exactly one
  reader is constructed per session.
- Block mode `<<<` … `>>>`, including a terminator glued to the last pasted line
  (`main.py:2117-2140`); lines join with `\n`.
- Trailing-backslash continuation joins lines with a single space
  (`main.py:2143-2156`).
- `:task-begin` … `:task-end` buffers a multi-line instruction and **bypasses the
  keyword router**; `:task-abort` discards it (`main.py:2179-2217`,
  `_collect_instruction_buffer` at `main.py:1162-1181`, terminators matched
  case-insensitively).
- `:operator-task` … `:end` collects a block and calls `_handle_operator_task`
  (`main.py:2158-2171`).
- **An empty Enter never exits** (`main.py:2106-2112`); EOF/`Ctrl+C` prints one
  newline and returns 0.
- `PASTE_COALESCE_GAP_SECONDS == 0.05` (`main.py:1198`).

Tests: `test_repl_input_modes.py`, plus the pre-existing
`tests/test_paste_coalesce.py` for burst coalescing itself.

### 1.8 Pre-dotenv fast paths

`:self-build-propose` and `:schedule-disable` are handled at
`main.py:1925-1932` — **before `load_dotenv()` and before any agent is built** —
and return 0 without creating `data/`, `logs/` or `config/`. Matching is
case-insensitive and applies to the head token only. Tests:
`test_cli_one_shot_policy.py`, extending
`tests/test_cli.py::test_self_build_propose_one_shot_short_circuits_before_agent_build`
and `::test_schedule_disable_one_shot_short_circuits_before_agent_build`.

---

## 2. Observed implementation behavior — frozen only for extraction

Recorded so extraction cannot change it silently. **Not endorsed as final
design.**

### 2.1 An ordinary one-shot meta-command builds the full agent first

For every `:command` except the two fast paths, `build_agent(...)` runs
(`main.py:2015`) *before* `handle_meta_command` (`main.py:2026`). A local, no-LLM
command therefore still constructs a provider-backed agent, resolves model
routes, and opens the usage/budget ledger paths. The no-LLM guarantee means **no
model call**, not "no agent construction".

Observed directly by running `python main.py --ask ":help" --workspace .`: it
emits `[SESS] session_start … llm_provider=… llm_model=…` before the help page.
Frozen by `test_cli_one_shot_policy.py::test_ordinary_meta_command_builds_the_agent_before_dispatch`
using call-order fakes (no provider is constructed in the test).

### 2.2 Startup ordering

`parse_args` → `_force_utf8_io()` (`main.py:1923`) → workspace resolve → the two
fast paths → `load_dotenv()` (`main.py:1934`) → resume → file preflight → mode
selection. UTF-8 initialisation is therefore **not** the first thing that runs,
and `load_dotenv()` runs after the fast paths. The daemon inbox notice
(`main.py:2092`) prints before the REPL banner.

### 2.3 Rate-limit asymmetry

`CLIRateLimiter(max_requests=30, window_seconds=60.0)` is constructed inside
`main()` (`main.py:2085-2086`) and exists only in the REPL — one-shot has no
limiter.

| REPL path | Consumes a token? | Anchor |
|---|---|---|
| plain question | **yes** | `main.py:2228` |
| `:task-begin` buffer | **yes** (after the local-reply check) | `main.py:2200` |
| explicit `:command` | no | `main.py:2218-2222` returns first |
| local operator reply | no | `main.py:2223` returns first |
| intent-routed message | no | `main.py:2225` returns first |

The third row is the notable one: an intent-routed message can run a real handler
without being counted. A blocked request prints to stderr and keeps the loop
alive. Frozen by `test_repl_rate_limit_paths.py`.

### 2.4 Interactive agent construction

The REPL calls `build_agent(workspace, with_memory=True,
approval_provider=…)` (`main.py:2079`) and **does not pass `with_persistent`**,
so the `app/bootstrap.py` default (`True`) applies. Frozen by
`test_cli_mode_selection.py::test_repl_builds_agent_with_memory_and_default_persistent`.

### 2.5 `main` is an importable compatibility surface

`main.py` already re-exports names for backwards compatibility: nine helpers from
`cli/parsers.py` (`main.py:68-80`, documented in `cli/__init__.py:4-5`),
`build_agent` from `app/bootstrap.py` (`main.py:185`), `_force_utf8_io` from
`app/io.py` (`main.py:50`).

`agent_tick.py:739`, `agent_tick.py:1175` and `api/server.py:83` do
`from main import build_agent` at call time, and several suites patch attributes
**by name on the module** (`tests/test_autonomous_runtime.py:283,407,463`;
`tests/test_cli.py:1263,1309,2258`; `tests/test_intent_bridge.py:39,48,69`).
Those bindings resolve against `main`'s namespace, so moving `main()` elsewhere
turns such patches into silent no-ops unless the re-exports remain. Inventory
frozen by `test_main_public_surface.py`.

`main.py:2247-2248` is already the target launcher shape; only the import source
would change.

---

## 3. Known divergences and undesirable side effects

### 3.1 The command surface is defined in four hand-maintained places

Exact snapshot at `9daa9bf` (`test_command_surface_snapshot.py`):

| Surface | Size | Derived from |
|---|---|---|
| `handle_meta_command` head chain | **140** tokens | `main.py:1396-1855` |
| `:help` page | **96** tokens | printed at `main.py:1716-1834` |
| REPL startup banner | **72** tokens (71 dispatched + `:task-begin`) | literal at `main.py:2097` |
| pre-dotenv fast paths | **2** | `main.py:1925-1932` |

Recorded divergences:

- **69** dispatched commands are absent from the banner (e.g. `:clear`,
  `:hygiene`, `:rollback`, `:inbox`, `:assumptions`, `:exit`).
- Dispatched aliases absent from `:help` include `:memory-status`, `:reset`,
  `:kill-switch`, `:assumption-log`.
- The banner advertises **`:task-begin`**, which the REPL intercepts itself and
  `handle_meta_command` never dispatches.
- The banner advertises **`:help`**, but the help page does not list itself — so
  the banner is *not* a subset of the help page.
- `:help` also prints four REPL block tokens (`:task-begin`, `:task-end`,
  `:task-abort`, `:end`), section headings, `flags:` continuation lines, the
  `empty line` note and Russian conversational-shortcut prose. **Not every token
  printed by `:help` or the banner is a dispatchable command.**

Consequence for Phase 2: a registry must model dispatched commands, aliases,
REPL block tokens, the `?` alias (which cannot live in a `:token` set), help
prose and the banner subset as *separate* concerns.

### 3.2 Natural-language intents partially duplicate handler selection

`_dispatch_operator_intent` (`main.py:1106-1159`) selects handlers itself across
**23** `intent.kind` branches; `shell_command_hint` is handled earlier
(`main.py:1092`) and never reaches it. Four intents — `capability_check`,
`current_gaps_check`, `weakness_finder`, `next_safe_test` — target handlers with
**no `:command` equivalent at all**, so "delegate to the same dispatcher" is only
possible for a subset unless the registry also models non-command intent targets.

### 3.3 The command-map guard reads `main.py` as text

`scripts/commands_map_check.py:35,39-40` regex-scans `main.py` for
`head ==` / `head in {…}`. When dispatch moves out of `main.py` the derived set
shrinks and the parity check keeps **passing** — a false green, not a failure.
Any dispatch move must be accompanied (or preceded) by the Phase-2 replacement.

### 3.4 `main.py` exceeds its own governance ceiling

`scripts/check_ceo_file_baseline.py:14` sets a soft ceiling of 2000 lines;
`main.py` is 2248 → the script reports `REVIEW` and exits 1. This is expected at
`9daa9bf`, is not caused by Phase 0, and is not wired into
`.github/workflows/ci.yml`.

### 3.5 CI cannot validate anything right now

Every GitHub Actions run fails at 0 executed steps with the check-run annotation
*"The job was not started because your account is locked due to a billing
issue."* Phase gates are local-only until that is resolved.

---

## 4. Behavior not yet verified

- **Paste-burst coalescing under real terminal timing.** The characterization
  suite drives `_StdinLineReader` with `interactive=False` (deterministic,
  line-at-a-time). Timing-dependent coalescing is covered separately by
  `tests/test_paste_coalesce.py`; no test reproduces a real TTY.
- **`_stdin_is_interactive()` returning `True`.** Every characterization run is
  non-interactive, so the interactive branch of `read_message`
  (`main.py:1297-1308`) is exercised only by `tests/test_paste_coalesce.py`.
- **An empty `--resume ""`.** It is falsy, so the validation block is skipped
  entirely and no branch is entered — recorded as a documented `skip` in
  `test_cli_resume_branches.py` rather than asserted as validation behavior.
- **Real provider/model behavior on any path.** No characterization test
  constructs a network-backed client; `build_agent` and `load_dotenv` are
  monkeypatched wherever their real work is not the behavior under test.
- **`_print_daemon_inbox_notice` content** (`app/daemon_notice.py`, 19% covered).
  Only its call ordering relative to the banner is frozen here.
- **The interactive approval flow end-to-end.** Only that the prompt is written
  to stderr and reads from the shared reader is frozen; a full approve/deny
  round-trip through the REPL is not characterized.
- **`main.py` line-level coverage remains 69%** (304 statements, 67 partial
  branches uncovered at `9daa9bf`). Phase 0 raises confidence on the CLI
  contract, not on every branch inside the extracted-to-be regions.

_Source of facts: `main.py`, `app/io.py`, `app/bootstrap.py`, `core/checkpoint.py`,
`core/rate_limiter.py`, `scripts/commands_map_check.py`,
`scripts/check_ceo_file_baseline.py` and `tests/characterization/` at `9daa9bf`._
