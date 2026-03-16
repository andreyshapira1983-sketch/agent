from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.communication import telegram_client


class _FakeApp:
    def __init__(self) -> None:
        self.handlers: list[object] = []
        self.error_handler = None
        self.run_polling_called_with = None

    def add_handler(self, handler: object) -> None:
        self.handlers.append(handler)

    def add_error_handler(self, handler: object) -> None:
        self.error_handler = handler

    def run_polling(self, **kwargs) -> None:
        self.run_polling_called_with = kwargs


class _FakeBuilder:
    def __init__(self, app: _FakeApp) -> None:
        self._app = app
        self.token_value = None

    def token(self, token: str) -> _FakeBuilder:
        self.token_value = token
        return self

    def build(self) -> _FakeApp:
        return self._app


def test_run_bot_registers_minimal_command_and_message_routing(monkeypatch) -> None:
    fake_app = _FakeApp()

    class _FakeApplication:
        @staticmethod
        def builder() -> _FakeBuilder:
            return _FakeBuilder(fake_app)

    def fake_command_handler(name: str, callback):
        return ("command", name, callback)

    def fake_message_handler(_filters_obj, callback):
        return ("message", callback)

    monkeypatch.setattr(telegram_client, "Application", _FakeApplication)
    monkeypatch.setattr(telegram_client, "CommandHandler", fake_command_handler)
    monkeypatch.setattr(telegram_client, "TgMessageHandler", fake_message_handler)

    async def _dummy(_update, _context):
        return None

    telegram_client.run_bot(
        "token",
        help_handler=_dummy,
        status_handler=_dummy,
        log_handler=_dummy,
        tasks_handler=_dummy,
        mood_handler=_dummy,
        guard_handler=_dummy,
        autonomous_handler=_dummy,
        stop_handler=_dummy,
        safe_expand_handler=_dummy,
        apply_sandbox_only_handler=_dummy,
        apply_validated_handler=_dummy,
        cancel_handler=_dummy,
        remind_handler=_dummy,
    )

    command_names = [h[1] for h in fake_app.handlers if isinstance(h, tuple) and h[0] == "command"]
    assert "help" in command_names
    assert "status" in command_names
    assert "log" in command_names
    assert "tasks" in command_names
    assert "queue" in command_names
    assert "mood" in command_names
    assert "emotions" in command_names
    assert "guard" in command_names
    assert "autonomous" in command_names
    assert "stop" in command_names
    assert "safe_expand" in command_names
    assert "apply_sandbox_only" in command_names
    assert "apply_validated" in command_names
    assert "cancel" in command_names
    assert "remind" in command_names
    assert callable(fake_app.error_handler)
    assert fake_app.run_polling_called_with is not None
    assert "allowed_updates" in fake_app.run_polling_called_with


def test_handle_message_routes_text_to_default_handler(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_handler_with_reply(update, user_id, text, *, prefer_voice=False, fallback_no_handler=""):
        captured["update"] = update
        captured["user_id"] = user_id
        captured["text"] = text
        captured["prefer_voice"] = prefer_voice
        _ = fallback_no_handler

    monkeypatch.setattr(telegram_client, "_run_handler_with_reply", fake_run_handler_with_reply)

    update = SimpleNamespace(
        message=SimpleNamespace(text="  Привет  "),
        effective_chat=SimpleNamespace(id=12345),
        effective_user=SimpleNamespace(id=777),
    )

    asyncio.run(getattr(telegram_client, "_handle_message")(update, None))

    assert captured["user_id"] == "777"
    assert captured["text"] == "Привет"
    assert captured["prefer_voice"] is False


def test_handle_message_routes_voice_prefix(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_handler_with_reply(_update, user_id, text, *, prefer_voice=False, fallback_no_handler=""):
        captured["user_id"] = user_id
        captured["text"] = text
        captured["prefer_voice"] = prefer_voice
        _ = fallback_no_handler

    monkeypatch.setattr(telegram_client, "_run_handler_with_reply", fake_run_handler_with_reply)

    update = SimpleNamespace(
        message=SimpleNamespace(text="/voice проверь статус"),
        effective_chat=SimpleNamespace(id=54321),
        effective_user=SimpleNamespace(id=42),
    )

    asyncio.run(getattr(telegram_client, "_handle_message")(update, None))

    assert captured["user_id"] == "42"
    assert captured["text"] == "/voice проверь статус"
    assert captured["prefer_voice"] is True
