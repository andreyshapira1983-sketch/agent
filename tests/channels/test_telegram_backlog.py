"""Tests for TelegramIntakeChannel's first-boot backlog skipping."""

from __future__ import annotations

from pathlib import Path

from brain.secrets import SecretsVault
from channels.telegram_intake import TelegramIntakeChannel


def _vault() -> SecretsVault:
    v = SecretsVault()
    v.set("TELEGRAM_BOT_TOKEN", "t")
    return v


class _FakeHTTP:
    """Records every call; replies are scripted in order of methods."""

    def __init__(self, scripts: dict):
        # scripts: {"getUpdates": [reply_1, reply_2, ...]}
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls = []

    def __call__(self, token, method, params, **_kw):
        self.calls.append({"token": token, "method": method, "params": dict(params)})
        replies = self.scripts.get(method, [])
        if not replies:
            return {"ok": True, "result": []}
        return replies.pop(0)


def test_skip_backlog_fast_forwards_offset_before_first_poll(tmp_path: Path):
    """First boot should drop pre-existing updates; only post-start traffic is processed."""
    http = _FakeHTTP({
        "getUpdates": [
            # 1st call — fast-forward probe (offset=-1)
            {"ok": True, "result": [{"update_id": 117, "message": {"chat": {"id": 1}}}]},
            # 2nd call — the real poll. New message arrives AFTER the bot started.
            {"ok": True, "result": [{
                "update_id": 200,
                "message": {
                    "chat": {"id": 42}, "from": {"id": 7, "username": "alice"},
                    "text": "hi",
                },
            }]},
        ],
    })
    ch = TelegramIntakeChannel(
        vault=_vault(), attachments_dir=tmp_path,
        http_factory=http, long_poll_timeout=1,
    )
    polled = ch.poll()
    assert len(polled) == 1
    assert polled[0].text == "hi"

    # Sanity: the fast-forward call used offset=-1
    assert http.calls[0]["method"] == "getUpdates"
    assert http.calls[0]["params"]["offset"] == -1
    # And the second call's offset was past 117 — backlog skipped
    assert http.calls[1]["params"]["offset"] == 118


def test_skip_backlog_with_empty_history_uses_offset_zero(tmp_path: Path):
    http = _FakeHTTP({"getUpdates": [
        {"ok": True, "result": []},   # probe — no history
        {"ok": True, "result": []},   # real poll — also empty
    ]})
    ch = TelegramIntakeChannel(
        vault=_vault(), attachments_dir=tmp_path,
        http_factory=http, long_poll_timeout=1,
    )
    assert ch.poll() == []
    assert http.calls[1]["params"]["offset"] == 0  # nothing to skip past


def test_skip_backlog_disabled_processes_existing_updates(tmp_path: Path):
    http = _FakeHTTP({"getUpdates": [
        {"ok": True, "result": [{
            "update_id": 5,
            "message": {
                "chat": {"id": 1}, "from": {"id": 2, "username": "bob"},
                "text": "old message",
            },
        }]},
    ]})
    ch = TelegramIntakeChannel(
        vault=_vault(), attachments_dir=tmp_path,
        http_factory=http, long_poll_timeout=1,
        skip_backlog=False,
    )
    polled = ch.poll()
    assert len(polled) == 1
    assert polled[0].text == "old message"
    # No probe call was made
    assert http.calls[0]["params"]["offset"] == 0


def test_fast_forward_failure_falls_back_to_normal_poll(tmp_path: Path):
    class _BrokenProbeHTTP:
        def __init__(self):
            self.calls = 0
        def __call__(self, token, method, params, **_kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("network blip on first request")
            return {"ok": True, "result": []}
    ch = TelegramIntakeChannel(
        vault=_vault(), attachments_dir=tmp_path,
        http_factory=_BrokenProbeHTTP(), long_poll_timeout=1,
    )
    # Should not raise — even if the probe fails, normal poll proceeds
    assert ch.poll() == []
