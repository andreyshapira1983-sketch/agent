"""Путь без человека не ослабляет своих судей и не закрывает дефект без свидетеля.

Проверка части 6 (2026-09-25), по источникам, открытым до правки:
* Darwin Gödel Machine (Sakana, 2025): в части прогонов агент снял метки, по
  которым детектор ловил подделку, и получил ложный «успех» — оптимизатор
  дотягивается до собственного судьи;
* автоматическое исправление программ: «правдоподобная» правка (тесты зелёные)
  не обязательно «верная» — набор тестов слабее задачи (переобучение правки);
* Agentless / SWE-bench: воспроизводящий тест падает ДО правки.

`core/patch_route.py` ставит правку в main без человека. До 25.09 он принимал
правку одних тестов (свидетель не запускался) и не мерил, стали ли старые
тесты меньше: честный новый тест прикрывал ослабленный старый.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from core.patch_route import settle_patch, weakened_judges
from tests.test_the_agent_repairs_itself_without_a_human import _repo

_GOAL = "Файл proposals/selffix/d/edits.txt создан"


def _patch(root: Path, text: str) -> None:
    path = root / "proposals" / "selffix" / "d" / "edits.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(root: Path, rel: str, content: str) -> None:
    (root / rel).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)  # noqa: S607
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "setup"],  # noqa: S607
                   cwd=root, check=True)


def test_a_patch_of_tests_alone_does_not_close_a_defect(tmp_path: Path) -> None:
    """Сделать красный тест зелёным, ослабив его, — не починка."""
    root = _repo(tmp_path)
    _commit(root, "tests/test_mod.py", "from core.mod import f\n\n\ndef test_f():\n    assert f() == 2\n")
    _patch(root, "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n    assert f() == 2\n=======\n"
                 "    assert f() in (1, 2)\n>>>>>>> REPLACE\n")
    verdict = settle_patch(None, root, _GOAL)
    assert verdict["verdict"] == "missing", verdict
    assert "f() == 2" in (root / "tests" / "test_mod.py").read_text(encoding="utf-8")


def test_an_honest_new_test_does_not_cover_a_weakened_old_one(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _commit(root, "tests/test_ok.py",
            "def test_ok():\n    assert True\n\n\ndef test_other():\n    assert 1 + 1 == 2\n")
    _patch(root, "FILE: core/mod.py\n<<<<<<< LINES 2-2\n    return 2\n>>>>>>> REPLACE\n"
                 "FILE: tests/test_mod.py\n<<<<<<< SEARCH\n=======\nfrom core.mod import f\n\n\n"
                 "def test_f():\n    assert f() == 2\n>>>>>>> REPLACE\n"
                 "FILE: tests/test_ok.py\n<<<<<<< SEARCH\n\n\ndef test_other():\n    assert 1 + 1 == 2\n"
                 "=======\n>>>>>>> REPLACE\n")
    verdict = settle_patch(None, root, _GOAL)
    assert verdict["verdict"] == "missing", verdict
    assert "ослабляет" in verdict["reason"] and "tests/test_ok.py" in verdict["reason"]
    assert "return 1" in (root / "core" / "mod.py").read_text(encoding="utf-8")


def test_a_skip_mark_is_weakening_too(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    after = "import pytest\n\n\n@pytest.mark.skip\ndef test_ok():\n    assert True\n"
    assert weakened_judges(root, {"tests/test_ok.py": after}) == ["tests/test_ok.py"]


def test_adding_tests_is_not_weakening(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    after = "def test_ok():\n    assert True\n\n\ndef test_more():\n    assert 2 > 1\n"
    assert weakened_judges(root, {"tests/test_ok.py": after,
                                  "tests/test_new.py": "def test_n():\n    assert 1\n"}) == []
