"""Средние дефекты памяти (проверка 16.11, средняя, группа 2).

1. Многострочный текст записи подделывал в подсказке отдельную запись.
2. Объяснение прохода гигиены говорило «ничего не уничтожено» после удалений.
3. Одна битая строка журнала эха роняла load(); дверь молча выключала эхо.
4. `:hygiene` без флагов удалял по-настоящему; опечатки во флагах не замечались.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

import pytest

from cli.commands_memory import _handle_hygiene
from core import memory_door
from core.memory_echo_antibody import MemoryWriteRegistry, make_event
from core.memory_hygiene_commands import run_maintenance_pass
from core.models import MemoryRecord
from core.persistent_memory import PersistentMemoryStore

# ── 2. правдивое объяснение гигиены ──────────────────────────────────────────

class _Log:
    def __init__(self):
        self.events = []

    def log(self, ev, data=None):
        self.events.append((ev, data))


def _explained(log):
    return next(d["full_text"] for ev, d in log.events if ev == "hygiene_explained")


def test_hygiene_explanation_names_permanent_deletions(tmp_path):
    store = PersistentMemoryStore(tmp_path / "mem.jsonl")
    for _ in range(2):
        store.save(MemoryRecord(content="одинаковая запись о сборке проекта", tags=["fact"]))
    log = _Log()
    rep = run_maintenance_pass(log=log, persistent_store=store, episodic_store=None,
                               assumption_store=None, suppressed_reason=None, dry_run=False)
    assert rep["deduped"] == 1
    txt = _explained(log)
    assert "ничего не уничтожено" not in txt
    assert "удалено навсегда: 1" in txt, txt


def test_hygiene_explanation_without_deletions_keeps_the_reassurance(tmp_path):
    store = PersistentMemoryStore(tmp_path / "mem.jsonl")
    store.save(MemoryRecord(content="единственная запись о сборке проекта", tags=["fact"]))
    log = _Log()
    run_maintenance_pass(log=log, persistent_store=store, episodic_store=None,
                         assumption_store=None, suppressed_reason=None, dry_run=False)
    assert "ничего не уничтожено" in _explained(log)


# ── 3. журнал эха и дверь ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [b"[1, 2]\n", b'"x"\n', b"\xff\xfe broken\n", b"42\n", b"null\n"])
def test_echo_registry_skips_a_corrupt_line(tmp_path, bad):
    p = tmp_path / "w.jsonl"
    good = json.dumps(make_event("hello world").to_dict()).encode() + b"\n"
    p.write_bytes(good + bad + good)
    assert len(MemoryWriteRegistry(p).load()) == 2


class _RefusingPolicy:
    def decide(self, text, tags, **kw):
        return NS(decision="skip", reasons=["too thin"])


class _SavingPolicy:
    def decide(self, text, tags, **kw):
        return NS(decision="save", reasons=[])


def _broken_recent(self, **kw):
    raise RuntimeError("journal unreadable")


TEXT = "Сборка проекта на сервере прошла без ошибок после смены версии компилятора"


def test_door_reports_an_echo_gate_failure_on_refusal(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(MemoryWriteRegistry, "recent", _broken_recent)
    before = memory_door.ECHO_GATE_FAILURES["count"]
    store = PersistentMemoryStore(tmp_path / "mem.jsonl")
    with caplog.at_level(logging.WARNING, logger="core.memory_door"):
        mid, why = memory_door.memory_door_verdict(
            store, _RefusingPolicy(), TEXT, "[ВЫВОД, проверен боем]", "run_1")
    assert mid is None
    assert "echo gate off" in why and "RuntimeError" in why, why
    assert memory_door.ECHO_GATE_FAILURES["count"] == before + 1
    assert "RuntimeError" in memory_door.ECHO_GATE_FAILURES["last"]
    assert any("echo gate" in r.getMessage() for r in caplog.records)


def test_door_still_writes_when_the_echo_log_is_broken(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryWriteRegistry, "recent", _broken_recent)
    before = memory_door.ECHO_GATE_FAILURES["count"]
    store = PersistentMemoryStore(tmp_path / "mem.jsonl")
    mid, why = memory_door.memory_door_verdict(
        store, _SavingPolicy(), TEXT, "[ВЫВОД, проверен боем]", "run_1")
    assert mid is not None and why == ""
    assert memory_door.ECHO_GATE_FAILURES["count"] == before + 1


# ── 4. :hygiene — сухой прогон по умолчанию, строгие флаги ──────────────────

class _Agent:
    def __init__(self):
        self.calls = []

    def expire_persistent(self, dry_run):
        self.calls.append(("expire", dry_run))
        return NS(expired=[], scanned=0)

    def dedupe_persistent(self, dry_run):
        self.calls.append(("dedupe", dry_run))
        return NS(deleted=[], groups=[], threshold=0.9)

    def prune_episodic(self, dry_run):
        self.calls.append(("prune_episodic", dry_run))
        return []

    def dedupe_episodic(self, dry_run):
        self.calls.append(("dedupe_episodic", dry_run))
        return []

    def cleanup_backups(self, ws, dry_run):
        self.calls.append(("backups", dry_run))
        return NS(deleted=[], scanned=0, kept=[], keep_last=0, max_age_days=0)

    def summarise_persistent(self, tag, dry_run):
        self.calls.append(("summarise", dry_run))
        return NS(skipped_reason=None, summarised_ids=[], new_record_id="m1")

    def archive_persistent(self, threshold, min_age_days, dry_run):
        self.calls.append(("archive", threshold, dry_run))
        return NS(scanned=0, archived=[], threshold=threshold, min_age_days=min_age_days)


@pytest.mark.parametrize("cmd", ["", "all", "expire", "dedupe", "backups", "episodic",
                                 "summarise notes", "archive"])
def test_hygiene_without_apply_is_a_dry_run(cmd, tmp_path, capsys):
    agent = _Agent()
    assert _handle_hygiene(cmd, agent, tmp_path) is True
    assert agent.calls, "сухой прогон всё же считает"
    assert all(c[-1] is True for c in agent.calls), agent.calls
    assert "--apply" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", [
    "expire --dryrun", "expire --dryrun --apply", "archive --treshold=0.1 --apply",
    "all --force", "dedupe --apply extra", "--apply --dry-run",
    "archive --deep --threshold=0.5 --apply",
])
def test_hygiene_unknown_or_conflicting_flags_do_nothing(cmd, tmp_path, capsys):
    agent = _Agent()
    assert _handle_hygiene(cmd, agent, tmp_path) is True
    assert agent.calls == [], cmd
    assert capsys.readouterr().err.strip()


@pytest.mark.parametrize("value", ["inf", "-inf", "0", "-0.1", "1.5", "nan"])
def test_hygiene_archive_threshold_must_be_finite_in_unit_interval(value, tmp_path, capsys):
    agent = _Agent()
    _handle_hygiene(f"archive --threshold={value} --apply", agent, tmp_path)
    assert agent.calls == []
    assert "Usage: :hygiene archive" in capsys.readouterr().err
