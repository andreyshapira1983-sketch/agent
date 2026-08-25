"""Смена формата строк состояния уничтожает данные, а не игнорирует их.

Замер, отвергнутые варианты и границы: H-31 в docs/audit/HISTORICAL_FAILURE_LEDGER.md.
"""
from __future__ import annotations

import json

from core.state_integrity import (
    INTEGRITY_MARKER,
    append_state_jsonl,
    quarantine_dir_for,
    read_state_jsonl,
)


def test_the_format_marker_is_still_the_one_this_measurement_was_made_on() -> None:
    assert INTEGRITY_MARKER == "agent-state-jsonl-v1", (
        "формат строк состояния сменился. Замер H-31: старый читатель не "
        "игнорирует чужую версию, а выносит её в карантин и переписывает живой "
        "файл без неё. Значит вместе с этой сменой обязан приехать читатель, "
        "принимающий ОБЕ версии, иначе первый же процесс на прежнем коде "
        "сотрёт из хранилища всё, что записал новый. Обновите tests и запись "
        "H-31, а не только константу."
    )


def test_a_row_from_a_future_format_is_stripped_from_the_live_file(tmp_path) -> None:
    """Доказательство внутри растяжки: иначе она была бы просто надписью."""
    store = tmp_path / "s.jsonl"
    append_state_jsonl(store, [{"kind": "старая", "n": 1}])

    row = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    future = {**row, "_integrity": {**row["_integrity"], "format": "agent-state-jsonl-v2"}}
    with store.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(future, ensure_ascii=False) + "\n")
    assert len(store.read_text(encoding="utf-8").splitlines()) == 2

    got = read_state_jsonl(store)

    assert len(got) == 1, "строка из будущего принята старым читателем как своя"
    assert len(store.read_text(encoding="utf-8").splitlines()) == 1, (
        "живой файл сохранил строку — тогда чтение не разрушительно и "
        "растяжку надо переписать под новое поведение"
    )
    qdir = quarantine_dir_for(store)
    assert qdir.exists() and list(qdir.iterdir()), (
        "строка исчезла и не попала в карантин — это уже потеря улики"
    )
