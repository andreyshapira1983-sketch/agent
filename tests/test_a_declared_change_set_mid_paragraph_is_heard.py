"""Объявление «меняется ровно один файл: X» слышно и посреди абзаца.

2026-09-21, вечер: «Повтори то же самое. Меняется ровно один файл: X —
перезапиши его. Файл Y — только прочитать…» — ворота двусмысленности трижды
отвечали «the request mixes reading and changing over several paths»: явное
объявление узнавалось только в начале строки и только в форме «Менять:».
"""
from __future__ import annotations

from core.completion_contract import _explicit_change_targets

MSG = ("Это Claude. Повтори то же самое. Меняется ровно один файл: "
       "proposals/final.md — перезапиши его целиком. Файл proposals/addendum.md — "
       "только прочитать, его содержимое включить в final.md.")


def test_the_declared_file_is_the_only_change() -> None:
    assert _explicit_change_targets(MSG) == ("proposals/final.md",)


def test_the_old_forms_still_work() -> None:
    assert _explicit_change_targets("Менять: core/a.py") == ("core/a.py",)
    assert _explicit_change_targets("Изменить: ровно один файл — core/a.py") == ("core/a.py",)


def test_a_passing_word_is_not_a_declaration() -> None:
    assert _explicit_change_targets("Мне кажется, что меняется погода в core/a.py и core/b.py") == ()
