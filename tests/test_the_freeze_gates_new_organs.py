"""Заморозка 2026-08-20 снята 2026-09-24: журнал её периода полон, дальше — свобода.

Был датчик раскрытия (Codex, 2026-08-28): новый кодовый файл после заморозки,
не названный в docs/audit/AUTONOMY_FREEZE.md, валил батарею. С 19.09 он молча
уходил в skip — история была обрезана коммитом «Baseline before the 24h
autonomous run», и базовой точки заморозки в ней не было. 24.09 полная история
нашлась на GitHub, датчик показал 23 незаписанных модуля — и в тот же день
оператор снял заморозку: «разморозку сделай полноценно».

Что проверяется теперь:
* документ говорит о снятии прямо (метка FREEZE LIFTED и слово оператора);
* журнал периода заморозки ПОЛОН: каждый кодовый файл, которого не было в
  базовой точке (1577b85, последний коммит дня заморозки) и который есть в
  коммите снятия, назван в документе. Файлы после снятия не проверяются —
  запрета больше нет.
Без истории — skip, как и раньше, но теперь история подшивается с GitHub
(`refs/history/github-main` + `git replace --graft`).
"""
from __future__ import annotations

import subprocess  # nosec B404 — fixed argv, reading our own history
from pathlib import Path

import pytest

_FREEZE_BASELINE = "1577b85"
_LIFT_MARK = "FREEZE LIFTED 2026-09-24"
_CODE_HOMES = ("core", "cli", "app", "tools", "api")
_DOC = Path("docs") / "audit" / "AUTONOMY_FREEZE.md"

_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv
            ["git", *args], cwd=str(_ROOT), capture_output=True, timeout=30, check=False,  # noqa: S607
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8") if out.returncode == 0 else None


def _py_files_at(ref: str) -> set[str] | None:
    listing = _git("ls-tree", "-r", "--name-only", ref, *_CODE_HOMES)
    if listing is None:
        return None
    names = {ln.strip() for ln in listing.splitlines()
             if ln.strip().endswith(".py") and not ln.strip().endswith("__init__.py")}
    return names or None


def _working_tree_files() -> set[str]:
    return {
        p.relative_to(_ROOT).as_posix()
        for home in _CODE_HOMES if (_ROOT / home).is_dir()
        for p in (_ROOT / home).rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "__init__.py"
    }


def _doc() -> str:
    return (_ROOT / _DOC).read_text(encoding="utf-8")


def test_the_doc_says_the_freeze_is_lifted_and_by_whose_word() -> None:
    doc = _doc()
    assert _LIFT_MARK in doc
    assert "разморозку сделай полноценно" in doc


def test_every_code_file_of_the_freeze_period_is_named_in_the_doc() -> None:
    baseline = _py_files_at(_FREEZE_BASELINE)
    if baseline is None:
        pytest.skip("git history unavailable — cannot read the freeze baseline")
    lift_commits = (_git("log", "--reverse", "--format=%H", "-S", _LIFT_MARK, "--", _DOC.as_posix()) or "").split()
    at_lift = _py_files_at(lift_commits[0]) if lift_commits else _working_tree_files()
    assert at_lift is not None
    doc = _doc()
    unrecorded = sorted(n for n in at_lift - baseline if Path(n).name not in doc)
    assert unrecorded == [], (
        "кодовые файлы периода заморозки (2026-08-20 — 2026-09-24) не записаны в "
        f"{_DOC.as_posix()}: " + ", ".join(unrecorded)
    )
