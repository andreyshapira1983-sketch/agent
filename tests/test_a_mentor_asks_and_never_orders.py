"""Канал наставника: вопросы, а не ответы; совещательно, а не приказом.

Замер, отвергнутые варианты и границы: MIR-178 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Форма задана оператором: «задавать ему вопросы и пытаться его учить, при этом
не вмешиваясь в его работу». Значит вопрос кладётся туда, где он выбирает цели,
с явно низшей властью: он вправе отклонить, и отказ сам по себе сигнал.
"""
from __future__ import annotations

from pathlib import Path

from core.mentor_channel import append_question, mentor_block, open_questions


def test_a_question_survives_the_round_trip(tmp_path: Path) -> None:
    """Красный свидетель: канала не существовало — вопросу некуда было лечь."""
    append_question(tmp_path, "почему действие X стоит вдвое дороже Y?")

    got = open_questions(tmp_path)

    assert len(got) == 1
    assert got[0].question.startswith("почему действие X")
    assert got[0].id


def test_the_block_declares_itself_advisory() -> None:
    """Власть названа в самом блоке: совещательно, отклонить можно.

    Отвергнут вариант «вопрос становится целью автоматически»: это был бы
    приказ в костюме вопроса, и вся разница с вмешательством исчезла бы.
    """
    from core.mentor_channel import MentorQuestion

    block = mentor_block((MentorQuestion(id="mq_1", ts="t", question="вопрос?"),))

    assert "<mentor_questions>" in block
    assert "advisory" in block
    assert "free to decline" in block


def test_no_questions_no_block(tmp_path: Path) -> None:
    """Пустой канал не занимает подсказку."""
    assert mentor_block(()) == ""
    assert open_questions(tmp_path) == ()


def test_the_block_is_bounded() -> None:
    """Подсказка стоит денег — и у наставника нет права на монолог."""
    from core.mentor_channel import MentorQuestion

    many = tuple(
        MentorQuestion(id=f"mq_{i}", ts="t", question="оч длинный вопрос " * 10)
        for i in range(50)
    )

    assert len(mentor_block(many, max_chars=900)) <= 900


def test_the_goal_chooser_reads_the_channel(tmp_path: Path) -> None:
    """Проводка: вопросы доходят до выбора цели тем же путём, что вердикты.

    Пин по смыслу: имя связано в модуле выбора и вопросы входят в подсказку.
    """
    import inspect

    from core import charter_goal as mod

    assert callable(getattr(mod, "open_questions", None))
    src = inspect.getsource(mod)
    assert "mentor_block" in src
    ask_src = inspect.getsource(mod._ask)
    assert "mentor_questions" in ask_src
    assert "if mentor_questions:" in ask_src


def test_a_broken_channel_is_silence_not_a_crash(tmp_path: Path) -> None:
    """Битый файл канала не роняет выбор цели — и не выдумывает вопросов."""
    path = tmp_path / "data" / "mentor_questions.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("{битый json\n", encoding="utf-8")

    assert open_questions(tmp_path) == ()


def test_the_decision_row_says_how_many_questions_were_shown(tmp_path: Path) -> None:
    """«Видел и отклонил» обязано отличаться от «не видел».

    Тик 15:31 (2026-08-27) выбрал цель из бэклога, и по журналу было НЕЛЬЗЯ
    сказать, видел ли выбор вопрос наставника: показ нигде не записывался.
    Отсутствие ключа = ноль, как у всех причинных ключей.
    """
    import json

    from core.charter_goal import DECISIONS_RELPATH, _record_decision

    _record_decision(tmp_path, status="proposed", goal="g",
                     mentor_questions_shown=2)
    _record_decision(tmp_path, status="proposed", goal="g2")

    rows = [json.loads(line)["payload"] if "payload" in line else json.loads(line)
            for line in (tmp_path / DECISIONS_RELPATH).read_text(
                encoding="utf-8").splitlines() if line.strip()]
    assert rows[0].get("mentor_questions_shown") == 2
    assert "mentor_questions_shown" not in rows[1]
