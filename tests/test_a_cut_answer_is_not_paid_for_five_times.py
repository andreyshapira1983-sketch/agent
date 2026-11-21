"""Оборванный ответ не оплачивается пять раз и не молчит об обрыве.

Живой прогон 2026-09-17 (`agent_tick.py --campaign --charter --max-cycles 1`)
записал один вызов синтезатора: вход 80 810 токенов, выход 10 240. Выход —
ровно `5 * DEFAULT_MAX_TOKENS`, то есть цепочка продолжений израсходовала все
пять ног и ответ всё равно был обрезан.

Замерено пробником, который перехватывал запрос до отправки: сам запрос
синтезатора весил 44 502 символа — около 11 000 токенов. Пять ног по 11 000
плюс растущий черновик дают 75 480; реестр записал 80 810. Арифметика сходится.

Отсюда два следствия, и оба здесь засвидетельствованы.

Первое — цена. `complete()` шлёт **весь** запрос заново на каждой ноге, а
`round_budget` удваивается только когда нога вернулась ПУСТОЙ. Нога, которая
писала текст и была обрезана, переспрашивается тем же потолком 2048 — и так
до конца цепочки. Запрос оплачен пять раз вместо одного.

Второе — и оно тяжелее. Маркер завершения стоит в КОНЦЕ ответа. Ответ до конца
не доходит, значит маркер не приходит никогда: в журнале живого прогона
`completion_declaration parse=missing declared=null`, а следом `verdict=missing`
и «ответ есть, следа нет». Двадцать пустых циклов прошлого лога объясняются
этим лучше, чем любой разобранный до сих пор дефект. Сам факт обрыва клиент
знает (`last_answer_was_truncated`), но до места, где судят маркер, он не
доезжает — и молчание там читается как «модель забыла маркер».
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import DEFAULT_MAX_TOKENS, LLM
from tests.test_llm import _scripted_openai_llm

BODY = "Conclusion: половина мысли, оборванная на"


@pytest.fixture(autouse=True)
def _own_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выученное — это состояние, и оно течёт между файлами.

    Реестр отвечает «эта модель рассуждает» и поднимает потолок эскалации до
    `_REASONING_TOKEN_FLOOR`: замерено 2026-09-17, прогон вместе с
    `test_the_roster_learns_which_models_think.py` дал ноги [2048, 16384,
    32768] вместо [2048, 4096, 8192]. Реестр пишется и ОТСЮДА: пустая
    обрезанная нога вызывает `remember_reasoning_model`, так что подсунуть
    свой файл мало — тест поднял бы себе потолок сам (моя первая попытка
    забора именно это и сделала). Здесь спор идёт о ШАГЕ эскалации, а не о
    высоте потолка, поэтому реестра не должно быть вовсе. Соседний
    `tests/test_llm.py::_clean_env` держит тот же забор и той же причиной.
    """
    for key in ("AGENT_AUTO_CONTINUE", "AGENT_MAX_CONTINUATIONS",
                "AGENT_CONTINUE_ESCALATION_CAP", "AGENT_MAX_TOKENS",
                "AGENT_REASONING_ROSTER"):
        monkeypatch.delenv(key, raising=False)


class _BudgetSensitiveCompletions:
    """Модель, которой просто не хватает потолка одной ноги.

    Это и есть живой случай: ответ на 10 000 токенов не помещается в 2048, и
    единственное, что может его закончить, — нога побольше. Скрипт с
    фиксированным `finish_reason` такой случай не различает, потому что он не
    смотрит на запрошенный потолок.
    """

    def __init__(self, needs: int) -> None:
        self._needs = needs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        budget = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 0
        finish = "stop" if budget >= self._needs else "length"
        return _Response("часть ответа. ", budget, finish)


class _Response:
    def __init__(self, content: str, out_tok: int, finish: str) -> None:
        self.choices = [_Choice(content, finish)]
        self.usage = _Usage(out_tok)


class _Choice:
    def __init__(self, content: str, finish: str) -> None:
        self.message = _Message(content)
        self.finish_reason = finish


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Usage:
    def __init__(self, out_tok: int) -> None:
        # Вход одинаков на каждой ноге по построению: запрос уходит целиком
        # заново. Это и есть измеряемая цена.
        self.prompt_tokens = 11000
        self.completion_tokens = out_tok


def _budget_sensitive_llm(needs: int) -> LLM:
    llm = _scripted_openai_llm([])
    llm._client.chat.completions = _BudgetSensitiveCompletions(needs)
    return llm


def test_the_bill_for_one_answer_is_not_the_prompt_five_times() -> None:
    """Запрос уходит целиком на каждой ноге — значит ног должно быть мало.

    Замер живого прогона: 80 810 токенов входа там, где честная цена запроса
    около 11 000. Это не округление, это множитель.
    """
    llm = _budget_sensitive_llm(needs=8192)

    llm.complete(system="s", user="u", max_tokens=DEFAULT_MAX_TOKENS)

    spent_in = llm.last_usage["input_tokens"]
    assert spent_in <= 11000 * 3, (
        f"запрос оплачен {spent_in / 11000:.1f} раз вместо одного"
    )


# ==========================================================================
# Следствие второе: молчание об обрыве там, где судят маркер.
# ==========================================================================
def _agent(workspace: Path):
    from app.bootstrap import build_agent

    return build_agent(workspace, with_memory=True, approval_provider=None)


@pytest.fixture()
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Живой ключ в окружении увёл бы цикл в сеть вместо заглушки."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _events(agent, name: str) -> list[dict]:
    return [
        json.loads(line)["payload"]
        for line in Path(agent.log.path).read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == name
    ]


def test_a_cut_answer_says_it_was_cut_where_the_marker_is_judged_missing(
    tmp_path: Path, monkeypatch, _offline_routing
) -> None:
    """`parse=missing` от обрыва и `parse=missing` от забывчивости — разное.

    В журнале живого прогона они выглядят одинаково, и двадцать циклов читались
    как «модель не ставит маркер». На деле маркер стоит в конце ответа, а ответ
    обрывался на потолке. Клиент знает об обрыве; строка, выносящая вердикт,
    обязана это знать тоже.
    """
    def _fake(self, *_args, completion_nonce: str = "", llm=None, **_kwargs) -> str:
        target = llm if llm is not None else self.llm
        target.last_answer_was_truncated = True
        return BODY

    monkeypatch.setattr("core.loop.AgentLoop._synthesize", _fake)
    agent = _agent(tmp_path)

    agent.run("сколько строк в файле core/loop_methods2.py")

    event = _events(agent, "completion_declaration")[-1]
    assert event["parse"] == "missing"
    assert event.get("truncated") is True, (
        "вердикт о маркере молчит о том, что ответ был обрезан"
    )
