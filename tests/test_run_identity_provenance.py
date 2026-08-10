"""Кто исполняется — обязано быть записано, а не выведено из имени файла.

ЖИВОЙ СБОЙ 2026-08-10. Агента спросили, что именно сейчас исполняется. Он
назвал `run_adcfda2…` из событий модели и `run_860e7ef…` из журнала и не смог
связать их — потому что связь не записана нигде. Хуже: файл с именем
`run_860e7ef*.jsonl` содержал ДВА прогона, то есть приставка `run_` стояла на
идентификаторе СЕАНСА. Система, которую просили себя опознать, не имела ни
одного машиночитаемого ребра между своими же пространствами имён.

`core/run_context.py` и раньше знал верную семантику — она записана в его
доксroke. Отсутствовала не мысль, а ЗАПИСЬ: производитель есть, потребителя
нет. Здесь пинится ребро.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.ids import new_trace_id
from core.logger import TraceLogger
from core.loop import AgentLoop
from core.policy import PolicyGate
from core.run_context import identity_provenance
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

_ANSWER = (
    "Conclusion: ok. [general-knowledge]\n"
    "Facts:\n- ok [general-knowledge]\n"
    "Sources:\n1. general-knowledge - general-knowledge\n"
    "Confidence: high\nUnverified: nothing\n"
)


def _agent(workspace: Path, trace_id: str) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=FakeLLM(responses=[_ANSWER] * 4),
        logger=TraceLogger(trace_id=trace_id, log_dir=workspace / "logs",
                           verbose=False),
        planner=FakePlanner(sources=[]),
        memory=None,
        max_replan_attempts=1,
    )


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_the_trace_id_does_not_wear_the_run_prefix() -> None:
    """Приставка не имеет права называть сеанс прогоном.

    Один файл журнала собирает все прогоны сеанса; пока он назывался `run_…`,
    единственный способ узнать правду был посчитать `run_id` внутри.
    """
    trace_id = new_trace_id()
    assert not trace_id.startswith("run_"), (
        f"идентификатор СЕАНСА выдан с приставкой прогона: {trace_id!r}"
    )
    assert trace_id.startswith("trace_")


def test_the_run_records_which_session_and_log_it_belongs_to(tmp_path: Path) -> None:
    """Ребро происхождения: прогон -> сеанс/журнал, записанное машиной."""
    trace_id = new_trace_id()
    agent = _agent(tmp_path, trace_id)
    agent.run("что в doc.txt")

    events = _events(tmp_path / "logs" / f"{trace_id}.jsonl")
    edges = [e for e in events if e.get("event") == "run_identity"]
    assert edges, (
        "прогон не записал, какому сеансу и журналу принадлежит — опознать себя "
        "можно было бы только разбором имени файла"
    )
    payload = edges[0]["payload"]
    assert payload["trace_id"] == trace_id
    assert payload["run_id"].startswith("run_")
    assert payload["run_id"] != trace_id, "прогон и сеанс обязаны быть различимы"
    assert "session_id" in payload, "связь с памятью обязана быть названа, пусть и None"


def test_two_runs_of_one_session_are_distinguishable_in_one_log(
    tmp_path: Path,
) -> None:
    """Ровно та ситуация, что сломала опознание: два прогона в одном файле."""
    trace_id = new_trace_id()
    agent = _agent(tmp_path, trace_id)
    agent.run("первый вопрос")
    agent.run("второй вопрос")

    events = _events(tmp_path / "logs" / f"{trace_id}.jsonl")
    run_ids = [e["payload"]["run_id"] for e in events
               if e.get("event") == "run_identity"]
    assert len(run_ids) == 2, f"ожидалось два ребра происхождения, найдено {run_ids}"
    assert len(set(run_ids)) == 2, "два прогона получили один идентификатор"
    assert all(e["trace_id"] == trace_id for e in events), (
        "события одного сеанса обязаны нести один trace_id"
    )


def test_the_edge_is_built_by_the_owner_of_run_identity() -> None:
    """Полезная нагрузка строится там, где живёт идентичность прогона.

    Иначе цикл собирал бы её сам, и второй вызывающий собрал бы иначе.
    """
    payload = identity_provenance(
        trace_id="trace_x", run_id="run_y", task_id=None, session_id="sess_z"
    )
    assert payload == {
        "trace_id": "trace_x",
        "run_id": "run_y",
        "task_id": None,
        "session_id": "sess_z",
    }


@pytest.mark.parametrize("missing", ["trace_id", "run_id"])
def test_the_edge_refuses_to_be_written_half_empty(missing: str) -> None:
    """Половина ребра хуже его отсутствия: она выглядит как связь."""
    kwargs = {"trace_id": "trace_x", "run_id": "run_y",
              "task_id": None, "session_id": None}
    kwargs[missing] = ""
    with pytest.raises(ValueError):
        identity_provenance(**kwargs)  # type: ignore[arg-type]
