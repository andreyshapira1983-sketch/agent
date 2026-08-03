"""`:rollback`, `:hygiene`, `:smart-memory`, `:memory-consolidate` — memory upkeep.

These commands delete persistent records, prune episodes and remove backup
files, so the flag that matters most is `--dry-run`: it must reach **every**
operation of a bulk run, not just the first one. A dry run that quietly deletes
one of four things is worse than no dry run at all.

Also pinned here: `:rollback` stops on a skipped plan instead of printing
zero-counts as if it had run; unknown `:hygiene` subcommands are refused rather
than silently treated as "all"; `:memory-consolidate` refuses when any of the
three stores is missing; and long persistent records are truncated when listed.

The agent is faked — these handlers only ever drive its public methods.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cli.commands_memory import (
    _handle_hygiene,
    _handle_memory_consolidate,
    _handle_rollback,
    _handle_smart_memory,
    _print_persistent,
)


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def kinds(self):
        return [e for e, _ in self.events]


def rep(**kw):
    return SimpleNamespace(**kw)


class HygieneAgent:
    """Records every hygiene call and the dry_run flag it was given."""

    def __init__(self):
        self.calls: list[tuple[str, bool]] = []
        self.log = FakeLog()

    def expire_persistent(self, *, dry_run):
        self.calls.append(("expire", dry_run))
        return rep(expired=["m1"], scanned=3)

    def dedupe_persistent(self, *, dry_run):
        self.calls.append(("dedupe", dry_run))
        return rep(
            deleted=["m2"],
            groups=[rep(canonical_id="m1", duplicate_ids=["m2"])],
            threshold=0.9,
        )

    def prune_episodic(self, *, dry_run):
        self.calls.append(("episodic", dry_run))
        return ["e1", "e2"]

    def cleanup_backups(self, workspace, *, dry_run):
        self.calls.append(("backups", dry_run))
        return rep(deleted=["a.bak"], kept=["b.bak"], scanned=2, keep_last=3, max_age_days=7)

    def summarise_persistent(self, *, tag, dry_run):
        self.calls.append((f"summarise:{tag}", dry_run))
        return rep(skipped_reason=None, summarised_ids=["m1", "m2"], new_record_id="m9")

    def archive_persistent(self, *, threshold, min_age_days, dry_run):
        self.calls.append((f"archive:{threshold}:{min_age_days}", dry_run))
        return rep(scanned=5, archived=["m3"], threshold=threshold, min_age_days=min_age_days)


@pytest.fixture
def hygiene_agent():
    return HygieneAgent()


# ── :hygiene — the dry-run contract ──────────────────────────────────────────

@pytest.mark.parametrize("rest,expected_dry", [("", False), ("--dry-run", True), ("all --dry-run", True)])
def test_bulk_hygiene_passes_the_same_dry_run_to_every_step(
    rest, expected_dry, hygiene_agent, tmp_path, capsys
):
    assert _handle_hygiene(rest, hygiene_agent, tmp_path) is True

    steps = [name for name, _ in hygiene_agent.calls]
    flags = {flag for _, flag in hygiene_agent.calls}
    assert steps == ["expire", "dedupe", "episodic", "backups"], "documented order"
    assert flags == {expected_dry}, "one stray real deletion inside a dry run is the bug this catches"

    err = capsys.readouterr().err
    assert f"hygiene (dry_run={expected_dry})" in err
    assert "expire   : 1 record(s) past TTL" in err
    assert "dedupe   : 1 near-duplicate(s) collapsed (1 group(s))" in err
    assert "episodic : 2 stale episode(s) pruned" in err
    assert "backups  : 1 old .bak.<ts> file(s) removed (scanned 2)" in err


@pytest.mark.parametrize(
    "sub,expected_call",
    [
        ("expire", "expire"),
        ("dedupe", "dedupe"),
        ("backups", "backups"),
        ("episodic", "episodic"),
    ],
)
def test_each_subcommand_runs_only_its_own_step(sub, expected_call, hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene(f"{sub} --dry-run", hygiene_agent, tmp_path) is True

    assert hygiene_agent.calls == [(expected_call, True)]
    assert f"{sub} (dry_run=True)" in capsys.readouterr().err


def test_dry_run_is_recognised_before_the_subcommand(hygiene_agent, tmp_path, capsys):
    """`:hygiene --dry-run expire` must not be read as a subcommand named --dry-run."""
    assert _handle_hygiene("--dry-run expire", hygiene_agent, tmp_path) is True
    assert hygiene_agent.calls == [("expire", True)]


def test_dedupe_lists_its_groups(hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene("dedupe", hygiene_agent, tmp_path) is True
    err = capsys.readouterr().err
    assert "threshold=0.9" in err
    assert "canonical=m1 dups=['m2']" in err


def test_backups_reports_kept_and_deleted(hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene("backups", hygiene_agent, tmp_path) is True
    err = capsys.readouterr().err
    assert "scanned 2 backup(s), deleted 1, kept 1" in err
    assert "keep_last=3, max_age_days=7" in err


def test_summarise_requires_a_tag(hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene("summarise", hygiene_agent, tmp_path) is True

    assert hygiene_agent.calls == [], "no tag means no merge"
    assert "Usage: :hygiene summarise <tag>" in capsys.readouterr().err


def test_summarise_merges_records_for_a_tag(hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene("summarise notes", hygiene_agent, tmp_path) is True

    assert hygiene_agent.calls == [("summarise:notes", False)]
    assert "merged 2 record(s) tagged 'notes' into m9" in capsys.readouterr().err


def test_summarise_reports_a_skip_reason(hygiene_agent, tmp_path, capsys):
    hygiene_agent.summarise_persistent = lambda *, tag, dry_run: rep(
        skipped_reason="only one record", summarised_ids=[], new_record_id=None
    )

    assert _handle_hygiene("summarise notes", hygiene_agent, tmp_path) is True
    assert "skipped — only one record" in capsys.readouterr().err


def test_archive_parses_its_two_options(hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene("archive --threshold=0.25 --min-age=30", hygiene_agent, tmp_path) is True

    assert hygiene_agent.calls == [("archive:0.25:30", False)]
    err = capsys.readouterr().err
    assert "archived 1 low-value record(s)" in err
    assert "threshold=0.25, min_age_days=30" in err


def test_archive_without_options_uses_the_agent_defaults(hygiene_agent, tmp_path):
    assert _handle_hygiene("archive", hygiene_agent, tmp_path) is True
    assert hygiene_agent.calls == [("archive:None:None", False)]


def test_archive_ignores_an_argument_it_does_not_understand(hygiene_agent, tmp_path):
    """An unrecognised word must not silently become a threshold."""
    assert _handle_hygiene("archive --deep --threshold=0.5", hygiene_agent, tmp_path) is True
    assert hygiene_agent.calls == [("archive:0.5:None", False)]


def test_unknown_subcommand_is_refused_not_treated_as_all(hygiene_agent, tmp_path, capsys):
    assert _handle_hygiene("purge-everything", hygiene_agent, tmp_path) is True

    assert hygiene_agent.calls == [], "an unknown word must never fall through to a bulk run"
    assert "(unknown :hygiene subcommand: 'purge-everything')" in capsys.readouterr().err


# ── :rollback ────────────────────────────────────────────────────────────────

def _plan(plan_id="cp-1"):
    return SimpleNamespace(
        id=plan_id,
        tool_name="file_write",
        description="restore core/foo.py",
        actions=[SimpleNamespace(kind="restore", path="core/foo.py", backup_path=None)],
    )


def test_rollback_list_when_nothing_is_registered(tmp_path, capsys):
    agent = SimpleNamespace(compensation_log=[], log=FakeLog())
    assert _handle_rollback("list", agent, tmp_path) is True
    assert "(no compensation plans registered)" in capsys.readouterr().err


def test_rollback_list_previews_each_plan(tmp_path, capsys):
    agent = SimpleNamespace(compensation_log=[_plan("cp-1"), _plan("cp-2")], log=FakeLog())

    assert _handle_rollback("LIST", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "=== compensation log (2 plan(s), oldest first) ===" in err
    assert "cp-1  tool=file_write  actions=[restore:core/foo.py]" in err
    assert "restore core/foo.py" in err


def test_rollback_without_an_id_asks_for_the_latest_plan(tmp_path, capsys):
    seen: dict = {}

    def _rollback(*, plan_id, workspace_root):
        seen["plan_id"] = plan_id
        seen["workspace"] = workspace_root
        return SimpleNamespace(
            plan_id="cp-9",
            summary=lambda: {
                "skipped_reason": None,
                "ok": 1,
                "noop": 0,
                "error": 0,
                "outcomes": [
                    {"status": "ok", "kind": "restore", "path": "core/foo.py", "detail": "restored"}
                ],
            },
        )

    agent = SimpleNamespace(compensation_log=[], rollback=_rollback, log=FakeLog())

    assert _handle_rollback("", agent, tmp_path) is True

    assert seen == {"plan_id": None, "workspace": tmp_path}
    err = capsys.readouterr().err
    assert "rollback cp-9: ok=1, noop=0, error=0" in err
    assert "[ok] restore core/foo.py — restored" in err


def test_rollback_with_an_explicit_id(tmp_path, capsys):
    seen: dict = {}
    agent = SimpleNamespace(
        compensation_log=[],
        rollback=lambda *, plan_id, workspace_root: seen.setdefault("plan_id", plan_id)
        and None
        or SimpleNamespace(
            plan_id=plan_id,
            summary=lambda: {"skipped_reason": None, "ok": 0, "noop": 1, "error": 0, "outcomes": []},
        ),
        log=FakeLog(),
    )

    assert _handle_rollback("cp-3", agent, tmp_path) is True
    assert seen["plan_id"] == "cp-3"
    assert "rollback cp-3: ok=0, noop=1, error=0" in capsys.readouterr().err


def test_a_skipped_rollback_says_why_and_prints_no_counts(tmp_path, capsys):
    agent = SimpleNamespace(
        compensation_log=[],
        rollback=lambda *, plan_id, workspace_root: SimpleNamespace(
            plan_id="cp-4",
            summary=lambda: {"skipped_reason": "plan already applied"},
        ),
        log=FakeLog(),
    )

    assert _handle_rollback("cp-4", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "rollback skipped: plan already applied" in err
    assert "ok=" not in err, "a skipped rollback must not print zero-counts as if it ran"


# ── _print_persistent ────────────────────────────────────────────────────────

def test_persistent_listing_when_empty(capsys):
    _print_persistent(SimpleNamespace(list_persistent=list))
    assert "(no persistent records)" in capsys.readouterr().err


def test_persistent_listing_truncates_and_renders_both_content_shapes(capsys):
    records = [
        SimpleNamespace(id="m1", type="fact", tags=["a", "b"], content="x" * 500),
        SimpleNamespace(id="m2", type="pref", tags=[], content={"k": "значение"}),
    ]
    _print_persistent(SimpleNamespace(list_persistent=lambda: records))

    out = capsys.readouterr().out
    assert "=== persistent memory (2 records) ===" in out
    assert "[m1] type=fact tags=a,b" in out
    assert "…" in out and "x" * 200 not in out, "long content is cut at 199 chars"
    assert "[m2] type=pref tags=-" in out
    assert '"k": "значение"' in out, "dict content is rendered as readable JSON"


# ── :smart-memory ────────────────────────────────────────────────────────────

SMART = {
    "episodic": {"episodes": 4, "outcomes": 3, "path": "data/episodic.jsonl"},
    "procedural": {"procedures": 2, "statuses": {"ok": 2}, "path": "data/procedural.jsonl"},
    "consolidation": {"reports": 1, "path": "data/consolidation.jsonl"},
}


def test_smart_memory_rejects_unknown_flags(capsys):
    agent = SimpleNamespace(
        log=FakeLog(), smart_memory_summary=lambda: pytest.fail("must not be read")
    )
    assert _handle_smart_memory("--all", agent) is True
    assert "Usage: :smart-memory [--json]" in capsys.readouterr().err


def test_smart_memory_prints_all_three_stores(capsys):
    agent = SimpleNamespace(log=FakeLog(), smart_memory_summary=lambda: dict(SMART))

    assert _handle_smart_memory("", agent) is True

    err = capsys.readouterr().err
    assert "episodic: episodes=4 outcomes=3" in err
    assert "procedural: procedures=2" in err
    assert "consolidation: reports=1" in err
    assert "last consolidation:" not in err
    assert agent.log.kinds() == ["smart_memory_status"]


def test_smart_memory_shows_the_last_consolidation_notes(capsys):
    payload = {**SMART, "consolidation": {**SMART["consolidation"], "last_report": {"notes": ["merged 2", "pruned 1"]}}}
    agent = SimpleNamespace(log=FakeLog(), smart_memory_summary=lambda: payload)

    assert _handle_smart_memory("", agent) is True
    assert "last consolidation: merged 2; pruned 1" in capsys.readouterr().err


def test_smart_memory_json(capsys):
    agent = SimpleNamespace(log=FakeLog(), smart_memory_summary=lambda: dict(SMART))
    assert _handle_smart_memory("--json", agent) is True
    assert json.loads(capsys.readouterr().err) == SMART


# ── :memory-consolidate ──────────────────────────────────────────────────────

def _consolidate_agent(**overrides):
    saved: list = []
    base = SimpleNamespace(
        log=FakeLog(),
        episodic_store=SimpleNamespace(load=lambda: ["e1"]),
        procedural_store=SimpleNamespace(load=lambda: ["p1"]),
        consolidation_store=SimpleNamespace(save=saved.append),
        saved=saved,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_consolidate_rejects_unknown_flags(capsys):
    assert _handle_memory_consolidate("--force", _consolidate_agent()) is True
    assert "Usage: :memory-consolidate [--json]" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["episodic_store", "procedural_store", "consolidation_store"])
def test_consolidate_refuses_when_any_store_is_missing(missing, capsys):
    agent = _consolidate_agent(**{missing: None})

    assert _handle_memory_consolidate("", agent) is True

    assert "(smart memory stores are not configured)" in capsys.readouterr().err
    assert agent.saved == []
    assert agent.log.kinds() == []


def test_consolidate_saves_logs_and_prints_its_notes(capsys, monkeypatch):
    import cli.commands_memory as mod

    report = SimpleNamespace(
        id="cons-1",
        episode_count=4,
        procedure_count=2,
        notes=["merged 2 episodes", "kept 1 procedure"],
        to_dict=lambda: {"id": "cons-1", "episodes": 4},
    )
    seen: dict = {}

    def _consolidate(*, episodes, procedures):
        seen["episodes"] = episodes
        seen["procedures"] = procedures
        return report

    monkeypatch.setattr(mod, "consolidate_memory", _consolidate)
    agent = _consolidate_agent()

    assert _handle_memory_consolidate("", agent) is True

    assert seen == {"episodes": ["e1"], "procedures": ["p1"]}
    assert agent.saved == [report], "the report is persisted, not just printed"
    assert agent.log.kinds() == ["memory_consolidation"]

    err = capsys.readouterr().err
    assert "report=cons-1 episodes=4 procedures=2" in err
    assert "- merged 2 episodes" in err
    assert "- kept 1 procedure" in err


def test_consolidate_json_mode(capsys, monkeypatch):
    import cli.commands_memory as mod

    report = SimpleNamespace(
        id="cons-2", episode_count=0, procedure_count=0, notes=[], to_dict=lambda: {"id": "cons-2"}
    )
    monkeypatch.setattr(mod, "consolidate_memory", lambda **kw: report)
    agent = _consolidate_agent()

    assert _handle_memory_consolidate("--json", agent) is True

    assert json.loads(capsys.readouterr().err) == {"id": "cons-2"}
    assert agent.saved == [report]
