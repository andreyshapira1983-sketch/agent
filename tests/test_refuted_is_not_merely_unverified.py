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
from core.smart_memory import (
    EpisodicMemoryStore,
)
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
