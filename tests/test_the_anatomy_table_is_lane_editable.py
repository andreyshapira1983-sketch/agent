"""Таблица групп анатомии живёт там, куда полоса вправе предлагать правки.

Замер, отвергнутые варианты и границы: MIR-180 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Первый одобренный раскол (2026-08-27, ain_05cf81b6…) откатился анатомическими
сторожами: новый core-модуль требует строки группировки, а таблица жила в
`scripts/gen_anatomy.py` — в каталоге, который полоса трогать не вправе. Два
честных органа воевали: сторож требовал строку, денилист запрещал её добавить.
"""
from __future__ import annotations

from pathlib import Path


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
