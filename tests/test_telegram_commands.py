import importlib

from src.communication.telegram_commands import (
    export_quality_status,
    get_agent_status,
    get_human_memory_reply,
    get_quality_status,
    get_weekly_quality_summary,
    reset_quality_status,
)


def test_get_agent_status_includes_quality_metrics(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_get_metrics():
        return {
            "calls": 10,
            "errors": 2,
            "successes": 8,
            "last_duration_sec": 1.23,
            "quality": {
                "tasks_solved": 5,
                "accepted_patches": 2,
                "successful_repairs": 1,
                "failed_repairs": 3,
                "test_runs_total": 4,
                "test_runs_passed": 3,
                "test_pass_ratio": 0.75,
                "recent_history": [{"event_type": "accepted_patch", "status": "ok"}],
            },
        }

    monkeypatch.setattr(metrics_mod, "get_metrics", fake_get_metrics)

    text = get_agent_status()

    assert "Качество:" in text
    assert "решено задач=5" in text
    assert "принято патчей=2" in text
    assert "успешных ремонтов=1" in text
    assert "проваленных ремонтов=3" in text
    assert "pass_ratio=0.75" in text


def test_get_quality_status_includes_history(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_get_metrics():
        return {
            "quality": {
                "tasks_solved": 2,
                "accepted_patches": 1,
                "successful_repairs": 1,
                "failed_repairs": 0,
                "test_runs_total": 2,
                "test_runs_passed": 2,
                "test_pass_ratio": 1.0,
                "recent_history": [
                    {
                        "event_type": "accepted_patch",
                        "status": "ok",
                        "target_path": "src/a.py",
                        "patch_id": "p1",
                    },
                    {
                        "event_type": "repair_attempt",
                        "status": "failed",
                        "target_path": "src/b.py",
                        "patch_id": "p2",
                        "note": "sandbox_validation_failed",
                    },
                ],
            }
        }

    monkeypatch.setattr(metrics_mod, "get_metrics", fake_get_metrics)

    text = get_quality_status()

    assert "Quality:" in text
    assert "Принято патчей: 1" in text
    assert "accepted_patch: ok; path=src/a.py; patch=p1" in text
    assert "repair_attempt: failed; path=src/b.py; patch=p2; note=sandbox_validation_failed" in text


def test_get_weekly_quality_summary(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_get_metrics():
        return {
            "quality": {
                "tasks_solved": 4,
                "accepted_patches": 2,
                "successful_repairs": 1,
                "failed_repairs": 1,
                "test_runs_total": 5,
                "test_runs_passed": 4,
                "test_pass_ratio": 0.8,
                "recent_history": [
                    {"event_type": "task_solved", "status": "ok", "target_path": "request_patch"},
                    {"event_type": "accepted_patch", "status": "ok", "target_path": "src/a.py"},
                    {"event_type": "repair_attempt", "status": "failed", "target_path": "src/b.py"},
                ],
            }
        }

    monkeypatch.setattr(metrics_mod, "get_metrics", fake_get_metrics)

    text = get_weekly_quality_summary()

    assert "Недельная сводка качества" in text
    assert "Решено задач: 4" in text
    assert "Принято патчей: 2" in text
    assert "repair_failed=1" in text
    assert "Короткий вывод:" in text
    assert "Что стало лучше:" in text
    assert "Что ломалось:" in text
    assert "Где риск:" in text


def test_export_quality_status(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_export_quality_report(report_format: str = "text", file_path=None):
        _ = file_path
        return f"Exported to C:/tmp/quality.{ 'json' if report_format == 'json' else 'txt' }"

    monkeypatch.setattr(metrics_mod, "export_quality_report", fake_export_quality_report)

    text = export_quality_status("json")

    assert "Quality JSON export готов" in text


def test_export_quality_status_full(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")

    def fake_export_quality_report(report_format: str = "text", file_path=None):
        _ = file_path
        return f"Exported to C:/tmp/quality.{ 'txt' if report_format == 'full' else 'json' }"

    monkeypatch.setattr(metrics_mod, "export_quality_report", fake_export_quality_report)

    text = export_quality_status("full")

    assert "FULL export" in text


def test_reset_quality_status_calls_metrics_reset(monkeypatch):
    metrics_mod = importlib.import_module("src.monitoring.metrics")
    called = {"reset": False}

    def fake_reset_quality():
        called["reset"] = True

    monkeypatch.setattr(metrics_mod.metrics, "reset_quality", fake_reset_quality)

    text = reset_quality_status()

    assert called["reset"] is True
    assert "сброшены" in text.lower()


def test_human_memory_reply_on_greeting_uses_memory(monkeypatch):
    stm = importlib.import_module("src.memory.short_term")
    monkeypatch.setattr(
        stm,
        "get_messages",
        lambda _uid: [
            {"role": "user", "content": "Хочу, чтобы ты запоминал мои темы"},
            {"role": "assistant", "content": "Запомнил"},
        ],
    )
    cmd = importlib.import_module("src.communication.telegram_commands")
    monkeypatch.setattr(cmd, "_build_live_brief", lambda: "автономный режим выключен; в очереди задач: 0")
    monkeypatch.setattr(cmd, "_extract_recent_learning_note", lambda: "изучил новую стратегию саморемонта")

    text = get_human_memory_reply("u1", "Привет")

    assert text is not None
    assert "Помню" in text
    assert "изучил новую стратегию" in text
    assert "Как ты сам" in text


def test_human_memory_reply_on_how_are_you(monkeypatch):
    stm = importlib.import_module("src.memory.short_term")
    monkeypatch.setattr(
        stm,
        "get_messages",
        lambda _uid: [{"role": "user", "content": "Мы обсуждали улучшение качества"}],
    )
    cmd = importlib.import_module("src.communication.telegram_commands")
    monkeypatch.setattr(cmd, "_build_live_brief", lambda: "последнее действие: run_self_repair")

    text = get_human_memory_reply("u1", "Как дела?")

    assert text is not None
    assert "По состоянию" in text
    assert "Как у тебя дела" in text


def test_human_memory_reply_returns_none_for_non_smalltalk():
    text = get_human_memory_reply("u1", "Сделай рефактор src/main.py")
    assert text is None