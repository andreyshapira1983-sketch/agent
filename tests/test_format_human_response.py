"""Tests for format_human_response — Output Contract → clean human text."""
from core.loop import format_human_response

# ── typical GPT/Opus Output Contract responses ────────────────────────────────


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


class TestUnverifiedShown:
    def test_real_unverified_content_shown(self):
        out = format_human_response(_OPUS_ANSWER)
        assert "Не подтверждено" in out
        assert "требования" in out


class TestOpusBoldHeaders:
    def test_bold_markers_stripped(self):
        out = format_human_response(_OPUS_ANSWER)
        assert "**" not in out
