"""Подъём получает настоящий адрес журнала трассы.

Кампания 2026-09-22: номер трассы уже начинается с «trace_», а наблюдение
склеивало адрес как logs/trace_{номер}.jsonl — выходило «logs/trace_trace_…».
38 наблюдений из 47 называли несуществующий файл; модель писала пробу по нему,
ворота рождения убивали пару, и подъём падал «выжило 1» десятки циклов подряд.
Список файлов не спасал: он режется на сорока именах, а в logs/ их сотни.
"""
from __future__ import annotations

from core.causal_claim_store import load_claims
from core.causal_climb_action import explain_causal_observation
from core.causal_lesson import Observation, trace_log_path
from core.causal_store import CausalObservationStore


class _CountingLLM:
    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []
        self.call_count = 0

    def complete(self, **kwargs):
        self.call_count += 1
        self.prompts.append(kwargs.get("user", ""))
        return self.response


class _Log:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload):
        self.events.append((event, payload))


class _Agent:
    def __init__(self, response: str):
        self.llm = _CountingLLM(response)
        self.log = _Log()


def _seed(tmp_path, mismatch: str) -> None:
    CausalObservationStore(tmp_path / "data" / "causal_observations.jsonl").record(Observation(
        episode_id="ep1", trace_id="trace_t1", run_id="r1",
        defect_signals=("sig_a",), evidence_refs=("ev1",), observed_mismatch=mismatch,
    ))
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for i in range(60):  # больше сорока — нужный журнал не влезает в обрезанный список
        (logs / f"a_{i:02d}.jsonl").write_text("{}\n", encoding="utf-8")
    (logs / "trace_t1.jsonl").write_text('{"event": "sig_a"}\n', encoding="utf-8")


_TWO_PAIRS = (
    "ОБЪЯСНЕНИЕ 1: причина А\n"
    "ПРЕДСКАЗАНИЕ 1: в журнале есть сигнал [probe: logs/trace_t1.jsonl | sig_a | есть]\n"
    "ОБЪЯСНЕНИЕ 2: причина Б\n"
    "ПРЕДСКАЗАНИЕ 2: в памяти нет записи [probe: data/causal_observations.jsonl | zzz | нет]\n"
)


def test_the_address_is_built_once() -> None:
    assert trace_log_path("trace_t1") == "logs/trace_t1.jsonl"
    assert trace_log_path("t1") == "logs/trace_t1.jsonl"


def test_an_old_doubled_address_is_repaired_and_the_real_file_is_listed(tmp_path) -> None:
    _seed(tmp_path, "событие детектора — в logs/trace_trace_t1.jsonl")
    agent = _Agent(_TWO_PAIRS)

    outcome = explain_causal_observation(agent=agent, workspace=tmp_path)

    prompt = agent.llm.prompts[0]
    assert "trace_trace_" not in prompt
    assert "названы наблюдением: logs/trace_t1.jsonl" in prompt
    assert outcome.result == "completed"
    assert len(load_claims(tmp_path)) == 1


def test_a_decline_after_the_model_call_reports_the_call(tmp_path) -> None:
    _seed(tmp_path, "событие детектора — в logs/trace_t1.jsonl")
    agent = _Agent("ОБЪЯСНЕНИЕ 1: одна\nПРЕДСКАЗАНИЕ 1: что-то\n")

    outcome = explain_causal_observation(agent=agent, workspace=tmp_path)

    assert outcome.result == "failed"
    assert outcome.llm_calls_spent == 1, "вызов модели оплачен — бюджет не должен писать 0"
