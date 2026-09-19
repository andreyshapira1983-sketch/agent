"""REFUTED — полноправная полярность, а не разновидность «не подтверждено».

ЖИВОЙ СЛУЧАЙ 2026-08-12, `trace_d322a875`, ход 3. Верификатор доказал по
содержимому, что три утверждения не следуют из процитированных ими улик
(`claims_refuted_by_content`), — и ответ всё равно ушёл как есть, эпизод
забанковался `success` с `usage_eligible=True`, из него родился процедурный
кандидат. Замер той же сессии показал почему: опровергнутый кусок получал ТОТ
ЖЕ вердикт `topic_supported_but_claim_unverified`, что и просто неподтверждён-
ный, и дальше все решатели читали только доли verified/weak.

НАРУШЕННЫЙ ИНВАРИАНТ, словами оператора: пять хороших утверждений не делают
одно известное ложное менее ложным. Доказанная ложь и «не проверено» — разные
полярности, и превращать первую во вклад в долю рядом со второй нельзя.

ГРАНИЦА КЛАССА — точная и уже существующая в коде: `ClaimReason` ставят только
опровергающие гейты (арифметика, отсутствующий литерал, опровергнутое
отсутствие). Понижения БЕЗ доказательства лжи — независимость памяти, цифры
без опоры — полярности не несут и остаются «не подтверждено». Здесь ничего не
изобретается: полярность уже есть на куске, чинится её потеря на переходе.

Цепь после ремонта:
    гейт → verdict=refuted + [claim-refuted] в тексте
         → VerificationReport.refuted_chunks
         → хвост прогона: дефект-сигнал content_refuted
         → smart_memory: дисквалификация из опыта и кредита
"""
from __future__ import annotations

from pathlib import Path

from core.approval import AutoApprover
from core.evidence import ProvenanceChain, make_evidence
from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.memory import WorkingMemory
from core.policy import PolicyGate
from core.smart_memory import EpisodicMemoryStore
from core.verifier import verify
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry
from tools.file_read import FileReadTool


def _chain(*evidences) -> ProvenanceChain:
    chain = ProvenanceChain()
    for ev in evidences:
        chain.add(ev)
    return chain


def _file_ev(source_id: str, excerpt: str):
    return make_evidence(
        kind="file", source_id=source_id, obtained_via="test", claim="",
        excerpt=excerpt, confidence=0.9,
    )


_REFUTED_ANSWER = (
    "Conclusion: The producer lives in core/self_build_memory.py. [file:core/loop.py]\n"
    "Facts:\n"
    "- The producer lives in core/self_build_memory.py [file:core/loop.py]\n"
    "Sources:\n1. file:core/loop.py - loop\n"
    "Confidence: high\nUnverified: nothing\n"
)


def test_a_refuted_chunk_gets_its_own_verdict() -> None:
    """ГЛАВНОЕ: доказанная ложь перестаёт называться «не подтверждено»."""
    report = verify(
        answer=_REFUTED_ANSWER,
        chain=_chain(_file_ev("core/loop.py", "from core.loop_memory_write import X\n")),
        user_question="who produces lesson records",
    )
    refuted = [c for c in report.chunks if c.verdict == "refuted"]
    assert refuted, [c.verdict for c in report.chunks]
    assert all(c.reason is not None for c in refuted)
    assert report.refuted_chunks == len(refuted)
    assert report.topic_supported_but_claim_unverified_chunks == 0


def test_the_marker_carries_the_polarity_to_the_operator() -> None:
    """Пометка в отгружаемом тексте называет ложь ложью, а не «не проверено»."""
    report = verify(
        answer=_REFUTED_ANSWER,
        chain=_chain(_file_ev("core/loop.py", "from core.loop_memory_write import X\n")),
        user_question="who produces lesson records",
    )
    line = next(
        ln for ln in report.annotated_answer.splitlines()
        if "self_build_memory" in ln and ln.startswith("-")
    )
    assert "[claim-refuted]" in line, line
    assert "[claim-figure-unverified]" not in line, line


def test_a_claim_verified_by_another_source_is_not_refuted() -> None:
    """ПРЕДОХРАНИТЕЛЬ от ложной полярности: вторая улика доказала — иск снят.

    Гейт сравнил утверждение с ОДНОЙ из процитированных улик и не нашёл
    литерала; другая улика его содержит и подтверждает кусок. Утверждение
    истинно, и остаточная «причина» не имеет права пережить подтверждение —
    иначе `claims_refuted_by_content` считал бы опровергнутыми verified-куски.
    """
    answer = (
        "Conclusion: The tag is minted in core/self_build_memory.py. "
        "[file:core/loop.py] [file:core/self_build_memory.py]\n"
        "Facts:\n"
        "- The tag is minted in core/self_build_memory.py "
        "[file:core/loop.py] [file:core/self_build_memory.py]\n"
        "Sources:\n1. file:core/self_build_memory.py - producer\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(
            _file_ev("core/loop.py", "from core.loop_memory_write import X\n"),
            _file_ev("core/self_build_memory.py",
                     'tags = ["self-build", "lesson", kind, status, outcome] '
                     "# core/self_build_memory.py minted tag\n"),
        ),
        user_question="where is the tag minted",
    )
    assert report.refuted_chunks == 0, [
        (c.verdict, getattr(c.reason, "code", None)) for c in report.chunks
    ]
    assert all(c.reason is None for c in report.chunks if c.verdict == "verified")


def test_merely_unverified_is_still_not_refuted() -> None:
    """Обратная сторона границы: без доказательства лжи полярность не выдаётся.

    Кусок с процентом, которого улика не содержит, — «не подтверждено», а не
    «опровергнуто»: неполная улика не доказывает ложь (пятый гейт того же
    семейства заведён ровно против этого превращения).

    Форма выбрана не случайно: статистический гейт понижает БЕЗ `ClaimReason`,
    и это законно — он не вычислил противоположного, он лишь не нашёл опоры.
    (Первая версия фикстуры с голым числом «7520 tests» получила `verified`
    целиком — известная числовая дыра MIR-060, xfail-класс, не этот тест.)
    """
    answer = (
        "Conclusion: The rate grew by 34%. [file:notes.txt]\n"
        "Facts:\n- The rate grew by 34% [file:notes.txt]\n"
        "Sources:\n1. file:notes.txt - notes\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    report = verify(
        answer=answer,
        chain=_chain(_file_ev("notes.txt", "The rate is discussed in the report.\n")),
        user_question="how fast did the rate grow",
    )
    assert report.refuted_chunks == 0
    assert report.topic_supported_but_claim_unverified_chunks >= 1


def test_five_good_claims_do_not_dilute_one_lie(tmp_path: Path) -> None:
    """КОНЕЦ ЦЕПИ, живая форма: доказанная ложь не банкуется пригодным опытом.

    Ровно конфигурация хода 3: подтверждённых утверждений достаточно, чтобы
    прежняя арифметика долей дала success/eligible. Полярность обязана дойти
    до банкования дефект-сигналом и закрыть карантин.
    """
    root = tmp_path / "ws"
    root.mkdir()
    (root / "doc.txt").write_text(
        "The producer lives in core/limits.py. The gate is documented. "
        "The brake freezes writes. The journal records events. "
        "The sensor reports failures.",
        encoding="utf-8",
    )
    synthesis = (
        "Conclusion: The gate is documented. [file:doc.txt]\n"
        "Facts:\n"
        "- The producer lives in core/limits.py [file:doc.txt]\n"
        "- The brake freezes writes [file:doc.txt]\n"
        "- The journal records events [file:doc.txt]\n"
        "- The sensor reports failures [file:doc.txt]\n"
        "- The real producer is core/self_build_memory.py [file:doc.txt]\n"
        "Sources:\n1. file:doc.txt - doc.txt\n"
        "Confidence: high\nUnverified: nothing\n"
    )
    registry = ToolRegistry()
    registry.register(FileReadTool(workspace_root=root))
    episodic = EpisodicMemoryStore(root / "data" / "episodic_memory.jsonl")
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[synthesis] * 4),
        logger=TraceLogger(new_trace_id(), root / "logs", verbose=False),
        planner=FakePlanner(sources=[{
            "tool": "file_read",
            "arguments": {"path": "doc.txt"},
            "rationale": "read the fixture",
            "label": "doc.txt",
            "expected_outcome": "fixture text",
        }]),
        memory=WorkingMemory(),
        episodic_store=episodic,
        approval_provider=AutoApprover(default="approve"),
        verifier_enabled=True,
        max_replan_attempts=1,
    )
    agent.run("who produces lesson records; cite doc.txt")

    episodes = episodic.load()
    assert episodes, "the run must bank an episode"
    ep = episodes[-1]
    assert ep.verified_chunks >= 3, (
        "фикстура сломалась: подтверждённых утверждений мало, и старая "
        f"арифметика долей не была бы обманута (verified={ep.verified_chunks})"
    )
    assert "content_refuted" in tuple(ep.defect_signals or ()), (
        ep.defect_signals,
        "полярность не дошла до банкования",
    )
    assert ep.usage_eligible is False, (
        "эпизод с доказанной ложью пригоден для обучения — "
        "пять хороших утверждений растворили одну ложь"
    )


def test_the_disqualification_is_shared_law() -> None:
    """Сигнал входит в общий список дисквалификации.

    `test_credit_and_eligibility_agree` параметризован этим множеством и
    автоматически докажет согласие кредита с допуском для нового сигнала.
    """
    from core.smart_memory import DISQUALIFYING_DEFECT_SIGNALS

    assert "content_refuted" in DISQUALIFYING_DEFECT_SIGNALS
