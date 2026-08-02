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

from core.completion_contract import (
    CompletionContract,
    ContractObligation,
    derive_completion_contract,
    unmet_obligations,
)
from core.completion_obligation import evaluate_completion_obligations
from core.smart_memory import EpisodeRecord, assemble_completion_verdict


class TestContractShape:
    def test_every_obligation_carries_a_verification_method(self):
        contract = derive_completion_contract(
            "Создай файл docs/report.md и исправь core/loop.py"
        )
        assert contract.obligations
        for duty in contract.obligations:
            assert duty.verification.strip(), duty
            assert duty.deliverable in {
                "file_exists", "file_modified", "tests_pass"
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
            ("tests_pass", ""),
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
            artifacts={"file:core/foo.py": {"tool": "file_read", "status": "success"}},
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
            artifacts={"file:core/foo.py": {"tool": "file_read", "status": "success"}},
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
                "file_write:docs/report.md": {"tool": "file_write", "status": "success"}
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


class TestAmbiguityAsksInstead:
    def test_a_named_path_without_an_action_is_an_ambiguity_not_a_guess(self):
        """Clause 6. The request names an object but never says what must
        happen to it. Guessing here manufactures a duty the operator never
        gave — and an invented obligation would then block `achieved`."""
        contract = derive_completion_contract("core/foo.py")
        assert contract.obligations == ()
        assert contract.needs_clarification is True
        assert "core/foo.py" in contract.ambiguities[0]

    def test_an_ambiguity_never_becomes_an_obligation(self):
        contract = derive_completion_contract("core/foo.py и core/bar.py")
        assert contract.obligations == ()
        assert len(contract.ambiguities) == 2
        assert unmet_obligations(contract, artifacts={}) == ()


class TestVerificationReadsEvidence:
    def test_a_failed_write_does_not_satisfy_the_deliverable(self):
        contract = derive_completion_contract("Создай файл docs/report.md")
        unmet = unmet_obligations(
            contract,
            artifacts={
                "file_write:docs/report.md": {"tool": "file_write", "status": "error"}
            },
        )
        assert [d.target for d in unmet] == ["docs/report.md"]

    def test_a_write_to_another_path_does_not_satisfy_it(self):
        contract = derive_completion_contract("Создай файл docs/report.md")
        unmet = unmet_obligations(
            contract,
            artifacts={
                "file_write:docs/other.md": {"tool": "file_write", "status": "success"}
            },
        )
        assert len(unmet) == 1

    def test_tests_pass_needs_a_run_tests_artifact(self):
        contract = derive_completion_contract("Почини core/x.py чтобы тесты проходили")
        by_kind = {d.deliverable for d in contract.obligations}
        assert "tests_pass" in by_kind
        unmet = unmet_obligations(
            contract,
            artifacts={"file_write:core/x.py": {"tool": "file_write", "status": "success"}},
        )
        assert [d.deliverable for d in unmet] == ["tests_pass"]

        unmet_after = unmet_obligations(
            contract,
            artifacts={
                "file_write:core/x.py": {"tool": "file_write", "status": "success"},
                "run_tests:.": {"tool": "run_tests", "status": "success"},
            },
        )
        assert unmet_after == ()
