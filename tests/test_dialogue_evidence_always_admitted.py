"""Ссылка на собственную прошлую реплику — не фабрикация.

Живой прогон 2026-08-03, ход 2. Оператор спросил «куда бы ты хотел
развиваться»; агент честно сослался на то, что сам сказал ходом раньше —
`[dialogue:1]`. Журнал этого хода:

    cited_but_unmatched_chunks=2
    fabricated_citations=2
    citation_integrity_violation=True
    chain_was_empty=True

При этом история диалога БЫЛА подана ему в контекст (`memory_inject
turns_visible=1`). То есть читать разговор разрешено, а ссылаться на него —
нельзя, и за честную ссылку прилетает клеймо выдумки.

Корень (core/loop.py): диалог добавлялся в цепочку улик только когда
срабатывал детектор самокоррекции. В ходе 2 он не сработал — там нет упрёка,
это обычный вопрос. Гейт лишний: безопасность держит не он, а верификатор,
который для диалога выдаёт `dialogue_supported` и никогда `verified`
(issue #119). Здесь закрепляется: есть история — есть улика.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.memory import WorkingMemory
from core.policy import PolicyGate
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

#: Ход 2 прогона — обычный вопрос, БЕЗ упрёка и просьбы починиться.
FOLLOW_UP_QUESTION = (
    "если тебе сказали и Дали возможность развиваться Куда ты хотел бы "
    "развиваться не по документации обсуди со мной эту мысль"
)

PRIOR_QUESTION = "скажи что ты думаешь о себе как автономный агент"
PRIOR_ANSWER = (
    "Conclusion: у меня нет устойчивого цифрового «я». "
    "Facts:\n  - я живу в контексте текущего запроса [general-knowledge]"
)

#: Ответ ссылается на собственную прошлую реплику — ровно как в прогоне.
ANSWER_CITING_THE_DIALOGUE = (
    "Conclusion: развиваться я хотел бы в сторону калибровки суждений.\n"
    "Facts:\n"
    "  - ранее в этом диалоге я сказал, что у меня нет устойчивого "
    "цифрового «я» [dialogue:1]\n"
    "Confidence: 0.6\n"
    "Unverified: это рассуждение, а не факт о мире"
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _loop_with_history(workspace: Path) -> tuple[AgentLoop, list[str]]:
    registry = ToolRegistry()
    memory = WorkingMemory()
    memory.record_turn(
        question=PRIOR_QUESTION,
        planner_reasoning="no tools needed",
        tools_used=[],
        artifact_labels=[],
        answer=PRIOR_ANSWER,
    )
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[ANSWER_CITING_THE_DIALOGUE]),
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
        ),
        planner=FakePlanner(sources=[], reasoning="опираюсь на историю диалога"),
        memory=memory,
    )
    events: list[str] = []
    _orig = loop.log.log

    def _spy(event_type, *a, **k):
        events.append(event_type)
        return _orig(event_type, *a, **k)

    loop.log.log = _spy  # type: ignore[method-assign]
    return loop, events


def test_dialogue_is_evidence_without_a_correction(workspace: Path):
    """Обычный вопрос с историей — диалог всё равно допущен в улики."""
    loop, events = _loop_with_history(workspace)

    loop.run(FOLLOW_UP_QUESTION)

    assert "self_analysis_turn" not in events or "dialogue_evidence_admitted" in events
    assert "dialogue_evidence_admitted" in events, (
        "история диалога подана в контекст, но не допущена как улика — "
        "честная ссылка на свою же реплику станет «фабрикацией»"
    )


def test_the_honest_self_citation_is_not_called_fabricated(workspace: Path):
    """Главное: ссылка на свою прошлую реплику не считается выдумкой."""
    loop, _events = _loop_with_history(workspace)

    loop.run(FOLLOW_UP_QUESTION)

    report = loop.last_verification
    assert report is not None
    assert report.cited_but_unmatched_chunks == 0, (
        "ссылка на реальную прошлую реплику помечена как несуществующая"
    )
    assert not report.chain_was_empty, "цепочка улик пуста, хотя история есть"


def test_dialogue_support_is_still_never_verified(workspace: Path):
    """Расширение допуска не должно превращать разговор в подтверждение."""
    loop, _events = _loop_with_history(workspace)

    loop.run(FOLLOW_UP_QUESTION)

    report = loop.last_verification
    assert report is not None
    assert report.verified_chunks == 0, (
        "разговор нельзя засчитывать как внешнее подтверждение (issue #119)"
    )


def test_a_first_turn_without_history_admits_nothing(workspace: Path):
    """Ссылаться не на что: без истории диалоговых улик не появляется."""
    registry = ToolRegistry()
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[ANSWER_CITING_THE_DIALOGUE]),
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
        ),
        planner=FakePlanner(sources=[], reasoning="первый ход"),
        memory=WorkingMemory(),
    )
    events: list[str] = []
    _orig = loop.log.log
    loop.log.log = lambda e, *a, **k: (events.append(e), _orig(e, *a, **k))[1]  # type: ignore[method-assign]

    loop.run(PRIOR_QUESTION)

    assert "dialogue_evidence_admitted" not in events
