"""Tests for `_warmup_checks` in runtime.agent_runtime — startup self-checks."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from brain.interfaces.llm_interface import LLMInterface
from brain.planner import Plan, PlanCheckpointStore, PlanStatus, Step, StepStatus
from brain.secrets import SecretsVault
from runtime.agent_runtime import build_runtime
from runtime.config import AgentConfig


class _FakeLLM(LLMInterface):
    def call(self, _ctx):
        return {"action": "respond", "content": "ok", "confidence": 1, "reasoning": ""}
    def is_available(self): return True
    @property
    def model_name(self): return "fake"


def _cfg(tmp_path: Path) -> AgentConfig:
    cfg = AgentConfig(
        workspace=tmp_path,
        data_dir=tmp_path / "data",
        attachments_dir=tmp_path / "data" / "attachments",
        professions_dir=Path.cwd() / "professions",
    )
    cfg.openai_ready = True
    return cfg


# ────────────────────────────────────────────────────────────────────


def test_boot_notes_audit_chain_ok(tmp_path):
    rt = build_runtime(_cfg(tmp_path), SecretsVault(), llm=_FakeLLM())
    assert any("audit: chain ok" in n for n in rt.notes)


def test_boot_notes_audit_chain_broken(tmp_path, caplog):
    """Tamper with the audit DB, rebuild, expect a critical log + note."""
    cfg = _cfg(tmp_path)
    # First build to create the DB
    rt = build_runtime(cfg, SecretsVault(), llm=_FakeLLM())
    rt.audit.record(actor="x", action="y", target="z", verdict="OK", params={})
    rt.close()

    # Corrupt one row's hash
    import sqlite3
    conn = sqlite3.connect(str(cfg.data_dir / "audit.db"))
    conn.execute("UPDATE audit_entries SET entry_hash = 'BROKEN' WHERE seq = 1")
    conn.commit()
    conn.close()

    with caplog.at_level(logging.CRITICAL):
        rt2 = build_runtime(cfg, SecretsVault(), llm=_FakeLLM())
    assert any("CHAIN BROKEN" in n for n in rt2.notes)


def test_boot_warns_about_unfinished_plans(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    store = PlanCheckpointStore(cfg.data_dir / "plans.db")
    plan = Plan(
        goal="finish the thing",
        steps=[Step(id=1, description="write", action="write_file",
                    status=StepStatus.PENDING)],
        status=PlanStatus.ACTIVE,
    )
    store.save("job-xyz", plan)
    store._conn.close()  # release handle before runtime opens its own

    with caplog.at_level(logging.WARNING):
        rt = build_runtime(cfg, SecretsVault(), llm=_FakeLLM())
    assert any("unfinished" in n for n in rt.notes)
    # job_id surfaces in /status for inspection
    assert "job-xyz" in rt.status()["unfinished_plans"]


def test_boot_no_unfinished_plans_means_no_note(tmp_path):
    rt = build_runtime(_cfg(tmp_path), SecretsVault(), llm=_FakeLLM())
    assert not any("unfinished" in n for n in rt.notes)
    assert rt.status()["unfinished_plans"] == []
