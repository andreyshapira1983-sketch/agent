"""Модель, уже записанная в реестр думающих, получает пол бюджета с ПЕРВОГО запроса.

Замер 10.11 (deepseek-flash, думает по умолчанию): сверка утверждения по смыслу
просит 4 токена на «yes/no». Рассуждение съедает их, ответ пуст и обрезан, и
только второй запрос — уже с полом — отвечает. Реестр знал это с первого хода,
но пол применялся лишь к продолжениям: каждая сверка стоила двух запросов.
Проверка та же, вопрос тот же, модель та же — убран только заведомо пустой запрос.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.llm import LLM, remember_reasoning_model
from core.request_checklist import BUILD_SYSTEM, JUDGE_SYSTEM
from core.verifier_utils import _NLI_SYSTEM

_THINKING_TOKENS = 64
_NOTES = "Список дел.\nКод склада: 7391.\nПозвонить в банк.\n"


class _ThinkingProvider:
    """Поставщик думающей модели: бюджет меньше рассуждения — пустой обрезанный ответ."""

    def __init__(self, synth_answer: str = "391 [general-knowledge]", plan_steps: list | None = None):
        self.requests: list[dict] = []
        self.synth_answer, self.plan_steps = synth_answer, plan_steps or []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        budget = int(kwargs["max_tokens"])
        if budget < _THINKING_TOKENS:
            text, finish, spent = "", "length", budget
        else:
            text, finish, spent = self._answer(kwargs["messages"][0]["content"]), "stop", _THINKING_TOKENS
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=spent),
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
        )

    def _answer(self, system: str) -> str:
        if system == BUILD_SYSTEM:
            return json.dumps({"items": [{"question": "Назван код склада?", "quote": "код склада",
                                          "importance": 90, "rule": None, "arg": None}]}, ensure_ascii=False)
        if system == JUDGE_SYSTEM:
            return json.dumps({"answers": [{"n": 1, "answer": "YES", "why": "t"}, {"n": 2, "answer": "YES", "why": "t"}]})
        if system == _NLI_SYSTEM:
            return "yes"
        if "PLANNER_MODE" in system:
            return json.dumps({"reasoning": "r", "steps": self.plan_steps})
        nonce = re.search(r"\[\[agent\.completion:([a-f0-9]+):<token>\]\]", system)
        return f"{self.synth_answer}\n[[agent.completion:{nonce[1]}:achieved]]" if nonce else self.synth_answer

    def of(self, system: str) -> list[dict]:
        return [r for r in self.requests if r["messages"][0]["content"] == system]


@pytest.fixture
def provider(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_REASONING_ROSTER", str(tmp_path / "roster.jsonl"))
    monkeypatch.delenv("AGENT_DEEPSEEK_THINKING", raising=False)
    fake = _ThinkingProvider()
    monkeypatch.setattr(LLM, "_build_client", lambda self: fake)
    return fake


# ── сквозь настоящий ход: проверки на месте, лишних запросов нет ───────────────


def _turn(monkeypatch, tmp_path: Path, provider: _ThinkingProvider, question: str) -> None:
    from cli.one_shot import run_one_shot

    monkeypatch.setenv("AGENT_PROVIDER", "deepseek")
    monkeypatch.setenv("AGENT_REQUEST_CHECKLIST", "1")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text(_NOTES, encoding="utf-8")
    remember_reasoning_model("deepseek", "deepseek-flash", spent=4)
    run_one_shot(question, workspace=workspace, auto_approve="deny")


def test_a_turn_with_a_source_keeps_every_verification_call(monkeypatch, tmp_path, provider, capsys) -> None:
    """Ход с файлом: сверка по смыслу и судья чек-листа вызваны, каждая сверка — один запрос."""
    provider.plan_steps = [{"tool": "file_read", "arguments": {"path": "notes.txt"}, "rationale": "read"}]
    provider.synth_answer = "Код склада 7391 — единственный код в файле [file:notes.txt]."

    _turn(monkeypatch, tmp_path, provider, "Прочитай файл notes.txt и скажи, какой там код склада.")

    assert "7391" in capsys.readouterr().out
    nli = provider.of(_NLI_SYSTEM)
    assert nli, "выведенное утверждение со ссылкой на файл обязано пройти сверку по смыслу"
    assert all(r["max_tokens"] >= _THINKING_TOKENS for r in nli), "ни одного заведомо пустого запроса"
    assert "Код склада: 7391." in nli[0]["messages"][1]["content"], "судья видит прочитанный файл"
    assert len(provider.of(BUILD_SYSTEM)) == 1 and len(provider.of(JUDGE_SYSTEM)) == 1
    assert all(r["max_tokens"] >= _THINKING_TOKENS for r in provider.requests)


def test_a_turn_without_sources_has_no_entailment_call_but_keeps_the_checklist(
        monkeypatch, tmp_path, provider, capsys) -> None:
    """Ход без инструментов: сверять по источнику нечего, а судья чек-листа остаётся."""
    _turn(monkeypatch, tmp_path, provider, "Какой код склада обычно пишут первым? Назови код склада одним числом.")

    assert "391" in capsys.readouterr().out
    assert provider.of(_NLI_SYSTEM) == []
    assert len(provider.of(BUILD_SYSTEM)) == 1 and len(provider.of(JUDGE_SYSTEM)) == 1
    # Составитель, планировщик, синтез, судья — и ни одного запроса сверх этого.
    assert len(provider.requests) == 4
