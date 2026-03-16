from types import SimpleNamespace

from src.core import intelligence


class _FakeCompletions:
    def __init__(self):
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        msg = SimpleNamespace(content="ok", tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()


def test_get_llm_timeout_sec_from_env(monkeypatch):
    monkeypatch.setenv("LLM_CALL_TIMEOUT_SEC", "123")
    assert intelligence._get_llm_timeout_sec() == 123.0


def test_chat_passes_timeout_to_openai(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(intelligence, "_client_get", lambda: fake)
    monkeypatch.setattr(intelligence, "_openai_tools", lambda: [])
    monkeypatch.setenv("LLM_CALL_TIMEOUT_SEC", "77")

    out = intelligence.chat([{"role": "user", "content": "hello"}], use_tools=False)

    assert out == "ok"
    assert fake.chat.completions.last_kwargs is not None
    assert fake.chat.completions.last_kwargs["timeout"] == 77.0


def test_truncate_context_prioritizes_user_intent_and_recent():
    msgs = [
        {"role": "assistant", "content": "old ack"},
        {"role": "user", "content": "[Intent: run_cycle] please run"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "разрешаю интернет и команды windows"},
    ]

    out = intelligence._truncate_messages_for_context(msgs)

    contents = "\n".join(m.get("content", "") for m in out)
    assert "[Intent: run_cycle]" in contents
    assert "разрешаю интернет" in contents
