# `main` as an import surface — the Phase 7 inventory

`docs/refactor/CLI_BASELINE.md` section 2.5 says Phase 7 ("remove the
compatibility re-exports") needs an inventory to audit against. This is it.

Every number below was **derived from the code** by an AST/scan pass, not typed
by hand, and re-derived after each step. The living version of the central check
is `tests/characterization/test_main_patch_seams.py` — a document goes stale, a
test does not.

## What `main` exposes and who actually needs it

| | |
|---|---|
| names on `main` (module-level imports + defs) | **52** |
| of those, needed by **non-test** code | **1** — `build_agent` |
| names the test suite patches on `main` | **14**, across **127** sites in **12** files |

The only production consumers are `agent_tick.py:739`, `agent_tick.py:1175` and
`api/server.py:83`, all doing `from main import build_agent` lazily. Everything
else in the re-export block exists for tests.

### The 14 patched names, by test file

| file | names patched on `main` |
|---|---|
| `characterization/test_cli_stream_placement.py` | 9 |
| `characterization/test_cli_command_precedence.py` | 8 |
| `characterization/test_repl_input_modes.py` | 8 |
| `characterization/test_repl_rate_limit_paths.py` | 8 |
| `characterization/test_cli_one_shot_policy.py` | 8 |
| `characterization/test_cli_mode_selection.py` | 7 |
| `characterization/test_cli_resume_branches.py` | 5 |
| `characterization/test_command_surface_snapshot.py` | 4 |
| `characterization/test_main_public_surface.py` | 4 (surface assertions, not interception) |
| `test_cli.py` | 3 |
| `test_autonomous_runtime.py` | 1 |
| `test_budget_kill_switch.py` | 1 |

Two groups behave differently and must be treated differently:

* **wiring names** — `build_agent`, `load_dotenv`, `_StdinLineReader`,
  `CLIApprovalProvider`, `_print_daemon_inbox_notice`, `_schedule_disable_message`,
  `_handle_self_build_propose`. `main()` still calls these itself, so patches on
  `main` bite and will keep biting until the wiring moves.
* **collaborator names** — `handle_meta_command`, `_handle_local_operator_reply`,
  `handle_conversational_operator_input`, `_run_agent_with_budget_guard`,
  `_handle_operator_task`. Their call sites already left `main.py`; they keep
  working only because `main()` passes its own bindings into
  `cli/one_shot.py::run_one_shot` and `cli/repl.py::run_repl` — the documented
  parameter seam.

## What the audit found wrong

1. **One inert fake.** `tests/test_cli.py` armed a "no code may be applied"
   guard with `monkeypatch.setattr(main, "_handle_self_apply_run", …)`, but the
   only call site is `cli/command_dispatch.py:406`, which resolves the name in
   its own namespace. Driving `:self-apply-run` with each target in turn proved
   it: patched on `main` the **real** apply lane ran; patched on
   `cli.command_dispatch` the fake fired. Fixed by pointing it at the call site.
2. **One stale claim.** `test_main_public_surface.py` said
   `tests/test_intent_bridge.py` patches `_dispatch_operator_intent` on `main`;
   it patches `cli.intent_bridge`. The test is a surface assertion, and now says
   so.

## Order of the remaining work

Each step is its own change, and the gate between them is not "the suite is
green" alone — a suite that quietly runs the real thing is also green. The
signals to check together: the suite passes, `tests/characterization/` still
runs in ~2s (an 84s run means fakes stopped intercepting), and
`test_main_patch_seams.py` stays green.

1. **Done** — the seam guard plus the two fixes above.
2. **Done, together with step 3** (they could not be separated: re-pointing a
   patch at the owner module only bites once the call site stops binding the
   name at import time). `cli/one_shot.py` and `cli/repl.py` now import the four
   collaborator modules *as modules* and call through the attribute --
   `command_dispatch.handle_meta_command(...)` -- so **one** patch on the module
   that defines a function is observed from both paths. The 5 collaborator
   names moved to their owners across **49 sites in 8 files**, and the seam
   parameters are gone from `run_repl` entirely and from `run_one_shot` except
   `build_agent`. Each new target was proven to intercept by driving both paths
   with a spy: 9/9 fired.
3. ~~Drop the parameter seam~~ — folded into step 2 above.
4. Move the startup wiring (reader, approval provider, agent build, rate limiter,
   daemon notice, banner) out of `main()`; re-point the *wiring* patches (7
   names) in the same change, because that is what moving them costs.
5. Point `agent_tick.py` and `api/server.py` at `app.bootstrap` and remove the
   last re-export. This one touches production code, so it goes last and alone.
