"""Кириллица в имени существующего файла — не выдумка планировщика.

Замер 2026-09-20. В рабочей папке агента 1556 файлов, чей путь не ASCII —
это его собственные математические черновики
(`math_study/drafts/M11/versions/2026-09-15_0-A_v1_до_рецензии/…`). Правило
`core/step_sanitizer.py` снимало КАЖДЫЙ `file_read` по такому пути: за семь
суток снято 20 шагов, и каждый раз прогон дальше бился вслепую — обход
`os.walk`, опровергнутые утверждения, `partially_achieved`.

Само правило написано против другого: «non-ASCII planner-invented
identifiers are rejected by policy» — против выдуманного имени вроде
«привет.txt», которое модель сочиняет из фразы пользователя. Различитель
здесь простой и проверяемый: выдуманного файла на диске нет, а свой
черновик — лежит. Существование в рабочей папке и решает.

Запись файла правило по-прежнему держит: имя НОВОГО файла выдумывается
всегда, и проверять там нечего.
"""
from __future__ import annotations

from pathlib import Path

from core.step_sanitizer import sanitize_step

_CYRILLIC = "math_study/drafts/M11/versions/2026-09-15_0-A_v1_до_рецензии/m11_part0.md"


def _sanitize(path: str, workspace: Path | None):
    warnings: list[str] = []
    spec = sanitize_step(
        "file_read", {"path": path}, None, 0, warnings,
        workspace=str(workspace) if workspace else None,
    )
    return spec, warnings


def test_an_existing_draft_is_readable(tmp_path: Path) -> None:
    target = tmp_path / _CYRILLIC
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("теорема", encoding="utf-8")

    spec, warnings = _sanitize(_CYRILLIC, tmp_path)
    assert spec is not None, "свой черновик агент обязан уметь открыть"
    assert spec["arguments"]["path"] == _CYRILLIC
    assert not [w for w in warnings if "not ASCII" in w]


def test_an_invented_name_is_still_dropped(tmp_path: Path) -> None:
    spec, warnings = _sanitize("привет.txt", tmp_path)
    assert spec is None, "файла нет на диске — имя выдумано"
    assert any("not ASCII" in w for w in warnings)


def test_without_a_workspace_the_old_policy_holds(tmp_path: Path) -> None:
    target = tmp_path / _CYRILLIC
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("теорема", encoding="utf-8")

    spec, _warnings = _sanitize(_CYRILLIC, None)
    assert spec is None, "без адреса папки проверить существование нечем"


def test_an_escape_is_not_excused_by_existing(tmp_path: Path) -> None:
    outside = tmp_path.parent / "секрет.txt"
    outside.write_text("x", encoding="utf-8")
    spec, _warnings = _sanitize("../секрет.txt", tmp_path)
    assert spec is None, "существование вне песочницы ничего не разрешает"
