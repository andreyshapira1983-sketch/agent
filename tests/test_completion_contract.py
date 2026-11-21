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
    derive_completion_contract,
    unmet_obligations,
)
from core.smart_memory import assemble_completion_verdict


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


class TestDerivedBeforeWork:
    def test_the_loop_logs_the_contract_before_any_tool_call(self):
        """The journal ordering is the evidence that the criterion predates the
        work. Read off the source: the contract event is emitted in the
        interpret block, above the plan/execute section."""
        source = inspect.getsource(
            __import__("core.loop", fromlist=["loop"])
        )
        contract_at = source.index('self.log.log(\n            "completion_contract"')
        # Планирование и исполнение уехали в `core/loop_attempt` целым циклом
        # попыток (разбор больших файлов на модули); в `core/loop.py` остался
        # его ВЫЗОВ — по нему и меряем порядок, проверяемое свойство то же.
        attempt_at = source.index("self._run_attempt_loop(")
        assert contract_at < attempt_at, "contract must be fixed before planning"

        # И проверка, без которой предыдущая строка ничего не значит: план и
        # исполнение действительно живут ВНУТРИ этого вызова. Уедь они куда-то
        # ещё — «раньше вызова» перестало бы означать «раньше работы».
        attempt_src = inspect.getsource(
            __import__("core.loop_attempt", fromlist=["loop_attempt"])
        )
        assert 'self.log.log("plan"' in attempt_src
        assert "self._execute_steps_parallel(" in attempt_src
        assert 'self.log.log("plan"' not in source, (
            "планирование вернулось в цикл — порядок надо мерить заново"
        )


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


class TestVerificationReadsEvidence:
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


    def test_the_two_modules_are_not_cyclic(self):
        """`paths_mentioned` moved to the shared scanner's home so the contract
        and the obligation sensor stopped importing each other."""
        import ast

        # Through the module, not a literal path: the check is about what
        # THIS module imports, and reading it by name survives a relocation
        # (census C1). `pathlib` stays imported above for the rest of the file.
        import inspect

        from core import completion_contract as _cc_mod

        source = inspect.getsource(_cc_mod)
        imported = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "core.completion_obligation" not in imported
