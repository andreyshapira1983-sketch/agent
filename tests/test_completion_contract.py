"""The completion contract: what a request owes, fixed before the work (MIR-067).

The operator's ruling of 2026-08-02, clause by clause:

  1. the result is a separate structured contract        -> TestContractShape
  2. derived from the request BEFORE the work            -> TestDerivedBeforeWork
  3. verifiable obligations + a verification method each -> TestContractShape
  4. plan and a good answer do not prove completion      -> TestAnswerCannotSatisfy
  5. unmet obligation forbids achieved / success / credit-> TestUnmetBlocksCredit
  6. ambiguous -> ask, never guess                       -> TestAmbiguityAsksInstead

The measured symptom this closes (MASTER_ISSUE_REGISTRY, MIR-067): asked to fix
a test, propose a change and file an approval request, the agent performed the
verification step only, declared `achieved`, banked `outcome=success` and
minted a procedure — and was right by the system's own definition, because
nothing represented the deliverable.
"""
from __future__ import annotations

import inspect
import pathlib

from core.completion_contract import (
    CompletionContract,
    derive_completion_contract,
    unmet_obligations,
)
from core.completion_obligation import evaluate_completion_obligations
from core.smart_memory import EpisodeRecord, assemble_completion_verdict


def _green_tests() -> dict:
    """A `run_tests` receipt in the shape production actually writes."""
    return {
        "tool": "run_tests",
        "output": {
            "exit_code": 0, "failed": 0, "errors": 0,
            "timed_out": False, "passed": 12,
        },
        "issues": [],
    }


class TestContractShape:
    def test_every_obligation_carries_a_verification_method(self):
        contract = derive_completion_contract(
            "Создай файл docs/report.md и исправь core/loop.py"
        )
        assert contract.obligations
        for duty in contract.obligations:
            assert duty.verification.strip(), duty
            assert duty.deliverable in {
                "file_exists", "file_modified", "tests_green"
            }

    def test_create_request_owes_the_file(self):
        contract = derive_completion_contract("Создай файл docs/report.md с итогами")
        assert [(d.deliverable, d.target) for d in contract.obligations] == [
            ("file_exists", "docs/report.md")
        ]

    def test_fix_plus_tests_owes_both(self):
        contract = derive_completion_contract(
            "Исправь core/loop.py чтобы тесты проходили"
        )
        assert {(d.deliverable, d.target) for d in contract.obligations} == {
            ("file_modified", "core/loop.py"),
            ("tests_green", ""),
        }

    def test_a_reading_request_owes_no_deliverable(self):
        """Reading is already the `intent` source's duty; owing it twice would
        make every question a deliverable."""
        contract = derive_completion_contract(
            "Прочитай core/plan_parsing.py и перечисли его функции"
        )
        assert contract.obligations == ()
        assert contract.needs_clarification is False

    def test_a_question_naming_no_object_stays_a_non_event(self):
        """The measured false positive the obligation sensor was built to avoid
        («объясни разницу…» matched a tool keyword) must not come back here."""
        contract = derive_completion_contract(
            "Объясни разницу между REST и GraphQL"
        )
        assert contract == CompletionContract()


class TestDerivedBeforeWork:
    def test_derivation_cannot_see_the_work(self):
        """Structural, not a convention: the deriver has no parameter through
        which the plan, the artifacts or the answer could reach it."""
        params = set(inspect.signature(derive_completion_contract).parameters)
        assert params == {"question", "file_hint"}
        for forbidden in ("plan", "artifacts", "answer", "chain", "steps"):
            assert forbidden not in params

    def test_the_loop_logs_the_contract_before_any_tool_call(self):
        """The journal ordering is the evidence that the criterion predates the
        work. Read off the source: the contract event is emitted in the
        interpret block, above the plan/execute section."""
        source = inspect.getsource(
            __import__("core.loop", fromlist=["loop"])
        )
        contract_at = source.index('self.log.log(\n            "completion_contract"')
        execute_at = source.index("def _execute_step")
        plan_at = source.index('self.log.log("plan"')
        assert contract_at < plan_at, "contract must be fixed before planning"
        assert contract_at < execute_at


class TestAnswerCannotSatisfy:
    def test_a_perfect_answer_does_not_satisfy_a_deliverable(self):
        """Clause 4. The old completion machinery measured the answer, so a
        grounded, fully cited reply WAS success. Verification reads artifacts."""
        contract = derive_completion_contract("Исправь core/foo.py")
        answer = (
            "Conclusion:\nЯ полностью разобрал core/foo.py и объяснил дефект.\n"
            "Facts:\n- всё подтверждено [file:core/foo.py]\nConfidence: high"
        )
        result = evaluate_completion_obligations(
            question="Исправь core/foo.py",
            answer=answer,
            artifacts={"file:core/foo.py": {"tool": "file_read", "output": "…", "issues": []}},
            contract=contract,
        )
        assert result.triggered is True
        assert "deliverable_produced" in result.missing_requirements

    def test_the_same_run_passed_silently_without_a_contract(self):
        """Fail-before, kept as a permanent record of what was missing: the
        identical run is `satisfied` when no contract is supplied."""
        result = evaluate_completion_obligations(
            question="Исправь core/foo.py",
            answer="Я прочитал файл и всё объяснил.",
            artifacts={"file:core/foo.py": {"tool": "file_read", "output": "…", "issues": []}},
        )
        assert result.triggered is False
        assert result.satisfied is True
        assert result.unavailable_sources == ("acceptance_criteria",)

    def test_a_real_write_satisfies_the_deliverable(self):
        contract = derive_completion_contract("Создай файл docs/report.md")
        result = evaluate_completion_obligations(
            question="Создай файл docs/report.md",
            answer="Готово.",
            artifacts={
                "file_write:docs/report.md": {
                    "tool": "file_write",
                    "output": {"path": "docs/report.md"},
                    "issues": [],
                }
            },
            contract=contract,
        )
        assert result.triggered is False
        assert "acceptance_criteria" in result.requirement_sources
        assert result.unavailable_sources == ()


class TestUnmetBlocksCredit:
    def test_an_unmet_deliverable_lowers_a_declared_achieved(self):
        """Clause 5, through the machinery that already exists: the signal is
        authoritative at banking, so `achieved` becomes `partially_achieved`
        and procedure credit is withheld."""
        verdict = assemble_completion_verdict(
            aborted_reason="",
            replan_exhausted=False,
            declared="achieved",
            obligation_unmet=True,
        )
        assert verdict.state == "partially_achieved"
        assert verdict.overridden_by == "obligation_silently_missing"
        assert verdict.diverged is True

        honest = assemble_completion_verdict(
            aborted_reason="",
            replan_exhausted=False,
            declared="achieved",
            obligation_unmet=False,
        )
        assert honest.state == "achieved"
        assert honest.diverged is False

    def test_procedure_credit_is_refused_for_that_episode(self):
        from core.smart_memory import procedure_credit_allowed

        episode = EpisodeRecord(
            goal="Исправь core/foo.py",
            question="Исправь core/foo.py",
            summary="прочитал файл",
            outcome="success",
            tools_used=("file_read",),
            completion_state="partially_achieved",
            declared_completion="achieved",
            defect_signals=("obligation_silently_missing",),
        )
        assert procedure_credit_allowed(episode) is False


class TestAmbiguityIsObservedNotGuessed:
    """Clause 6, honestly scoped.

    An ambiguity is recorded when a change is asked for and no target can be
    read. It is never turned into an obligation, and — measured — it does not
    yet stop the run to ask: see the module docstring for the two candidate
    rules and their false-ask rates on 48 real requests.
    """

    def test_a_change_request_with_no_target_is_an_ambiguity(self):
        contract = derive_completion_contract("исправь это")
        assert contract.obligations == ()
        assert contract.needs_clarification is True

    def test_an_ambiguity_never_becomes_an_obligation(self):
        """Guessing would manufacture a duty the operator never gave — and an
        invented obligation would then block `achieved` on its own."""
        contract = derive_completion_contract("создай файл")
        assert contract.obligations == ()
        assert unmet_obligations(contract, artifacts={}) == ()

    def test_a_quoted_path_in_a_discussion_raises_nothing(self):
        """The measured false positive that kept clause 6 observational: two of
        the four asks under the first candidate rule were discussion turns
        citing a file as evidence."""
        contract = derive_completion_contract(
            "Твоя гипотеза неверна. Доказательство: core/evidence.py строка 522"
        )
        assert contract.obligations == ()
        assert contract.needs_clarification is False

    def test_a_plain_question_about_a_file_raises_nothing(self):
        contract = derive_completion_contract("сколько строк в core/loop.py")
        assert contract.obligations == ()
        assert contract.needs_clarification is False


class TestVerificationReadsEvidence:
    def test_a_simulated_write_does_not_satisfy_the_deliverable(self):
        contract = derive_completion_contract("Создай файл docs/report.md")
        unmet = unmet_obligations(
            contract,
            artifacts={
                "file_write:docs/report.md": {
                    "tool": "file_write",
                    "output": {"path": "docs/report.md"},
                    "issues": ["gateway simulate — effect not executed"],
                }
            },
        )
        assert [d.target for d in unmet] == ["docs/report.md"]

    def test_a_write_to_another_path_does_not_satisfy_it(self):
        contract = derive_completion_contract("Создай файл docs/report.md")
        unmet = unmet_obligations(
            contract,
            artifacts={
                "file_write:docs/other.md": {
                    "tool": "file_write",
                    "output": {"path": "docs/other.md"},
                    "issues": [],
                }
            },
        )
        assert len(unmet) == 1

    def test_tests_green_needs_a_run_tests_artifact(self):
        contract = derive_completion_contract("Почини core/x.py чтобы тесты проходили")
        by_kind = {d.deliverable for d in contract.obligations}
        assert "tests_green" in by_kind
        unmet = unmet_obligations(
            contract,
            artifacts={"file_write:core/x.py": {"tool": "file_write", "output": {"path": "core/x.py"}, "issues": []}},
        )
        assert [d.deliverable for d in unmet] == ["tests_green"]

        unmet_after = unmet_obligations(
            contract,
            artifacts={
                "file_write:core/x.py": {"tool": "file_write", "output": {"path": "core/x.py"}, "issues": []},
                "run_tests:.": _green_tests(),
            },
        )
        assert unmet_after == ()

class TestReviewRoundOn258:
    """Every finding of the #258 review, kept as behaviour.

    They were real: the first draft could be satisfied by a read-only shell
    command, by a same-named file in another directory, by a write the gateway
    only simulated, and by a test run that was red.
    """

    def _contract(self):
        return derive_completion_contract("Создай файл docs/report.md")

    def test_a_shell_command_cannot_satisfy_a_file_deliverable(self):
        unmet = unmet_obligations(
            self._contract(),
            artifacts={
                "shell_exec:cat docs/report.md #a1b2c3": {
                    "tool": "shell_exec",
                    "output": {"stdout": "docs/report.md"},
                    "issues": [],
                }
            },
        )
        assert len(unmet) == 1, "a read-only shell command claimed a write"

    def test_a_same_named_file_elsewhere_does_not_satisfy_it(self):
        unmet = unmet_obligations(
            self._contract(),
            artifacts={
                "file_write:tests/report.md": {
                    "tool": "file_write",
                    "output": {"path": "tests/report.md"},
                    "issues": [],
                }
            },
        )
        assert len(unmet) == 1, "basename matching let another file pass"

    def test_a_red_test_run_does_not_satisfy_tests_green(self):
        contract = derive_completion_contract("почини core/x.py чтобы тесты проходили")
        red = {
            "tool": "run_tests",
            "output": {
                "exit_code": 1, "failed": 3, "errors": 0, "timed_out": False,
            },
            "issues": [],
        }
        unmet = unmet_obligations(
            contract,
            artifacts={
                "file_write:core/x.py": {
                    "tool": "file_write",
                    "output": {"path": "core/x.py"},
                    "issues": [],
                },
                "run_tests:.": red,
            },
        )
        assert [d.deliverable for d in unmet] == ["tests_green"]

    def test_a_timed_out_run_is_not_a_pass(self):
        contract = derive_completion_contract("чтобы тесты проходили")
        stalled = {
            "tool": "run_tests",
            "output": {
                "exit_code": 0, "failed": 0, "errors": 0, "timed_out": True,
            },
            "issues": [],
        }
        assert len(unmet_obligations(contract, artifacts={"run_tests:.": stalled})) == 1

    def test_a_mixed_read_and_change_request_owes_nothing_and_asks(self):
        contract = derive_completion_contract(
            "прочитай core/a.py и исправь core/b.py"
        )
        assert contract.obligations == ()
        assert contract.needs_clarification is True

    def test_one_path_read_then_changed_still_owes_the_change(self):
        contract = derive_completion_contract(
            "Прочитай core/foo.py и исправь его"
        )
        assert [(d.deliverable, d.target) for d in contract.obligations] == [
            ("file_modified", "core/foo.py")
        ]
        assert contract.needs_clarification is False

    def test_the_two_modules_are_not_cyclic(self):
        """`paths_mentioned` moved to the shared scanner's home so the contract
        and the obligation sensor stopped importing each other."""
        import ast

        source = pathlib.Path("core/completion_contract.py").read_text(encoding="utf-8")
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "core.completion_obligation" not in imported
