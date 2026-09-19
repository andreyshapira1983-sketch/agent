"""Голый идентификатор трассы находит свой файл — тест АВТОРСТВА АГЕНТА.

Первый код в жизни агента, который родился, доехал и позеленел: спроектирован
и написан им самим в прогоне trace_c5a4384f7fcac7588d9884299131e5b2
(2026-08-29, раунд 3 повторного экзамена), извлечён из сырой трассы дословно
— включая отступы, которые смял рот при вручении (седьмая болезнь, в
летописи) — и прошёл с первого прогона. Проверяет починку его же вчерашнего
отказа: «read_logs вернул 0 событий для голого hex» (раунд 7 допроса).

Правки Клода: только эта докстрока. Код ниже — его, байт-в-байт.
"""
import json

from tools.read_logs import ReadLogsTool


def test_bare_trace_id_resolves_prefixed_trace_log(tmp_path):
    bare_trace_id = "d854bda564185f76e94fb566b41dba9f"
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    (logs_dir / f"trace_{bare_trace_id}.jsonl").write_text(
        json.dumps({"event": "probe", "message": "bare id resolution"}) + "\n",
        encoding="utf-8",
    )

    result = ReadLogsTool(tmp_path).run(trace_id=bare_trace_id)

    assert result["trace_id"] == f"trace_{bare_trace_id}"
    assert result["total_events"] == 1
    assert result["events_returned"] == 1
    assert result["events"][0]["event"] == "probe"
