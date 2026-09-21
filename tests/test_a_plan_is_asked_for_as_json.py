"""План просится у поставщика в режиме JSON, а не только словами подсказки.

Разговор через мостик 2026-09-21 ~17:10: «перепиши в _v3.md и назови размер».
Планировщик дважды вернул текст, который не разобрался как JSON (11 329 и
10 997 знаков: неэкранированная кавычка в цитате кода, неверный `\\`), цикл
сдался без единого инструмента — и ответ сочинил «записано, 9412 байт».
Замер 19–21.09: 9 из 2176 ответов планировщика не разобрались, у всех четыре
вида порчи строки. DeepSeek и OpenAI умеют `response_format={"type":
"json_object"}` (api-docs.deepseek.com/guides/json_mode); агент его не просил.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.llm import LLM, accepted_flags


class _Completions:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text, reason = self.script.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=reason)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


def _llm(provider: str, script) -> LLM:
    llm = LLM(provider="mock")
    llm.provider, llm.model = provider, "m"
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions(script)))
    return llm


def test_deepseek_gets_json_mode_when_asked() -> None:
    llm = _llm("deepseek", [('{"steps": []}', "stop")])
    assert llm.complete("s", "u", json_object=True) == '{"steps": []}'
    assert llm._client.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


def test_without_the_request_nothing_changes() -> None:
    llm = _llm("deepseek", [("text", "stop")])
    llm.complete("s", "u")
    assert "response_format" not in llm._client.chat.completions.calls[0]


def test_a_provider_without_json_mode_is_not_sent_it() -> None:
    llm = _llm("local", [("{}", "stop")])
    llm.complete("s", "u", json_object=True)
    assert "response_format" not in llm._client.chat.completions.calls[0]


def test_a_continuation_leg_resumes_text_not_a_new_object() -> None:
    llm = _llm("deepseek", [('{"steps": [', "length"), ("]}", "stop")])
    assert llm.complete("s", "u", json_object=True) == '{"steps": []}'
    first, second = llm._client.chat.completions.calls
    assert "response_format" in first and "response_format" not in second


def test_a_wrapper_that_predates_the_flag_is_not_sent_it() -> None:
    def old(system, user, max_tokens=1, temperature=0.0):
        return ""

    def new(system, user, max_tokens=1, temperature=0.0, *, json_object=False):
        return ""

    def open_ended(system, user, **kwargs):
        return ""

    assert accepted_flags(old, {"json_object": True}) == {}
    assert accepted_flags(new, {"json_object": True}) == {"json_object": True}
    assert accepted_flags(open_ended, {"json_object": True}) == {"json_object": True}


def test_the_router_carries_the_flag_and_does_not_pay_twice() -> None:
    """Старая развилка ловила TypeError и звала модель второй раз; TypeError
    изнутри настоящего вызова стоил бы двойной оплаты."""
    from core.model_router import UsageTrackedLLM

    calls: list[dict] = []

    class _Inner:
        provider, model = "deepseek", "m"

        def complete(self, system, user, max_tokens=1, temperature=0.0, *,
                     allow_continuation=True, json_object=False):
            calls.append({"json_object": json_object, "allow_continuation": allow_continuation})
            raise TypeError("inside the provider")

    router = UsageTrackedLLM.__new__(UsageTrackedLLM)
    router._llm = _Inner()
    try:
        router._call_llm(system="s", user="u", max_tokens=1, temperature=0.0,
                         allow_continuation=False, json_object=True)
    except TypeError:
        pass
    assert calls == [{"json_object": True, "allow_continuation": False}]


def test_the_planner_asks_for_json() -> None:
    from core.planner import LLMPlanner as Planner
    from tools.base import ToolRegistry

    seen: list[dict] = []

    class _Recorder:
        provider, model = "deepseek", "m"

        def complete(self, system, user, max_tokens=1, temperature=0.0, **flags):
            seen.append(flags)
            return '{"reasoning": "r", "steps": []}'

    Planner(llm=_Recorder(), registry=ToolRegistry()).plan("прочитай core/llm.py", None)
    assert seen and seen[0].get("json_object") is True
