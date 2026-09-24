"""The agent can see its own models (operator's word 2026-09-04: «ставь глаз»).

Measured before: no block of the unattended context named a provider or a
model; `:models` existed only for the operator; the agent learned a key
existed only when it died («no balance» three times, router moved on). Three
keys configured, one visible — not locked, unseen. The roster shows what the
router, the catalog and the ledgers already know. It never switches and
never prints a key.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.model_roster import model_roster, model_roster_block

_NOW = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
_ENV = {
    "OPENAI_API_KEY": "sk-secret-value-never-shown",
    "DEEPSEEK_API_KEY": "ds-secret",
    "AGENT_PLANNER_PROVIDER": "openai",
    "AGENT_SYNTHESIZER_PROVIDER": "openai",
    "AGENT_PROVIDER": "deepseek",
}


class _Ledger:
    """Rows as the usage ledger returns them, newest last."""

    def __init__(self, rows: dict[str, list[dict]], unhealthy: dict[str, str | None]):
        self.rows, self.unhealthy = rows, unhealthy
        self.budget_ledger = _Budget()

    def _recent_rows_for(self, provider, *, limit=40):
        return self.rows.get(provider, [])[-limit:]

    def provider_unhealthy(self, provider, *, now=None):
        return self.unhealthy.get(provider)


class _Budget:
    def snapshot(self, *, now=None):
        return {"windows": [{"name": "day", "counters": {
            "model_cost_units": {"used": 2287, "limit": 3000}}}]}


def _rows():
    return {
        "openai": [
            {"status": "error", "error": "credit balance is too low", "completed_at": "2026-09-04T16:00:00+00:00",
             "cost_tier": "high", "cost_units": 0, "total_tokens": 0},
        ],
        "deepseek": [
            {"status": "success", "completed_at": "2026-09-04T12:00:00+00:00", "cost_tier": "low",
             "cost_units": 10, "total_tokens": 9000},
            {"status": "success", "completed_at": "2026-09-03T12:00:00+00:00", "cost_tier": "low",
             "cost_units": 99, "total_tokens": 99000},
        ],
    }


def test_the_roster_names_every_provider_and_its_key_presence_without_the_value() -> None:
    roster = model_roster(usage_ledger=_Ledger(_rows(), {}), env=_ENV, now=_NOW)
    by = {p["provider"]: p for p in roster["providers"]}

    assert set(by) == {"openai", "anthropic", "deepseek", "huggingface", "local", "google"}
    assert by["openai"]["key_present"] and by["deepseek"]["key_present"]
    assert not by["anthropic"]["key_present"], "the third key is absent and must be shown as absent"
    text = model_roster_block(roster)
    assert "sk-secret" not in text and "ds-secret" not in text


def test_roles_are_read_the_way_the_router_reads_them() -> None:
    roster = model_roster(usage_ledger=_Ledger(_rows(), {}), env=_ENV, now=_NOW)
    by = {p["provider"]: p for p in roster["providers"]}

    assert by["openai"]["roles"] == ("planner", "synthesizer")
    assert by["deepseek"]["roles"] == ("repair", "memory", "verifier"), "the default provider takes the unassigned roles"
    assert by["anthropic"]["roles"] == ()


def test_health_and_the_last_error_class_come_from_the_ledger() -> None:
    ledger = _Ledger(_rows(), {"openai": "3_consecutive_key_errors_within_20m"})
    roster = model_roster(usage_ledger=ledger, env=_ENV, now=_NOW)
    by = {p["provider"]: p for p in roster["providers"]}

    assert by["openai"]["health"] == "unhealthy" and "20m" in by["openai"]["unhealthy_reason"]
    assert by["openai"]["last_error_class"] == "billing"
    assert by["deepseek"]["health"] == "healthy" and by["deepseek"]["last_error_class"] == ""


def test_spend_today_counts_only_today() -> None:
    roster = model_roster(usage_ledger=_Ledger(_rows(), {}), env=_ENV, now=_NOW)
    by = {p["provider"]: p for p in roster["providers"]}

    assert by["deepseek"]["calls_today"] == 1
    assert by["deepseek"]["cost_units_today"] == 10 and by["deepseek"]["tokens_today"] == 9000
    assert by["deepseek"]["cost_tiers_seen"] == ("low",)


def test_the_day_ceiling_and_its_remainder_are_shown() -> None:
    ledger = _Ledger(_rows(), {})
    roster = model_roster(usage_ledger=ledger, budget_ledger=ledger.budget_ledger, env=_ENV, now=_NOW)

    assert roster["day_cost_units"] == {"known": True, "used": 2287, "limit": 3000, "left": 713}
    assert "used 2287 of 3000, left 713" in model_roster_block(roster)


def test_missing_sources_read_as_unknown_not_healthy() -> None:
    """A missing ledger is not a healthy provider (the audit's family:
    unreadable must not widen freedom)."""
    roster = model_roster(usage_ledger=None, budget_ledger=None, env=_ENV, now=_NOW)

    # «Нет ключа» — факт и без журнала (24.09); у остальных здоровье неизвестно.
    # Ни один не «healthy»: нечитаемое не расширяет свободу.
    assert all(p["health"] == ("unknown" if p["key_present"] or p["provider"] == "local" else "no key")
               for p in roster["providers"])
    assert not any(p["health"] == "healthy" for p in roster["providers"])
    assert roster["day_cost_units"] == {"known": False}
    assert "Day ceiling: unknown" in model_roster_block(roster)


def test_the_tool_is_read_only_and_argumentless() -> None:
    import pytest

    from tools.model_roster import ModelRosterTool

    tool = ModelRosterTool(usage_ledger=_Ledger(_rows(), {}))
    assert tool.risk == "read_only"
    out = tool.run()
    assert tool.validate_output(out) == (True, [])
    with pytest.raises(PermissionError):
        tool.run(provider="openai")


def test_the_eye_is_wired_into_the_context_and_the_registry() -> None:
    """Wiring, not presence: the block is built where the spend mirror is,
    and the tool is registered for every agent."""
    import inspect

    from app import bootstrap
    from core import loop_context

    assert "ModelRosterTool(" in inspect.getsource(bootstrap.build_agent)
    src = inspect.getsource(loop_context)
    assert "_model_roster_block" in src and "model_roster_unavailable" in src


def test_without_a_ledger_bearing_router_there_is_no_eye() -> None:
    """The block describes providers «as the router and the ledgers see them».
    An AgentLoop built without a router, or with the single-LLM stub router
    every sandbox test gets (no usage ledger), must get an empty string — not
    a roster read from the developer's own environment, which put a
    <conversation_history> wrapper on a first turn and turned 11 tests red
    (battery of 2026-09-04)."""
    from core.loop_context import AgentLoopContext
    from core.model_router import ModelRouter

    class _Bare(AgentLoopContext):
        model_router = None
        log = None

    assert _Bare()._model_roster_block() == ""

    class _Stub(AgentLoopContext):
        model_router = ModelRouter.single(object())
        log = None

    assert _Stub().model_router.usage_ledger is None
    assert _Stub()._model_roster_block() == ""


def test_a_key_without_a_client_is_shown_as_a_key_without_a_door() -> None:
    """The operator keeps four model keys in .env (2026-09-04: «он должен
    увидеть четыре ключа, а видит только один»). Google's key has no client
    anywhere in this code; the roster must still SHOW it — present, and
    uncallable — and read the operator's own spelling of the name."""
    env = dict(_ENV)
    env["Google_API_KEY"] = "g-secret"
    env["HF_TOKEN"] = "hf-secret"
    roster = model_roster(usage_ledger=_Ledger(_rows(), {}), env=env, now=_NOW)
    by = {p["provider"]: p for p in roster["providers"]}

    assert by["google"]["key_present"] and not by["google"]["door"]
    assert by["huggingface"]["key_present"] and by["huggingface"]["door"]
    assert by["openai"]["door"] and by["anthropic"]["door"] and by["deepseek"]["door"]
    assert not by["local"]["key_present"]
    text = model_roster_block(roster)
    assert "g-secret" not in text and "hf-secret" not in text
    google_line = next(line for line in text.splitlines() if line.startswith("- google"))
    assert "key present" in google_line and "NO CLIENT IN THIS CODE" in google_line
    assert "NO CLIENT" not in next(line for line in text.splitlines() if line.startswith("- openai"))


def test_a_provider_without_a_key_is_not_called_healthy() -> None:
    """24.09, сервер: агент видел пять поставщиков без ключа со словом «healthy»
    (здоровье = «не было сбоев», а у невызванного сбоев нет). Прибор, который
    врёт. Без ключа — «no key», и в подсказке — одной строкой, без шума."""
    env = {"DEEPSEEK_API_KEY": "ds-secret", "AGENT_PROVIDER": "deepseek"}
    roster = model_roster(usage_ledger=_Ledger(_rows(), {}), env=env, now=_NOW)
    by = {p["provider"]: p for p in roster["providers"]}

    assert by["anthropic"]["health"] == "no key"
    assert by["deepseek"]["health"] == "healthy"
    text = model_roster_block(roster)
    assert "healthy" not in next(line for line in text.splitlines() if "anthropic" in line)
    assert not any(line.startswith("- anthropic") for line in text.splitlines())
