"""Integration test for runtime.agent_runtime.build_runtime.

We don't hit any real network — `llm=` is overridden with a fake adapter
so the assembly produces a fully-wired AgentRuntime that we can poke at.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.interfaces.llm_interface import LLMInterface
from brain.secrets import SecretsVault
from runtime.agent_runtime import build_runtime
from runtime.config import AgentConfig


class _FakeLLM(LLMInterface):
    def call(self, context):
        return {"action": "respond", "content": "hello", "confidence": 1.0, "reasoning": ""}

    def is_available(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "fake"


def _config(tmp_path: Path) -> AgentConfig:
    cfg = AgentConfig(
        workspace=tmp_path,
        data_dir=tmp_path / "data",
        attachments_dir=tmp_path / "data" / "attachments",
        professions_dir=Path.cwd() / "professions",   # the real ones
    )
    cfg.openai_ready = True
    cfg.telegram_ready = False
    cfg.email_ready = False
    cfg.imap_ready = False
    return cfg


def test_build_runtime_wires_brain_and_registries(tmp_path):
    cfg = _config(tmp_path)
    vault = SecretsVault()
    rt = build_runtime(cfg, vault, llm=_FakeLLM())
    assert rt.brain is not None
    assert rt.skill_registry is not None
    assert rt.tool_registry is not None
    assert "docx_reader" in rt.tool_registry.names()
    assert "email" in rt.tool_registry.names()
    # Professions actually loaded
    assert "text_editor" in rt.skill_registry.profession_ids()


def test_runtime_status_reports_channel_readiness(tmp_path):
    cfg = _config(tmp_path)
    rt = build_runtime(cfg, SecretsVault(), llm=_FakeLLM())
    status = rt.status()
    assert status["config"]["openai_ready"] is True
    assert status["channels"]["telegram_in"] is False
    assert status["channels"]["email_in"] is False
    assert status["channels"]["email_out"] is False
    assert "welcomed_clients" in status
    assert "unfinished_plans" in status


def test_build_runtime_skips_telegram_when_unconfigured(tmp_path):
    cfg = _config(tmp_path)
    rt = build_runtime(cfg, SecretsVault(), llm=_FakeLLM())
    assert rt.telegram_intake is None
    assert rt.telegram_sender is None
    assert any("telegram" in n for n in rt.notes)


def test_build_runtime_wires_telegram_when_token_present(tmp_path):
    cfg = _config(tmp_path)
    cfg.telegram_ready = True
    vault = SecretsVault()
    vault.set("TELEGRAM_BOT_TOKEN", "0:test")
    rt = build_runtime(cfg, vault, llm=_FakeLLM())
    assert rt.telegram_intake is not None
    assert rt.telegram_sender is not None


def test_build_runtime_wires_email_when_imap_ready(tmp_path):
    cfg = _config(tmp_path)
    cfg.imap_ready = True
    vault = SecretsVault()
    vault.set("IMAP_USERNAME", "me@example.com")
    vault.set("IMAP_PASSWORD", "pw")
    vault.set("IMAP_HOST", "imap.gmail.com")
    vault.set("IMAP_PORT", "993")
    rt = build_runtime(cfg, vault, llm=_FakeLLM())
    assert rt.email_intake is not None


def test_build_runtime_refuses_without_openai_key(tmp_path):
    cfg = _config(tmp_path)
    cfg.openai_ready = False
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY missing"):
        build_runtime(cfg, SecretsVault())


def test_chat_handler_is_wired_to_brain(tmp_path):
    cfg = _config(tmp_path)
    rt = build_runtime(cfg, SecretsVault(), llm=_FakeLLM())
    # First contact: the welcome book prepends a small intro. Second message
    # from the same client is the LLM reply unadorned.
    rt.chat.handle(brief="hi", client_id="alice")
    reply = rt.chat.handle(brief="again", client_id="alice")
    assert reply.text == "hello"
    assert reply.used_workflow is False
