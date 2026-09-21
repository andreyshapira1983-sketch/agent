"""Угаданный режим «разбор файлов» не вправе отказать во всём вопросе.

2026-09-21, 05:31. Письмо агенту было спором о мере соответствия и
упоминало два ещё не существующих тестовых файла — те, которые агент сам
предложил создать (core/big_cohesive.py и core/borrowed.py), — а где-то в
середине стояло «Сравни со своим утренним ответом». Два пути плюс глагол
сравнения в любом месте текста — и `prepare_multi_file_review` решил, что
это просьба прочитать эти файлы. Оба не нашлись, и вопрос целиком получил
отказ: «Multi-file review could not start because no valid workspace files
passed preflight». Прогон умер за три секунды. То же письмо без путей прошло.

Следствие было хуже самого отказа: агента нельзя было попросить СОЗДАТЬ файл,
потому что имя создаваемого файла убивало вопрос.

Отказ во всём вопросе остаётся там, где режим НАЗВАН оператором словами:
он просил именно разбор, и честно сказать «читать нечего». Там, где режим
угадан по глаголу, отсутствие всех названных файлов — довод против догадки,
и вопрос идёт обычным путём.
"""
from __future__ import annotations

from pathlib import Path

from core.file_request_intent import prepare_multi_file_review

_LOGGED: list[tuple[str, dict]] = []


def _log(event: str, payload: dict) -> None:
    _LOGGED.append((event, payload))


def _ws(tmp_path: Path, *names: str) -> Path:
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    return tmp_path


_THE_LETTER = (
    "Твоя правка на A не работает. Падающий тест ты не дал. Сравни со своим "
    "утренним ответом: там ты построил настоящую основу — core/big_cohesive.py "
    "на 1300 строк и core/borrowed.py на 400."
)


def test_a_guessed_review_of_missing_files_is_not_a_refusal(tmp_path) -> None:
    verdict = prepare_multi_file_review(
        _THE_LETTER, file_hint=None, workspace_root=_ws(tmp_path), log=_log,
    )
    assert verdict["kind"] == "none", verdict


def test_the_fallback_is_recorded(tmp_path) -> None:
    _LOGGED.clear()
    prepare_multi_file_review(
        _THE_LETTER, file_hint=None, workspace_root=_ws(tmp_path), log=_log,
    )
    assert any(event == "multi_file_review_guess_dropped" for event, _ in _LOGGED)


def test_a_named_review_of_missing_files_is_still_refused(tmp_path) -> None:
    verdict = prepare_multi_file_review(
        "multi-file review: сравни core/big_cohesive.py и core/borrowed.py",
        file_hint=None, workspace_root=_ws(tmp_path), log=_log,
    )
    assert verdict["kind"] == "refusal"


def test_a_guessed_review_with_real_files_still_forces_the_reads(tmp_path) -> None:
    verdict = prepare_multi_file_review(
        "сравни a.md и b.md", file_hint=None,
        workspace_root=_ws(tmp_path, "a.md", "b.md"), log=_log,
    )
    assert verdict["kind"] == "forced"
