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
