import importlib


def test_send_daily_quality_summary_if_due(tmp_path, monkeypatch):
    alerts = importlib.import_module("src.communication.telegram_alerts")

    monkeypatch.setattr(alerts, "_SUMMARY_STATE_PATH", tmp_path / "quality_summary_state.json")
    monkeypatch.setattr(alerts, "get_alerts_chat_id", lambda: "123")
    monkeypatch.setattr(alerts, "_send_to_alerts_chat", lambda text: "Недельная сводка качества" in text)

    commands = importlib.import_module("src.communication.telegram_commands")
    monkeypatch.setattr(commands, "get_weekly_quality_summary", lambda: "Недельная сводка качества:\n  ok")

    sent = alerts.send_daily_quality_summary_if_due(force=True)

    assert sent is True
    assert alerts._SUMMARY_STATE_PATH.exists()


def test_send_agent_step_uses_default_event_types(monkeypatch):
    alerts = importlib.import_module("src.communication.telegram_alerts")

    monkeypatch.delenv("TELEGRAM_EVENT_TYPES", raising=False)
    monkeypatch.setenv("TELEGRAM_AUTONOMOUS_EVENTS", "1")
    monkeypatch.setattr(alerts, "_last_chat_id", "123")
    monkeypatch.setattr(alerts, "_last_step_sent", 0.0)
    monkeypatch.setattr(alerts.time, "monotonic", lambda: 1000.0)

    sent: list[str] = []
    monkeypatch.setattr(alerts, "_send_to_alerts_chat", lambda text: (sent.append(text) or True))

    alerts.send_agent_step("task_start", "Начал задачу")
    assert len(sent) == 1
    assert "Начал задачу" in sent[0]


def test_send_agent_step_skips_non_allowed_event(monkeypatch):
    alerts = importlib.import_module("src.communication.telegram_alerts")

    monkeypatch.delenv("TELEGRAM_EVENT_TYPES", raising=False)
    monkeypatch.setenv("TELEGRAM_AUTONOMOUS_EVENTS", "1")
    monkeypatch.setattr(alerts, "_last_chat_id", "123")
    monkeypatch.setattr(alerts, "_last_step_sent", 0.0)
    monkeypatch.setattr(alerts.time, "monotonic", lambda: 2000.0)

    sent: list[str] = []
    monkeypatch.setattr(alerts, "_send_to_alerts_chat", lambda text: (sent.append(text) or True))

    alerts.send_agent_step("goal", "Цель цикла")
    assert sent == []