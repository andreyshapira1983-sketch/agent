"""Tests for format_human_response — Output Contract → clean human text."""
import pytest
from core.loop import format_human_response

# ── typical GPT/Opus Output Contract responses ────────────────────────────────

_GPT_ANSWER = """\
Conclusion:
GIL (Global Interpreter Lock) — это мьютекс внутри CPython. [general-knowledge]
Он не позволяет нескольким потокам одновременно исполнять байткод Python. [general-knowledge]
Facts:
- GIL защищает счётчики ссылок объектов от гонок. [general-knowledge]
- В I/O-bound задачах потоки освобождают GIL и работают параллельно. [general-knowledge]
- Убрать GIL сложно: сломается совместимость с C-расширениями. [general-knowledge]
Sources:
1.
general-knowledge
Confidence: medium
Unverified:
nothing
Safety:
nothing

[note] Every claim above is marked — the model honestly admits these come from its prior training.
"""

_OPUS_ANSWER = """\
Conclusion:
Микросервисная архитектура системы мониторинга строится из нескольких слоёв. [general-knowledge]
Ниже разбивка по компонентам.
Facts:
- **Сбор данных (ingestion):** агенты на хостах экспонируют метрики. [general-knowledge]
- **Collector / Gateway:** нормализует и буферизует входящий поток. [general-knowledge]
- **Очередь сообщений:** Kafka/NATS как буфер между ingestion и обработкой. [general-knowledge]
Хранилище данных:
- **Метрики:** TSDB — Prometheus, VictoriaMetrics, InfluxDB. [general-knowledge]
Sources:
1. [general-knowledge] - general-knowledge
Confidence: medium
Unverified:
Конкретные требования системы не были предоставлены.
Safety:
nothing
"""

_NO_CONTRACT = "Это просто текст без контракта."


class TestBasicStripping:
    def test_removes_conclusion_header(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Conclusion:" not in out

    def test_removes_facts_header(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Facts:" not in out

    def test_removes_sources_section(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Sources:" not in out
        assert "general-knowledge" not in out

    def test_removes_confidence_section(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Confidence:" not in out

    def test_removes_unverified_nothing(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Unverified:" not in out
        assert "nothing" not in out

    def test_removes_safety_section(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Safety:" not in out

    def test_removes_note_disclaimer(self):
        out = format_human_response(_GPT_ANSWER)
        assert "[note]" not in out

    def test_removes_citation_tokens(self):
        out = format_human_response(_GPT_ANSWER)
        assert "[general-knowledge]" not in out


class TestContentPreserved:
    def test_conclusion_text_preserved(self):
        out = format_human_response(_GPT_ANSWER)
        assert "GIL" in out
        assert "мьютекс" in out

    def test_facts_bullets_preserved(self):
        out = format_human_response(_GPT_ANSWER)
        assert "счётчики ссылок" in out
        assert "I/O-bound" in out
        assert "C-расширениями" in out

    def test_bullets_use_dot(self):
        out = format_human_response(_GPT_ANSWER)
        assert "•" in out


class TestUnverifiedShown:
    def test_real_unverified_content_shown(self):
        out = format_human_response(_OPUS_ANSWER)
        assert "Не подтверждено" in out
        assert "требования" in out

    def test_unverified_nothing_not_shown(self):
        out = format_human_response(_GPT_ANSWER)
        assert "Не подтверждено" not in out


class TestOpusBoldHeaders:
    def test_bold_subheader_preserved(self):
        out = format_human_response(_OPUS_ANSWER)
        assert "Сбор данных" in out or "ingestion" in out.lower()

    def test_bold_markers_stripped(self):
        out = format_human_response(_OPUS_ANSWER)
        assert "**" not in out


class TestEdgeCases:
    def test_non_contract_returned_unchanged(self):
        out = format_human_response(_NO_CONTRACT)
        assert out == _NO_CONTRACT

    def test_empty_string(self):
        assert format_human_response("") == ""

    def test_web_citation_stripped(self):
        answer = (
            "Conclusion:\nPython 3.14 вышел в 2026. [web:https://python.org]\n"
            "Facts:\n- Поддержка Gil-free режима. [web:https://python.org]\n"
            "Sources:\n1. web:https://python.org\nConfidence: high\nUnverified:\nnothing\nSafety:\nnothing\n"
        )
        out = format_human_response(answer)
        assert "[web:" not in out
        assert "python.org" in out or "Python 3.14" in out
        assert "Sources:" not in out

    def test_file_citation_stripped(self):
        answer = (
            "Conclusion:\nФайл содержит 200 строк. [file:core/loop.py]\n"
            "Facts:\n- Класс AgentLoop определён на строке 280. [file:core/loop.py]\n"
            "Sources:\n1. file:core/loop.py\nConfidence: high\nUnverified:\nnothing\nSafety:\nnothing\n"
        )
        out = format_human_response(answer)
        assert "[file:" not in out
        assert "200 строк" in out

    def test_no_double_blank_lines(self):
        out = format_human_response(_GPT_ANSWER)
        assert "\n\n\n" not in out


def test_strips_empty_blockquote_leftover():
    answer = """\
Conclusion:
Слабые стороны ясны. [user:target]
Facts:
- > [user:target]
- Дублирование тезисов. [user:target]
Sources:
1. user:target
Confidence: medium
Unverified:
nothing
Safety:
nothing
"""
    out = format_human_response(answer)
    assert "\n>\n" not in out
    assert not any(line.strip() == ">" for line in out.splitlines())
    assert "Дублирование тезисов" in out


def test_strips_user_target_and_prior_turn_citations():
    answer = """\
Conclusion:
Текст содержит преувеличения. [prior_turn:turn_abc]
Facts:
- Нет источников. [user:target]
Sources:
1. prior_turn:turn_abc
Confidence: medium
Unverified:
nothing
Safety:
nothing
"""
    out = format_human_response(answer)
    assert "[prior_turn:" not in out
    assert "[user:target]" not in out
    assert "преувеличения" in out
