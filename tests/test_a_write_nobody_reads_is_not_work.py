"""Запись, которую никто не прочтёт, работой не является.

Вечер 2026-09-20/21, четыре случая подряд одной формы. Агент клал ВЕРНОЕ
содержание в склад, которого никто не открывает:

  1. «находку о себе надо в `data/source_registry.jsonl`» — там реестр
     внешних источников, и записи о себе туда не идут;
  2. завёл `data/defect_registry.jsonl` — файла не существовало, читателя
     у него нет;
  3. завёл `data/judgements.jsonl` — то же самое;
  4. исправив решение, снова положил его в `defect_registry.jsonl`, спутав
     свой выдуманный «реестр дефектов» с настоящим
     `self_improvement_issues.jsonl`.

Каждый раз инструмент отвечал `appended: True`. Запись была, работы не было.

Правило агент знал и повторял вслух сам: склад определяется ЧИТАТЕЛЕМ, а не
названием. Но знание жило в разговоре, а рука писала по названию — и
разговор кончался вместе с ходом. Поэтому знание переехало к самой руке.

Тот же вечер дал ещё два молчаливых отказа, и оба теперь громкие:

  * запись без непустого `fingerprint` читатель реестра отбрасывает молча —
    высокая тяжесть, которую агент честно себе поставил, не доехала;
  * строка в поле, которое читается списком, раскладывается ПОСИМВОЛЬНО:
    улика из 105 знаков стала 105 элементами, первый — буква «d»;
  * решение о собственном весе было записано полем
    `decision: PENDING — filled from the measured file contents in this turn` —
    запись завели, чтобы прибор перестал врать молча, и она молчала о самом
    решении.

Инструмент по-прежнему пишет куда просят: запрещать незнакомый журнал
нельзя, их заводят и законно. Но теперь он ГОВОРИТ, кто это прочтёт, — а
когда читателя нет, говорит и это.
"""
from __future__ import annotations

import pytest

from tools.journal_append import JournalAppendTool


@pytest.fixture()
def tool(tmp_path):
    (tmp_path / "data").mkdir()
    return JournalAppendTool(workspace_root=tmp_path)


def test_the_result_names_the_reader(tool) -> None:
    out = tool.run(path="data/self_improvement_issues.jsonl",
                   record={"fingerprint": "probe-cwd", "title": "проба во временной папке"})
    assert "campaign_io" in out["reader"]
    assert "warning" not in out


def test_a_journal_nobody_reads_says_so(tool) -> None:
    out = tool.run(path="data/judgements.jsonl", record={"decision": "не трогать веса"})
    assert out["appended"] is True, "писать не запрещено: журналы заводят и законно"
    assert out["reader"] is None
    assert "никто не читает" in out["warning"]
    assert "self_improvement_issues.jsonl" in out["warning"], "назови те, у кого читатель есть"


def test_a_record_the_reader_would_drop_is_refused(tool) -> None:
    """Без отпечатка читатель реестра отбрасывает запись молча."""
    with pytest.raises(ValueError, match="fingerprint"):
        tool.run(path="data/self_improvement_issues.jsonl",
                 record={"title": "проба во временной папке", "severity": "high"})


def test_a_string_where_a_list_is_read_is_refused(tool) -> None:
    """Строка в поле-списке рассыпается посимвольно."""
    with pytest.raises(ValueError, match="посимвольно"):
        tool.run(path="data/self_improvement_issues.jsonl",
                 record={"fingerprint": "x", "title": "y", "evidence": "одна строка улики"})


def test_a_placeholder_field_is_refused(tool) -> None:
    """`PENDING` — это дырка, а не решение."""
    with pytest.raises(ValueError, match="заглушкой"):
        tool.run(path="data/judgements.jsonl",
                 record={"topic": "веса", "decision": "PENDING — filled in this turn"})


def test_an_honest_record_still_goes_through(tool) -> None:
    out = tool.run(path="data/self_improvement_issues.jsonl",
                   record={"fingerprint": "probe-cwd-high", "title": "прибор врёт молча",
                           "evidence": ["проба живёт во временной папке"],
                           "related_files": ["tools/python_probe.py"], "severity": "high"})
    assert out["appended"] is True
    assert out["reader"]
