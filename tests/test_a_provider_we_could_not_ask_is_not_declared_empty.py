"""Поставщика, которого не смогли спросить, нельзя объявлять пустым.

Замер, отвергнутые варианты и границы: MIR-170 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import model_catalog as mc

_ANTHROPIC = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]
_OPENAI = ["gpt-5.6-terra", "gpt-4o-mini"]


@pytest.fixture()
def catalog_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Каталог во временной папке: живой файл конфигурации тест не трогает."""
    path = tmp_path / "model_catalog.json"
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(path))
    return path


def _seed(path: Path) -> None:
    """Каталог, где ОБА поставщика уже известны."""
    mc._save_catalog(mc.discover_catalog(["anthropic", "openai"]))
    assert json.loads(path.read_text(encoding="utf-8"))["providers"]["anthropic"]["models"]


def _fetchers(monkeypatch: pytest.MonkeyPatch, *, anthropic, openai) -> None:
    def _wrap(value):
        def _fetch(_key=None):
            if isinstance(value, Exception):
                raise value
            return list(value)
        return _fetch

    monkeypatch.setitem(mc._FETCHERS, "anthropic", _wrap(anthropic))
    monkeypatch.setitem(mc._FETCHERS, "openai", _wrap(openai))


def test_a_provider_that_could_not_be_asked_keeps_its_models(
    catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Красный свидетель: живой каталог, 2026-08-27.

    Ключ anthropic существует, но не оплачен, поэтому запрос списка моделей
    падает. Обновление пропускало такого поставщика и сохраняло каталог целиком
    — и его **10 моделей превращались в 0**. Отсутствие улики записывалось как
    улика отсутствия.

    Цена не бумажная: без моделей у поставщика не находится равный по уровню, и
    переход к нему скатывается на дефолтную модель — тот самый спуск, который
    замерили 15 августа (73 раза за сутки).
    """
    _fetchers(monkeypatch, anthropic=_ANTHROPIC, openai=_OPENAI)
    _seed(catalog_path)

    _fetchers(monkeypatch, anthropic=RuntimeError("401 no credit"), openai=_OPENAI)
    mc.refresh_catalog(["anthropic", "openai"])

    saved = json.loads(catalog_path.read_text(encoding="utf-8"))["providers"]
    assert [m["id"] for m in saved["anthropic"]["models"]] == _ANTHROPIC, (
        "поставщик, которого не смогли спросить, объявлен пустым"
    )


def test_the_carried_over_entry_says_that_it_is_old(
    catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сохранить — не значит выдать за свежее.

    Молчаливый перенос читался бы как «спросили, всё на месте». Запись обязана
    называть себя перенесённой, иначе непроверенное выдаётся за проверенное.
    """
    _fetchers(monkeypatch, anthropic=_ANTHROPIC, openai=_OPENAI)
    _seed(catalog_path)

    _fetchers(monkeypatch, anthropic=RuntimeError("401 no credit"), openai=_OPENAI)
    mc.refresh_catalog(["anthropic", "openai"])

    saved = json.loads(catalog_path.read_text(encoding="utf-8"))["providers"]
    assert saved["anthropic"].get("carried_over") is True
    assert saved["anthropic"].get("carried_reason")
    assert "carried_over" not in saved["openai"]


def test_an_answered_empty_list_is_a_real_answer(
    catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Контроль: «спросили и получили пусто» — это ответ, и его надо записать.

    Отвергнут вариант «переносить всегда, когда моделей нет»: тогда поставщик,
    честно снявший все модели, оставался бы в каталоге навсегда.
    """
    _fetchers(monkeypatch, anthropic=_ANTHROPIC, openai=_OPENAI)
    _seed(catalog_path)

    _fetchers(monkeypatch, anthropic=[], openai=_OPENAI)
    mc.refresh_catalog(["anthropic", "openai"])

    saved = json.loads(catalog_path.read_text(encoding="utf-8"))["providers"]
    assert saved["anthropic"]["models"] == []
    assert "carried_over" not in saved["anthropic"]


def test_an_unknown_provider_that_fails_stays_absent(
    catalog_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переносить нечего, если прежде записи не было — выдумывать нельзя."""
    _fetchers(monkeypatch, anthropic=RuntimeError("401 no credit"), openai=_OPENAI)
    mc.refresh_catalog(["anthropic", "openai"])

    saved = json.loads(catalog_path.read_text(encoding="utf-8"))["providers"]
    assert "anthropic" not in saved
    assert saved["openai"]["models"]
