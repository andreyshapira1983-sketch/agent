"""Banked: the head names a goal, the hands pick their own target.

MEASURED LIVE on the 2026-08-19 23:31 tick, which is why this is a bank and
not a theory. In one run:

  * head (charter selector, Sol, 20:31:16Z) chose the goal «Trace the
    current `:team-run` workflow and draft a reviewed proposal for the
    minimum forensic event envelope covering inputs, policy decisions, tool
    activity, approvals, and outputs»;
  * hands (autonomous_runtime → self-build producer, Terra, 20:33:56Z)
    emitted the run's ONLY concrete product: approval item
    ain_b0cd21fe… , «self-apply split proposal for
    core/self_task_producer.py (+5 new file(s))».

Nothing connects the two but the tick they share. Across every trace in
logs/ there are 6 such (campaign goal ↔ produced proposal) pairs and in
0 of them does the goal name the target the hands took.

Read the 0/6 carefully: in five of the six the goal named no target at all
(«найди и почини свои дефекты»), so the hands had nothing to disobey —
picking freely is what `_propose_engineering_step`'s own docstring promises
(«turn the top real backlog candidate into a proposal»). The defect is
therefore NOT «the hands ignored an instruction». It is that the road has
no way to carry one, in two independent places:

  * core/best_next_action.py — `BestNextAction` has no target field at all.
    `_candidate_engineering_task()` classifies a goal as engineering partly
    BY MATCHING A `.py` FILENAME IN IT (`_PY_TARGET_RE`) and then keeps only
    `evidence=("goal: …",)`. The filename is read and dropped in the same
    function.
  * both production callers of `produce_self_apply_proposal()` —
    core/campaign_io.py (charter road) and core/autonomous_runtime.py
    (the one that fired tonight) — leave `candidate_targets` and
    `grounded_selector` unset, so the producer re-derives its own top
    backlog candidate independently of anything the head decided.

Consequence, stated without overclaiming: a goal that DOES name a module
has never yet occurred, so «the hands would then take a different file» is
an INFERENCE, not an observation. What IS observed is that the machinery
cannot bind them even in principle, and that tonight the agent's declared
purpose and its only product were unrelated.

Deliberately unprescribed: whether the fix is a `target` field on the
action, a goal-derived `candidate_targets`, or a refusal when the goal
names a module the backlog does not rank. The invariant is only that the
head's choice must be able to reach the hands.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PRODUCER = "produce_self_apply_proposal"
#: Machine-driven callers only. cli/ is the operator's hand: a human typing
#: :self-build-produce IS the binding, so it proves nothing about the loop.
_MACHINE_DIRS = ("core", "app")


def _producer_call_sites() -> list[tuple[str, int, set[str]]]:
    """Every machine call of the producer, with the kwargs it passes."""
    sites: list[tuple[str, int, set[str]]] = []
    for folder in _MACHINE_DIRS:
        for path in (_REPO / folder).glob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - unparseable file
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != _PRODUCER:
                    continue
                kwargs = {kw.arg for kw in node.keywords if kw.arg}
                sites.append((f"{folder}/{path.name}", node.lineno, kwargs))
    return sites


def test_the_producer_is_actually_called_by_the_machine() -> None:
    """Boundary pin: if this fails the bank below is measuring nothing."""
    sites = _producer_call_sites()
    assert sites, f"no machine caller of {_PRODUCER}() — the road moved"
    assert len(sites) >= 2, (
        f"expected both the charter road and the runtime to call it; found {sites}"
    )


def test_the_producer_can_be_told_which_target_to_take() -> None:
    """Boundary pin: the socket exists, so the gap is wiring, not API."""
    src = (_REPO / "core" / "self_build_producer.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    params: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _PRODUCER:
            params = {a.arg for a in node.args.args + node.args.kwonlyargs}
    assert {"candidate_targets", "grounded_selector"} <= params, (
        "the producer lost the parameters that could carry the head's choice"
    )


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured live 2026-08-19 23:31 and banked rather than "
        "fixed: every machine caller of the self-build producer leaves both "
        "target sockets empty, so the producer re-selects its own top "
        "backlog candidate with no knowledge of the goal the head just "
        "chose. The invariant: at least one machine road must be able to "
        "hand the producer the target its own goal named."
    ),
    strict=True,
)
def test_some_machine_road_hands_the_producer_a_target() -> None:
    sites = _producer_call_sites()
    binding = [s for s in sites if {"candidate_targets", "grounded_selector"} & s[2]]
    assert binding, (
        "no machine caller passes candidate_targets or grounded_selector; "
        f"call sites and their kwargs: {sites}"
    )


def test_the_head_decision_can_name_a_target() -> None:
    """ПРОБЕЛ ЗАКРЫТ 2026-08-24; до того здесь стоял строгий xfail.

    Класс назван в исторической сводке оператора и в журнале как H-20 —
    binding drift: обязательство переживает перерыв и при этом теряет связь с
    конкретным предметом. Замер показал ровно это: `_PY_TARGET_RE` находил имя
    файла, по нему цель признавалась инженерной, и совпадение ВЫБРАСЫВАЛОСЬ —
    имя оставалось лишь внутри `evidence`, дословным эхом текста цели, откуда
    машине его не взять.

    Проверяется и структура, и поведение: поле без значения было бы той же
    потерей связи, только с графой.
    """
    src = (_REPO / "core" / "best_next_action.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BestNextAction":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                    stmt.target, ast.Name
                ):
                    fields.add(stmt.target.id)
    assert fields & {"target", "target_path", "targets"}, (
        f"BestNextAction carries no target field; it has {sorted(fields)}"
    )

    from core.best_next_action import _candidate_engineering_task

    decided = _candidate_engineering_task(
        "Починить сенсор размера в core/backlog_signals.py"
    )
    assert decided is not None
    assert decided.target_path == "core/backlog_signals.py", (
        "поле есть, а объект в нём не лежит — связь потеряна там же, где была"
    )

    unnamed = _candidate_engineering_task("Отрефакторить архитектуру памяти")
    if unnamed is not None:
        assert unnamed.target_path is None, (
            "объект назван там, где основание его не называло — это выдумка, "
            "а не связь"
        )
