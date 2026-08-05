"""A handler that writes nothing to the journal must not multiply.

Census item A7. `tests/test_except_audit_ratchet.py` already holds a rule about
these handlers — that a silent one must carry a comment — and it stands at zero.
The census then found three defects whose handlers all satisfied it:

    the answer-safety check whose failure looked like a clean success (A2)
    the referent resolver that could stop working unnoticed (A3)
    the task-specific prompt contract silently replaced (A4)

Every one of them was commented. The comment helps whoever reads the code; it
does nothing for an operator reading logs while the agent runs unattended. So
the existing rule is not wrong, it is answering a different question — and the
question it does not ask is the one the operator asks at three in the morning.

This ratchet asks it: how many handlers report NOTHING to the journal? Two
exclusions, both structural: a handler that re-raises has reported by the
strongest means there is, and a handler nested inside a reporting one is the
last-resort guard around reporting itself, where reporting again is a recursion.

The number is not a defect count. Seventeen handlers being silent is not
seventeen bugs — some are genuinely best-effort paths whose caller reports. It
is a budget that may only shrink, so the next silent handler has to displace an
existing one or be argued for.

The scope is the loop layer, deliberately. That is where the census traced
consequences, one handler at a time. Across all of `core/` the same counter
reports 281, and asserting those are defects would be the mistake this whole
exercise is about — claiming past the measurement. Widening the scope is a
separate decision that wants its own evidence.

**But "the loop layer" is no longer spelled `loop*`, and the reason is a hole
this ratchet fell into within a day of being written.** Census item B1 moved
`propose_repair` into `core/repair_commands.py`, and one journal-silent handler
went with it — the same handler, still silent, now outside a scope defined by
filename. The count read 16 against a budget of 17 and the suite stayed green,
because the second test below allowed a slack of two. A refactor had produced
the exact reading a repair produces.

Both halves are fixed. `loop_layer_files` in the audit follows extractions, and
the budget must now match the measurement EXACTLY. Slack in a ratchet is what
lets a number drift away from the thing it counts; an exact check costs one
deliberate edit when a handler is genuinely fixed, and that edit is where the
reason gets written down.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from except_audit import journal_silent_in, loop_layer_files  # noqa: E402

#: Measured 2026-08-05 after A2, A3 and A4 closed six of the census's original
#: twenty-eight. Lower it when a handler starts reporting; never raise it
#: without saying here why the new silence is right.
#:
#: 17 -> 16 on the same day, and NOT because any code was fixed. The counter
#: stopped being wrong about `core/loop_response_deciders.py:293`, which
#: delegates its report to `_safe_answer_after_enforcement_failure`. Recorded
#: as a measurement correction rather than a repair, because a budget that
#: cannot tell those apart is worth nothing — the same distinction that caught
#: B1 moving a silent handler out of scope an hour earlier.
#:
#: 16 -> 15, also a measurement correction: `core/loop_run_tail.py:155` guards
#: a `try` whose body is nothing but a journal write. `classify_source` already
#: excluded that shape as `try_only_logs`; the rule had simply never been
#: carried into this counter, so the layer was charged for a silence it had no
#: way to remove.
#:
#: 15 -> 13, and THESE two were repairs. `core/loop_run_tail.py:246` and `:258`
#: now report through `_sensor_failed`. See
#: tests/test_run_tail_failures_are_reported.py for what each failure looked
#: like before — the assumption-store one produced a journal identical to a
#: healthy run.
#:
#: 13 -> 11, two more repairs. `core/loop_step_execution.py:130` and `:135`
#: both answered "not read-only" without a word, so a missing tool and a
#: `risk_for` that raises were indistinguishable from a step that genuinely
#: writes — and each cost the whole batch its parallel path. They report
#: through `_risk_probe_failed` now; the ordinary "this step writes" case is
#: still silent, because an event per effect step would be a stream rather
#: than a signal. Cover: tests/test_step_risk_probe_reports.py, 3 red on the
#: old code.
JOURNAL_SILENT_BASELINE = 11

_LAYER = Path("core")


def _loop_layer_rows() -> list[dict]:
    """Rows for the layer as the audit defines it — extractions included.

    Deliberately NOT a filename filter any more. Recomputing the scope here
    would let this file and the audit disagree, and the disagreement would show
    up as a number nobody could reproduce with the tool.
    """
    in_scope = {p.as_posix() for p in loop_layer_files(_LAYER)}
    return [row for row in journal_silent_in(_LAYER) if row["file"] in in_scope]


def test_journal_silence_does_not_grow():
    rows = _loop_layer_rows()
    addresses = [f"{Path(r['file']).name}:{r['line']}" for r in rows]

    assert len(rows) <= JOURNAL_SILENT_BASELINE, (
        "a handler stopped reporting to the journal. A comment satisfies the "
        "older audit and still leaves an operator reading logs with nothing. "
        f"Now {len(rows)}, budget {JOURNAL_SILENT_BASELINE}:\n  "
        + "\n  ".join(addresses)
    )


def test_the_budget_matches_the_measurement():
    """Exactly, with no slack. The slack is what hid B1's move.

    An earlier version allowed the budget to sit up to two above the real count,
    reasoning that a small drift was harmless. It was not: when B1 carried a
    silent handler out of the layer the count fell to 16, the budget stayed at
    17, and the suite said nothing. The drift IS the failure mode — a budget
    above its measurement admits new silence for free, which is how a closed
    campaign leaves a live defect.

    Going red is the point. Whoever lowers the number has to say in the comment
    above whether a handler started reporting or merely moved.
    """
    rows = _loop_layer_rows()

    assert len(rows) == JOURNAL_SILENT_BASELINE, (
        f"the budget says {JOURNAL_SILENT_BASELINE}, the layer measures "
        f"{len(rows)}. If a handler now reports, lower the budget and say so. "
        "If code MOVED, check it did not leave the scope — a refactor must not "
        "read like a repair.\n  "
        + "\n  ".join(f"{r['file']}:{r['line']}" for r in rows)
    )


def test_the_scope_follows_code_extracted_out_of_the_layer(tmp_path):
    """B1's lesson, pinned so the scope cannot quietly shrink back.

    When `propose_repair` moved to `core/repair_commands.py` it took a silent
    handler with it. The file no longer matched `loop*`, the count fell by one,
    and the suite was happy — a refactor reading exactly like a repair. The
    scope now follows the dotted-alias import a facade uses, so the extracted
    module stays measured.
    """
    # Named `core` because the rule matches the import prefix against the
    # directory name, and a fixture that quietly renames the package would be
    # testing something the repository never does.
    pkg = tmp_path / "core"
    pkg.mkdir()
    (pkg / "loop_thing.py").write_text(
        "import core.thing_commands as ops\n", encoding="utf-8"
    )
    (pkg / "thing_commands.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "unrelated.py").write_text("y = 2\n", encoding="utf-8")

    names = {p.name for p in loop_layer_files(pkg)}

    assert "thing_commands.py" in names, (
        "a module the layer extracted behind a facade left the measurement"
    )
    assert "unrelated.py" not in names, (
        "the scope widened past what the census measured"
    )


def test_the_real_layer_still_holds_the_module_b1_extracted():
    """The rule above, checked against the actual repository rather than a
    fixture — a rule that works only on synthetic input has proved nothing.
    """
    names = {p.name for p in loop_layer_files(_LAYER)}

    assert "repair_commands.py" in names
    assert "memory_hygiene_commands.py" in names


def test_a_reraising_handler_is_not_counted_as_silent():
    """It reported by the strongest means available."""
    from except_audit import journal_silent_handlers

    src = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        raise\n"
    )
    assert journal_silent_handlers(src, "x.py") == []


def test_a_guard_around_reporting_itself_is_not_counted():
    """Reporting from inside a failed report is a recursion, not a fix."""
    from except_audit import journal_silent_handlers

    src = (
        "def f(self):\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        try:\n"
        "            self.log.log('failed', {})\n"
        "        except Exception:\n"
        "            pass\n"
    )
    rows = journal_silent_handlers(src, "x.py")
    assert rows == [], rows


def test_a_plainly_silent_handler_is_counted():
    """The counter must catch the thing it exists for, or it proves nothing."""
    from except_audit import journal_silent_handlers

    src = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError:\n"
        "        pass\n"
    )
    rows = journal_silent_handlers(src, "x.py")
    assert [r["line"] for r in rows] == [4], rows


def test_the_layer_helper_counts_as_reporting():
    """`_sensor_failed` journals on the caller's behalf.

    The older audit matched a literal `.log(` call and scored fifteen reporting
    handlers silent — which is how a correctly reporting handler ended up having
    to carry a justifying comment to satisfy the very tool meant to find silence.
    """
    from except_audit import journal_silent_handlers

    src = (
        "def f(self):\n"
        "    try:\n"
        "        g()\n"
        "    except ValueError as exc:\n"
        "        self._sensor_failed('thing', exc)\n"
    )
    assert journal_silent_handlers(src, "x.py") == []


def test_a_handler_that_delegates_its_report_to_a_helper_is_not_silent():
    """Found in A7 at `core/loop_response_deciders.py:293`.

    `_enforce_answer_safety` was scored silent while calling
    `_safe_answer_after_enforcement_failure`, whose first statement writes
    `answer_enforcement_failed`. Nothing was wrong with the code — the counter
    was matching a literal `.log(` inside the handler's own body, so moving a
    report into a well-named helper made the code look worse. A measure that
    punishes the better arrangement is broken as a measure.

    The helper list is no longer hand-maintained either: it held exactly one
    name, which is the same "wherever someone looked" scope this item exists to
    remove.
    """
    from except_audit import journal_silent_handlers

    src = (
        "class C:\n"
        "    def _report(self, exc):\n"
        "        self.log.log('failed', {'e': exc})\n"
        "        return SafeThing()\n"
        "    def f(self):\n"
        "        try:\n"
        "            g()\n"
        "        except ValueError as exc:\n"
        "            return self._report(exc)\n"
    )
    assert journal_silent_handlers(src, "x.py") == []


def test_a_helper_that_only_sometimes_logs_does_not_launder_the_silence():
    """The limit of the rule above, and the reason it says "unconditional".

    A helper logging inside an `if` reports on some paths and not others. Were
    that enough, any silence could be laundered by moving it behind a helper
    with a conditional write — the counter would measure delegation rather than
    reporting, and would go quiet exactly where the code got harder to follow.
    """
    from except_audit import journal_silent_handlers

    src = (
        "class C:\n"
        "    def _maybe(self, exc):\n"
        "        if self.verbose:\n"
        "            self.log.log('failed', {'e': exc})\n"
        "    def f(self):\n"
        "        try:\n"
        "            g()\n"
        "        except ValueError as exc:\n"
        "            self._maybe(exc)\n"
    )
    rows = journal_silent_handlers(src, "x.py")
    assert [r["line"] for r in rows] == [8], rows


def test_a_write_inside_a_try_still_counts_as_unconditional():
    """`try` is not a branch — the body runs.

    `_safe_answer_after_enforcement_failure` wraps its journal write in one so a
    logging failure cannot stop the refusal, and reading that as "might not log"
    would have kept the false positive this rule exists to remove.

    Both handlers here are legitimate, for two different structural reasons:
    the outer one delegates its report, and the inner one guards the write
    itself. Neither is a silence the layer could remove.
    """
    from except_audit import journal_silent_handlers

    src = (
        "class C:\n"
        "    def _report(self, exc):\n"
        "        try:\n"
        "            self.log.log('failed', {'e': exc})\n"
        "        except Exception:\n"
        "            pass\n"
        "    def f(self):\n"
        "        try:\n"
        "            g()\n"
        "        except ValueError as exc:\n"
        "            self._report(exc)\n"
    )
    assert journal_silent_handlers(src, "x.py") == []


def test_a_handler_guarding_a_pure_journal_write_is_not_counted():
    """Found in A7 at `core/loop_run_tail.py:155`, and the fix was already
    written elsewhere: `classify_source` has excluded this shape as
    `try_only_logs` since the older audit. The rule had never been carried into
    this counter, so the layer was charged for a silence it had no way to
    remove — reporting a failed report is a recursion, not a fix.
    """
    from except_audit import journal_silent_handlers

    src = (
        "def f(self):\n"
        "    try:\n"
        "        self.log.log('thing', {})\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert journal_silent_handlers(src, "x.py") == []


def test_a_handler_guarding_real_work_beside_a_write_is_still_counted():
    """The limit of the rule above. `core/loop_run_tail.py:246` had a journal
    call in its `try` too — right after the profile update that could fail. If
    the presence of ANY write bought the exemption, that defect would have been
    excused instead of found.
    """
    from except_audit import journal_silent_handlers

    src = (
        "def f(self):\n"
        "    try:\n"
        "        self.profile = update()\n"
        "        self.log.log('profile_update', {})\n"
        "    except Exception:\n"
        "        pass\n"
    )
    rows = journal_silent_handlers(src, "x.py")
    assert [r["line"] for r in rows] == [5], rows
