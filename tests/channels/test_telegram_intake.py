"""Tests for channels/telegram_intake.py — TelegramIntakeChannel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from brain.secrets import SecretsVault
from brain.skills.job import Job, JobStatus, JobStore
from brain.skills.workflow_runner import JobOutcome
from channels.telegram_intake import (
    PolledMessage,
    TelegramIntakeChannel,
    TelegramIntakeError,
)


# ════════════════════════════════════════════════════════════════════
# Fakes
# ════════════════════════════════════════════════════════════════════

class FakeHTTP:
    """Programmable replacement for the real Telegram HTTP transport."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, token: str, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        if method not in self.responses:
            return {"ok": False, "description": f"no fake for {method}"}
        return self.responses[method]


def _make_vault() -> SecretsVault:
    v = SecretsVault()
    v.set("TELEGRAM_BOT_TOKEN", "fake-token-abc")
    return v


class _FakeBrain:
    def __init__(self):
        self.calls: list[Job] = []
    def intake_job(self, job: Job) -> JobOutcome:
        self.calls.append(job)
        return JobOutcome(
            job_id=job.id, profession_id="text_editor",
            final_status=JobStatus.DELIVERED,
        )


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════

class TestPoll:

    def test_text_message(self, tmp_path):
        http = FakeHTTP({
            "getUpdates": {
                "ok": True,
                "result": [{
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200, "username": "client"},
                        "text": "please edit",
                    },
                }],
            },
        })
        ch = TelegramIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            http_factory=http,
        )
        polled = ch.poll()
        assert len(polled) == 1
        assert polled[0].text == "please edit"
        assert polled[0].chat_id == 100
        assert polled[0].username == "client"

    def test_offset_advances(self, tmp_path):
        http = FakeHTTP({
            "getUpdates": {
                "ok": True,
                "result": [
                    {"update_id": 5, "message": {"chat": {"id": 1}, "from": {"id": 1}, "text": "a"}},
                    {"update_id": 7, "message": {"chat": {"id": 1}, "from": {"id": 1}, "text": "b"}},
                ],
            },
        })
        ch = TelegramIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            http_factory=http,
        )
        ch.poll()
        # Next poll should ask with offset = 8
        ch.poll()
        params = http.calls[1][1]
        assert params["offset"] == 8

    def test_missing_token_raises(self, tmp_path):
        ch = TelegramIntakeChannel(
            vault=SecretsVault(),    # empty vault
            attachments_dir=tmp_path,
            http_factory=FakeHTTP({}),
        )
        with pytest.raises(TelegramIntakeError):
            ch.poll()

    def test_not_ok_raises(self, tmp_path):
        http = FakeHTTP({"getUpdates": {"ok": False, "description": "unauthorized"}})
        ch = TelegramIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            http_factory=http,
        )
        with pytest.raises(TelegramIntakeError):
            ch.poll()


class TestToJob:

    def test_constructs_job(self, tmp_path):
        ch = TelegramIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            http_factory=FakeHTTP({}),
        )
        msg = PolledMessage(
            update_id=1, chat_id=42, user_id=7,
            username="alice", text="hello",
            attachments=[tmp_path / "x.docx"],
        )
        job = ch.to_job(msg)
        assert job.source == "telegram"
        assert "42" in job.client_id
        assert "alice" in job.client_id
        assert job.brief == "hello"
        assert len(job.attachments) == 1


class TestDispatch:

    def test_poll_and_dispatch(self, tmp_path):
        http = FakeHTTP({
            "getUpdates": {
                "ok": True,
                "result": [{
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 1}, "from": {"id": 1, "username": "u"},
                        "text": "edit me",
                    },
                }],
            },
        })
        ch = TelegramIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            http_factory=http,
        )
        brain = _FakeBrain()
        store = JobStore(tmp_path / "jobs.db")
        try:
            results = ch.poll_and_dispatch(brain, store)
        finally:
            store.close()
        assert len(results) == 1
        assert brain.calls[0].source == "telegram"
