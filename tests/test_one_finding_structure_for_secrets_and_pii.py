"""Одна структура находки для секретов и личных данных.

Заявка агента proposals/2026-09-21_dedupe_secretfinding_into_dlp.md: SecretFinding
в core/secret_scanner.py дословно повторял DlpFinding из core/dlp.py. Кампания
2026-09-22 шесть раз подряд брала цель «свести повтор» и не могла её выполнить —
живой код ей менять нельзя; сделано Claude по заявке агента.
"""
from __future__ import annotations

import ast
import inspect

import core.secret_scanner as scanner
from core.dlp import DlpFinding
from core.secret_scanner import SecretFinding


def test_the_old_name_is_the_same_class() -> None:
    assert SecretFinding is DlpFinding


def test_secret_scanner_no_longer_defines_its_own_copy() -> None:
    tree = ast.parse(inspect.getsource(scanner))
    assert not [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SecretFinding"]
