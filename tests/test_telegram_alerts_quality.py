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