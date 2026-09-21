"""Доказанный предмет едет целиком — с классом — и в файл, названный по смыслу.

Эпизод 2026-09-21 ~16:20 (ain_bea533…, отклонено оператором): доказательство
«два предмета» в core/knowledge_pipeline.py называло класс ClaimExtractor и 13
его функций. Раскольщик перенёс функции и константы, класс оставил дома (в
кандидаты не брались классы вовсе), а новый файл назвал
knowledge_pipeline_helpers.py — имя считалось по перенесённому, где «claim»
утонул в константах.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.incremental_splitter import _NoHome, _plan_function_split, plan_incremental_split

_SRC = '''
_LIMIT = 3


def claim_id(text):
    return hash(text) % _LIMIT


def claim_text(text):
    return text.strip()


class ClaimExtractor:
    def run(self, text):
        return claim_id(claim_text(text))


def other_subject(x):
    return x * 2
'''


def _write(tmp_path, src=_SRC):
    (tmp_path / "core").mkdir(exist_ok=True)
    (tmp_path / "core" / "pipe.py").write_text(src, encoding="utf-8")


def test_the_class_moves_with_its_functions(tmp_path) -> None:
    _write(tmp_path)
    proof = SimpleNamespace(kind="multi_subject", names=("ClaimExtractor", "claim_id", "claim_text"))
    plan = plan_incremental_split(tmp_path, "core/pipe.py", proof=proof)
    assert plan.step is not None, plan.reason
    assert "ClaimExtractor" in plan.step.moved_names
    assert "other_subject" not in plan.step.moved_names
    assert plan.step.new_module == "core/pipe_claim.py"


def test_a_proven_group_without_a_common_word_is_not_called_helpers(tmp_path) -> None:
    _write(tmp_path)
    import ast
    src = (tmp_path / "core" / "pipe.py").read_text(encoding="utf-8")
    with pytest.raises(_NoHome, match="helpers"):
        _plan_function_split(tmp_path, "core/pipe.py", src, ast.parse(src), None, 400,
                             names={"other_subject", "claim_id"})


def test_a_proven_group_that_cannot_move_whole_is_refused(tmp_path) -> None:
    src = _SRC.replace("def run(self, text):", "def run(self, text, stays=None):\n        other_subject(1)")
    _write(tmp_path, src)
    import ast
    with pytest.raises(_NoHome, match="whole"):
        _plan_function_split(tmp_path, "core/pipe.py", src, ast.parse(src), None, 400,
                             names={"ClaimExtractor", "claim_id", "claim_text", "missing_name"})
