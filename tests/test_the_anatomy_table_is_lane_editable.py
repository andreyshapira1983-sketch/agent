"""Таблица групп анатомии живёт там, куда полоса вправе предлагать правки.

Замер, отвергнутые варианты и границы: MIR-180 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Первый одобренный раскол (2026-08-27, ain_05cf81b6…) откатился анатомическими
сторожами: новый core-модуль требует строки группировки, а таблица жила в
`scripts/gen_anatomy.py` — в каталоге, который полоса трогать не вправе. Два
честных органа воевали: сторож требовал строку, денилист запрещал её добавить.
"""
from __future__ import annotations

import ast
from pathlib import Path

from core.self_apply_lane import FileChange, classify_patch_risk


def test_the_table_lives_where_the_lane_may_propose() -> None:
    """Красный свидетель: сама точка переезда.

    `core/anatomy_groups.py` полоса принимает; `scripts/gen_anatomy.py` — нет,
    и это правильно остаётся так: скрипты — не поверхность самосборки.
    """
    ok_core, _, _ = classify_patch_risk(
        (FileChange(path="core/anatomy_groups.py", content="GROUPS = []\n"),)
    )
    ok_scripts, _, _ = classify_patch_risk(
        (FileChange(path="scripts/gen_anatomy.py", content="x = 1\n"),)
    )

    assert ok_core is True
    assert ok_scripts is False


def test_one_table_no_copies() -> None:
    """Генератор читает таблицу ИЗ core-файла; второй литерал разошёлся бы.

    Чтение — AST-разбором, без импорта: правило скриптов «не исполнять код
    агента» сохраняется, потому что literal_eval исполняет литерал, а не модуль.
    """
    import sys
    sys.path.insert(0, "scripts")
    try:
        import gen_anatomy
    finally:
        sys.path.pop(0)
    import core.anatomy_groups as groups_mod

    # Читаем через модуль, не литеральным путём: переезд файла не должен
    # краснить тест о другом (правило test_no_test_pins_a_production_path).
    src = Path(gen_anatomy.__file__).read_text(encoding="utf-8")
    assert "Interface & Interaction" not in src, "в скрипте остался свой литерал"
    assert any("Interface & Interaction" in g[0] for g in gen_anatomy.GROUPS)
    # И сама таблица — чистый литерал, пригодный для literal_eval.
    tree = ast.parse(Path(groups_mod.__file__).read_text(encoding="utf-8"))
    assigns = [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))]
    assert assigns, "в core/anatomy_groups.py нет присваивания GROUPS"


def test_a_split_proposal_carries_its_own_grouping_line(tmp_path: Path) -> None:
    """Расщепитель вкладывает строку группировки в СВОЁ ЖЕ предложение.

    Правило детерминированное: новый модуль наследует группу исходного.
    Семантику расщепитель знать не может, а происхождение — знает точно.
    """
    from core.self_build_producer import _sync_anatomy_groups

    groups_src = (
        'GROUPS = [\n'
        '    ("Model Management", "routing", [\n'
        '        "model_router",\n'
        '        "model_catalog",\n'
        '    ]),\n'
        ']\n'
    )
    build = {"files": [
        {"path": "core/model_router.py", "content": "x = 1\n"},
        {"path": "core/model_router_helpers.py", "content": "y = 2\n"},
    ]}

    _sync_anatomy_groups(build, "core/model_router.py",
                         lambda p: groups_src if "anatomy_groups" in p else None)

    paths = [f["path"] for f in build["files"]]
    assert "core/anatomy_groups.py" in paths
    new_src = next(f["content"] for f in build["files"]
                   if f["path"] == "core/anatomy_groups.py")
    assert '"model_router_helpers",' in new_src
    idx_src = new_src.index('"model_router",')
    idx_new = new_src.index('"model_router_helpers",')
    assert idx_new > idx_src, "новый модуль должен встать в группу исходного"


def test_an_unknown_source_module_adds_nothing_silent(tmp_path: Path) -> None:
    """Исходника нет в таблице — строку не выдумываем: сторож поймает и скажет."""
    from core.self_build_producer import _sync_anatomy_groups

    build = {"files": [
        {"path": "core/nowhere.py", "content": "x = 1\n"},
        {"path": "core/nowhere_helpers.py", "content": "y = 2\n"},
    ]}

    _sync_anatomy_groups(build, "core/nowhere.py", lambda p: 'GROUPS = []\n')

    assert "core/anatomy_groups.py" not in [f["path"] for f in build["files"]]
