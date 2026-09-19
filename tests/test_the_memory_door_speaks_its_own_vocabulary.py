"""Дверь памяти говорит своим словарём — и тому, кто стучится, и на отказе.

Замер 2026-09-03, шесть ходов разговора наставника с агентом (logs/dialog_d5_*):
планировщик видел инструменты одной строкой — имя, риск, описание — и без
контракта аргументов; три раза выпустил memory_bank без обязательного text,
дважды — с kind='reflection', которого нет в словаре двери (CONSENT_TAG_MAP:
три ключа). Дверь на отказ возвращала None, не называя правила; в четвёртом
ходе агент доложил «banked=True» с чужим mem_id, а в пятом — «шаг снова без
text», хотя шаг выполнился и отказала дверь. Ноль вкладов в память.

Три починки одного шва: (1) инструмент объявляет контракт аргументов,
(2) планировщик видит контракты в промпте, (3) отказ двери называет правило.
Решение самой двери (что принять) не меняется.
"""
from __future__ import annotations

from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore, memory_door_verdict
from tools.base import ToolRegistry
from tools.memory_bank import MemoryBankTool

_GOOD_KIND = "[ВЫВОД, проверен боем]"
_PROSE = "Пол бюджета для думающей модели не применяется без реестра молчавших"


def _tool(tmp_path) -> MemoryBankTool:
    return MemoryBankTool(
        store=PersistentMemoryStore(tmp_path / "pm.jsonl"), policy=MemoryWritePolicy(),
    )


def test_the_tool_declares_its_argument_contract_with_the_three_kinds(tmp_path):
    tool = _tool(tmp_path)

    assert "text" in tool.arguments and "provenance" in tool.arguments
    for kind in ("[ВЫВОД, проверен боем]", "[ВЫВОД, замерен N раз]", "[НАБЛЮДЕНИЕ, один день]"):
        assert kind in tool.arguments, f"словарь двери обязан быть в контракте: {kind}"


def test_the_registry_hands_the_contract_to_the_planner(tmp_path):
    registry = ToolRegistry()
    registry.register(_tool(tmp_path))

    contracts = registry.argument_contracts()

    assert contracts.startswith("- memory_bank:")
    assert "[ВЫВОД, проверен боем]" in contracts
    assert registry.argument_contracts(hidden=frozenset({"memory_bank"})) == ""


def test_a_wrong_kind_is_refused_by_name(tmp_path):
    result = _tool(tmp_path).run(text=_PROSE, kind="reflection", provenance="core/llm.py")

    assert result["banked"] is False and result["mem_id"] is None
    assert "unknown kind 'reflection'" in result["refused_by"]
    assert "[ВЫВОД, проверен боем]" in result["refused_by"]


def test_code_like_text_is_refused_by_name(tmp_path):
    result = _tool(tmp_path).run(
        text="if signature in attempted_signatures:", kind=_GOOD_KIND, provenance="x",
    )

    assert result["banked"] is False
    assert "looks like code" in result["refused_by"]


def test_an_accepted_write_carries_no_refusal(tmp_path):
    result = _tool(tmp_path).run(text=_PROSE, kind=_GOOD_KIND, provenance="core/llm.py:39-43")

    assert result["banked"] is True and str(result["mem_id"]).startswith("mem_")
    assert result["refused_by"] is None


def test_the_verdict_names_a_missing_field(tmp_path):
    store, policy = PersistentMemoryStore(tmp_path / "pm.jsonl"), MemoryWritePolicy()

    mem_id, reason = memory_door_verdict(store, policy, _PROSE, _GOOD_KIND, "")

    assert mem_id is None and "provenance" in reason
