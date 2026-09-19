"""Протухший каталог обновляется ДО подстройки маршрутов, а не вместо неё.

ПОСТАНОВЛЕНИЕ ОПЕРАТОРА 2026-08-12 (R7, probe_r1): автономный агент сам
обслуживает свежесть своего каталога моделей. Порядок обязателен: обновление →
проверка → маршрутизация. Протухший список — сигнал «сначала обнови», а не
разрешение молча подстроиться под зашитые дефолты.

ЖИВОЙ СЛУЧАЙ, доказанный полным сканом журналов: каталог с ПРАВИЛЬНЫМ
`standard: claude-sonnet-5` умер по TTL (15 дней > 7), `tier_model_for`
вернул пустоту, запасная ветка разрешилась ПЕРВЫМ спеком реестра — а реестр
собирается «встроенные сперва» — и все 34 вызова probe_r1 ушли на зашитые
claude-sonnet-4-5 / gpt-4o-mini. Ноль вызовов настроенной модели; заголовок
сессии при этом рекламировал sonnet-5. Конфигурация стала декорацией.

Здесь закрепляются ОБА ребра разрыва:
  1) смерть каталога вызывает ОДНУ попытку самообновления (с ключами, с
     выключателем, без сети в тестах — conftest держит его выключенным);
  2) «объявленная оператором модель» ищется в операторских спеках раньше
     встроенных — подпись `source="builtin"` не имеет права затенять конфиг.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import core.model_catalog as mc
from core.model_router import (
    ModelRegistry,
    ModelRouter,
    ModelSelectionPolicy,
    ModelSpec,
)
from core.task_complexity import ComplexityTier


def _write_catalog(path: Path, *, age_days: int, standard: str) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    path.write_text(json.dumps({
        "updated_at": stamp,
        "providers": {
            "anthropic": {"tier_best": {
                "light": "claude-haiku-4-5-20251001",
                "standard": standard,
                "deep": "claude-opus-5",
            }},
        },
    }), encoding="utf-8")


@pytest.fixture()
def stale_catalog(tmp_path: Path, monkeypatch) -> Path:
    """Каталог с верным содержимым и мёртвым штампом — форма живого случая."""
    cat = tmp_path / "model_catalog.json"
    _write_catalog(cat, age_days=15, standard="claude-sonnet-5")
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(cat))
    monkeypatch.setattr(mc, "_AUTOREFRESH_DONE", False, raising=False)
    return cat


def test_expired_catalog_refreshes_then_routes(stale_catalog, monkeypatch) -> None:
    """ГЛАВНОЕ: сначала обновление, потом маршрут — и модель из конфига."""
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # предпосылка теста, не машины
    calls: list = []

    def fake_refresh(providers=None, api_keys=None):
        calls.append(tuple(providers or ()))
        _write_catalog(stale_catalog, age_days=0, standard="claude-sonnet-5")
        return {}

    monkeypatch.setattr(mc, "refresh_catalog", fake_refresh)
    got = mc.tier_model_for(ComplexityTier.STANDARD, "anthropic")
    assert calls == [("anthropic",)], "обновление обязано случиться ДО ответа"
    assert got == "claude-sonnet-5", got


def test_the_attempt_happens_once_per_process(stale_catalog, monkeypatch) -> None:
    """Неудачное обновление не превращается в молотьбу по сети на каждый вызов."""
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-real-key")
    calls: list = []

    def failing_refresh(providers=None, api_keys=None):
        calls.append(1)
        raise ConnectionError("provider down")

    monkeypatch.setattr(mc, "refresh_catalog", failing_refresh)
    assert mc.tier_model_for(ComplexityTier.STANDARD, "anthropic") == ""
    assert mc.tier_model_for(ComplexityTier.STANDARD, "anthropic") == ""
    assert len(calls) == 1, "вторая смерть каталога не даёт второй попытки"


def test_no_credentials_means_no_attempt(stale_catalog, monkeypatch) -> None:
    """Без ключей не бывает сети: попытка не делается вовсе."""
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def must_not_run(providers=None, api_keys=None):
        raise AssertionError("refresh_catalog вызван без учётных данных")

    monkeypatch.setattr(mc, "refresh_catalog", must_not_run)
    assert mc.tier_model_for(ComplexityTier.STANDARD, "anthropic") == ""


def test_the_killswitch_holds(stale_catalog, monkeypatch) -> None:
    """AGENT_CATALOG_AUTOREFRESH=0 — оператор запретил; suite живёт этим же."""
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-real-key")

    def must_not_run(providers=None, api_keys=None):
        raise AssertionError("refresh_catalog вызван при выключателе 0")

    monkeypatch.setattr(mc, "refresh_catalog", must_not_run)
    assert mc.tier_model_for(ComplexityTier.STANDARD, "anthropic") == ""


def test_a_fresh_catalog_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    """ПРЕДОХРАНИТЕЛЬ: свежий каталог не трогают."""
    cat = tmp_path / "model_catalog.json"
    _write_catalog(cat, age_days=0, standard="claude-sonnet-5")
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(cat))
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-real-key")
    monkeypatch.setattr(mc, "_AUTOREFRESH_DONE", False, raising=False)

    def must_not_run(providers=None, api_keys=None):
        raise AssertionError("свежий каталог обновлять незачем")

    monkeypatch.setattr(mc, "refresh_catalog", must_not_run)
    assert mc.tier_model_for(ComplexityTier.STANDARD, "anthropic") == "claude-sonnet-5"


def _router(registry: ModelRegistry, *, max_cost: str | None = None) -> ModelRouter:
    def factory(provider, model):
        class _L:
            pass
        llm = _L()
        llm.provider, llm.model = provider, model
        return llm

    return ModelRouter(
        default_provider="anthropic",
        default_model="claude-sonnet-5",
        llm_factory=factory,
        registry=registry,
        selection_policy=ModelSelectionPolicy(
            name="offline", allow_mock=True, max_cost_tier=max_cost
        ),
    )


def test_operator_registry_outranks_builtin_defaults() -> None:
    """Второе ребро: подпись builtin не затеняет операторский конфиг.

    Ровно порядок живого реестра: встроенный спек первым, операторский — за
    ним. «Объявленная оператором модель» обязана быть операторской.
    """
    registry = ModelRegistry([
        ModelSpec(id="anthropic-default", provider="anthropic",
                  model="claude-sonnet-4-5", source="builtin"),
        ModelSpec(id="planner-balanced", provider="anthropic",
                  model="claude-sonnet-5", source="file:config/model_registry.json"),
    ])
    router = _router(registry)
    assert router._declared_model_for("anthropic") == "claude-sonnet-5"


def test_the_declared_model_respects_the_cost_ceiling() -> None:
    """Замер живой регрессии 2026-08-12: сняв тень builtin, первым операторским
    спеком оказался frontier-Opus, который реестр сознательно держит за
    потолком («raise the ceiling to release it»). Объявленной моделью обязана
    быть та, которую позволено звать."""
    registry = ModelRegistry([
        ModelSpec(id="anthropic-default", provider="anthropic",
                  model="claude-sonnet-4-5", source="builtin",
                  cost_tier="medium"),
        ModelSpec(id="planner-frontier", provider="anthropic",
                  model="claude-opus-4-5-20251101",
                  source="file:config/model_registry.json", cost_tier="high"),
        ModelSpec(id="planner-balanced", provider="anthropic",
                  model="claude-sonnet-5",
                  source="file:config/model_registry.json", cost_tier="medium"),
    ])
    router = _router(registry, max_cost="medium")
    assert router._declared_model_for("anthropic") == "claude-sonnet-5"


def test_builtin_still_answers_when_it_is_all_there_is() -> None:
    """ПРЕДОХРАНИТЕЛЬ: без операторских спеков встроенный остаётся ответом."""
    registry = ModelRegistry([
        ModelSpec(id="anthropic-default", provider="anthropic",
                  model="claude-sonnet-4-5", source="builtin"),
    ])
    router = _router(registry)
    assert router._declared_model_for("anthropic") == "claude-sonnet-4-5"


def test_the_reason_confesses_a_declared_fallback(monkeypatch, tmp_path) -> None:
    """Журнал обязан отличать модель из каталога от модели-запаски.

    В probe_r1 обе выглядели одинаково: `complexity:standard:anthropic` — и
    протухание было невидимым. Причина обязана называть источник модели.
    """
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setenv("AGENT_CATALOG_AUTOREFRESH", "0")
    monkeypatch.setattr(mc, "_AUTOREFRESH_DONE", False, raising=False)
    monkeypatch.setenv("AGENT_TIER_PROVIDERS_STANDARD", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-not-a-real-key")

    registry = ModelRegistry([
        ModelSpec(id="planner-balanced", provider="anthropic",
                  model="claude-sonnet-5", source="file:config/model_registry.json"),
    ])
    router = _router(registry)
    provider, reason, _skipped = router._resolve_tier_provider(
        ComplexityTier.STANDARD, "planner"
    )
    assert provider == "anthropic"
    assert "model_source:declared" in (reason or ""), reason
