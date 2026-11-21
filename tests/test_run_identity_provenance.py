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

import pytest

from core.run_context import identity_provenance


@pytest.mark.parametrize("missing", ["trace_id", "run_id"])
def test_the_edge_refuses_to_be_written_half_empty(missing: str) -> None:
    """Половина ребра хуже его отсутствия: она выглядит как связь."""
    kwargs = {"trace_id": "trace_x", "run_id": "run_y",
              "task_id": None, "session_id": None}
    kwargs[missing] = ""
    with pytest.raises(ValueError):
        identity_provenance(**kwargs)  # type: ignore[arg-type]
