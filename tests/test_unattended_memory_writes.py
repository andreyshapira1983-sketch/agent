"""An unattended run may write to long-term memory only if told to, and only twice-sourced.

Operator, 2026-08-05: "I cannot turn on long-term memory in a campaign —
`auto_write_memory=False` is hardcoded. Add an explicit opt-in flag, off by
default, and a test that without it writing is forbidden, and with it only
verified claims pass."

Both halves matter, and measuring the second corrected my reason for the first.

The obvious story — "the run extracted `_touch(tmp_path / ...)` from a test file
and only the hardcoded False kept it out of memory" — is WRONG, and checking it
is what proved it. `KnowledgeWritePolicy` already refuses code sources:
"programs do not assert facts". Those 34 scaffolding claims would have been
rejected with the flag on.

What is NOT covered is prose. Measured 2026-08-05, single source, no
corroboration:

    src.py          -> reject  (programs do not assert facts)
    notes.md        -> save    (confidence 0.90)
    docs/guide.md   -> save    (confidence 0.90)
    a web page      -> save    (confidence 0.75)

So one document asserting something becomes a permanent fact in a run nobody is
watching. That is what `require_verified` is for, and it is a different hazard
from the one that raised the question.

`verified` is not a label anyone applies by hand. `KnowledgePipeline.build_registry`
promotes a claim to it when a SECOND source corroborates it, and only if the
claim was normally `extracted` — so two weak sources cannot manufacture a fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from core.autonomous_runtime import AutonomousRuntimeConfig
from core.evidence import ProvenanceChain, make_evidence
from core.knowledge_pipeline import KnowledgePipeline
from core.source_registry import SourceRegistry


@dataclass
class _RecordingRemember:
    """Stands in for `agent._remember_from_knowledge` and counts what it is asked."""

    calls: list[tuple[str, Any]] = field(default_factory=list)

    def __call__(self, content, tags, origin, kind, scope):
        self.calls.append((content, tags))

        class _Decision:
            decision = "save"
            reasons: tuple[str, ...] = ()
            policy_id = "test"

        class _Record:
            id = f"mem_{len(self.calls)}"

        return _Decision(), _Record()


def _chain_with(*excerpts: str) -> ProvenanceChain:
    chain = ProvenanceChain()
    for i, excerpt in enumerate(excerpts, start=1):
        chain.add(make_evidence(
            kind="file", source_id=f"notes{i}.md", obtained_via="tool",
            claim=f"contents of notes{i}.md", excerpt=excerpt,
        ))
    return chain


def test_the_flag_is_off_by_default():
    """An operator who configures nothing gets no unattended writes."""
    assert AutonomousRuntimeConfig().learning_writes_memory is False


def test_without_the_flag_nothing_is_written():
    """The default path must not reach `remember` at all — not even to reject.

    Checked by counting calls rather than by reading counters: a pipeline that
    called `remember` and then discarded the result would report zero saved and
    still have written.
    """
    remember = _RecordingRemember()
    pipeline = KnowledgePipeline()

    result = pipeline.run(
        _chain_with("Alpha is the first letter.\n\nBeta is the second.\n"),
        remember=remember,
        auto_write_memory=False,
    )

    assert remember.calls == [], remember.calls
    assert result.memory_saved == 0
    assert result.memory_skipped > 0, "skipped rows are how the reader learns why"


def test_with_the_flag_an_uncorroborated_claim_is_still_skipped():
    """One source is not enough to become a memory.

    A `.md` source on purpose: a code file is already rejected by the write
    policy, so testing the gate with one would prove nothing about the gate.
    Prose is exactly the case the gate exists for — a single document that
    would otherwise be saved at confidence 0.90.

    `memory_skipped`, not `memory_rejected`: the claim was never judged bad, it
    was never seconded.
    """
    remember = _RecordingRemember()
    pipeline = KnowledgePipeline()

    result = pipeline.run(
        _chain_with("The gate refuses an unsigned request.\n"),
        remember=remember,
        auto_write_memory=True,
        require_verified=True,
    )

    assert remember.calls == [], remember.calls
    assert result.memory_saved == 0
    assert result.memory_skipped > 0


def test_the_gate_lets_a_verified_claim_through_the_real_run(monkeypatch):
    """End to end through `run`, with corroboration faked at its source.

    `build_registry` is patched to return a registry whose claims are already
    `verified`, which is what a second corroborating source produces. The point
    is that `run` then calls `remember` — the gate is a filter, not a wall.
    """
    remember = _RecordingRemember()
    pipeline = KnowledgePipeline()
    chain = _chain_with("The gate refuses an unsigned request.\n")

    registry, conflicts = pipeline.build_registry(chain)
    promoted = SourceRegistry()
    for source in registry.sources:
        promoted.add_source(source)
    for claim in registry.claims:
        promoted.add_claim(replace(claim, status="verified"))

    monkeypatch.setattr(pipeline, "build_registry",
                        lambda *a, **k: (promoted, conflicts))

    result = pipeline.run(
        chain, remember=remember, auto_write_memory=True, require_verified=True,
    )

    assert remember.calls, "a verified claim never reached memory"
    assert result.memory_saved > 0
    assert result.memory_skipped == 0


@pytest.mark.xfail(strict=True, reason=(
    "А1 очереди починок: два вызова конвейера знаний на пути ХОДА не передают "
    "require_verified — core/loop_evidence_chain.py:257 и "
    "core/loop_verify_replan.py:452 (второй внутри цикла добычи цитат). "
    "Сторож расширен на весь core/ и честно красный. Отметка strict: когда "
    "дефект починят, тест станет XPASS и потребует снять эту строку — "
    "молча зазеленеть он не сможет."
))
def test_the_unattended_path_always_demands_verification():
    """Флаг открывает дверь, но не снимает замок — и дверей больше одной.

    Область сторожа — ВЕСЬ `core/`, а не один модуль. Первая редакция разбирала
    только `core.autonomous_runtime`, и это было не осторожностью, а догадкой о
    том, где может быть дефект. Перепись 2026-08-05 показала, чего догадка не
    видела: необслуживаемый прогон идёт через обычный цикл
    (`core/autonomous_runtime.py` зовёт `agent.run`), а конвейер знаний хода
    вызывается ещё из `core/loop_evidence_chain.py` и
    `core/loop_verify_replan.py` — причём второй внутри цикла добычи цитат, то
    есть на каждой итерации. Сторож был честен внутри своей области и не знал,
    что область выбрана заранее.

    Читается из исходника, а не исполняется: чтобы дойти до этих строк, нужен
    целиком собранный прогон, а проверить надо простое — что ни одна настройка
    не может включить запись, не включив содержательные ворота.
    """
    import ast
    import pathlib

    calls = []
    for path in sorted(pathlib.Path("core").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls.extend(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and any(kw.arg == "auto_write_memory" for kw in node.keywords)
        )
    assert calls, "места записи знаний исчезли — обновите этого сторожа"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "require_verified" in kwargs, (
            "запись в память без требования подтверждения: "
            f"{ast.unparse(call)[:120]}"
        )
        verified = next(kw for kw in call.keywords if kw.arg == "require_verified")
        assert isinstance(verified.value, ast.Constant) and verified.value.value is True, (
            f"требование подтверждения стало настраиваемым: {ast.unparse(verified.value)}"
        )


def test_a_code_source_is_refused_even_when_verified():
    """The gate is added, not substituted: the code rule still holds above it.

    Two independent refusals now stand between an unattended run and memory —
    "this came from a program" and "only one source said it". Checked together
    because a change that replaced one with the other would leave each hole the
    other used to cover.
    """
    pipeline = KnowledgePipeline()
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file", source_id="core/thing.py", obtained_via="tool",
        claim="contents", excerpt="The gate refuses an unsigned request.\n",
    ))
    registry, _ = pipeline.build_registry(chain)
    assert registry.claims, "fixture produced nothing"

    for claim in registry.claims:
        promoted = replace(claim, status="verified")
        decision = pipeline.write_policy.decide(
            promoted, source=registry.get_source(claim.source_id)
        )
        assert decision.decision == "reject", (
            "код перестал отвергаться, хотя это отдельное правило: "
            f"{list(getattr(decision, 'reasons', []))[:1]}"
        )


def test_a_skipped_claim_leaves_a_reason_not_just_a_counter():
    """The gate must say why it refused, in the same channel as every other decision.

    A claim that vanishes leaving only `memory_skipped += 1` gives the reader a
    number and no cause — the invisible-failure shape MIR-077 was closed for.
    A new gate is the worst possible place to reintroduce it, so the skip
    writes a `decisions` row like the reject path does.
    """
    pipeline = KnowledgePipeline()

    result = pipeline.run(
        _chain_with("The gate refuses an unsigned request.\n"),
        remember=_RecordingRemember(),
        auto_write_memory=True,
        require_verified=True,
    )

    assert result.memory_skipped > 0
    assert len(result.decisions) == result.memory_skipped, (
        "пропущенные claim'ы не оставили строк решения"
    )
    decision = result.decisions[0]["knowledge_decision"]
    assert decision["decision"] == "skip"
    assert decision["policy_id"] == "require_verified"
    assert "verified" in decision["reasons"][0], decision["reasons"]


def test_every_skip_path_says_why_not_only_the_new_one():
    """Both silent paths, because fixing one of two is not fixing the class.

    The `require_verified` skip got a decision row and the older
    `auto_write_memory` short-circuit did not, so a live run on 2026-08-05
    reported `memory_skipped=45, decisions=[]`: forty-five claims went nowhere
    and nothing said why. The two reasons are also different facts — "the
    operator has not opted in" and "no memory writer is wired" — and a reader
    diagnosing an empty memory needs to know which.
    """
    pipeline = KnowledgePipeline()
    chain = _chain_with("Alpha is the first letter.\n")

    off = pipeline.run(chain, remember=_RecordingRemember(), auto_write_memory=False)
    unwired = pipeline.run(chain, remember=None, auto_write_memory=True)

    for result in (off, unwired):
        assert result.memory_skipped > 0
        assert len(result.decisions) == result.memory_skipped, (
            "пропуск без строки решения: счётчик есть, причины нет"
        )
        assert result.decisions[0]["knowledge_decision"]["decision"] == "skip"

    off_reason = off.decisions[0]["knowledge_decision"]["reasons"][0]
    unwired_reason = unwired.decisions[0]["knowledge_decision"]["reasons"][0]
    assert "auto_write_memory" in off_reason, off_reason
    assert "writer" in unwired_reason, unwired_reason
    assert off_reason != unwired_reason, "две разные причины слились в одну"

    # And in the machine-readable half of the row. The prose differed while
    # `policy_id` said `auto_write_memory` for both, so anyone filtering
    # decisions by rule saw the operator blamed for a run where the operator
    # had opted in and no writer was wired. Two facts, one label, and the
    # label is what a filter reads — the same invisible failure one field
    # further down than the one this test was written for.
    off_rule = off.decisions[0]["knowledge_decision"]["policy_id"]
    unwired_rule = unwired.decisions[0]["knowledge_decision"]["policy_id"]
    assert off_rule != unwired_rule, (
        f"обе причины помечены одним правилом {off_rule!r}: "
        "по метаданным их уже не различить"
    )
    assert off_rule == "auto_write_memory", off_rule
    assert "writer" in unwired_rule, unwired_rule
