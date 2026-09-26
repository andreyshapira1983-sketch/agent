"""Одобрение попадает в реестр производителя, только если заявка пришла от производителя."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import core.subagent_registry as reg_mod
from cli.commands_approval import _record_producer_approval
from core.self_build_producer import PRODUCER_ORIGIN


class _Spy:
    def __init__(self) -> None:
        self.loads: list[Path] = []
        self.outcomes: list[tuple[str, str]] = []

    def load(self, workspace: Path) -> _Spy:
        self.loads.append(workspace)
        return self

    def apply_lane_outcome(self, item_id: str, outcome: str) -> None:
        self.outcomes.append((item_id, outcome))


@pytest.fixture()
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    spy = _Spy()
    monkeypatch.setattr(reg_mod.SubagentRegistry, "load", staticmethod(spy.load))
    return spy


@pytest.mark.parametrize("item", [
    SimpleNamespace(id="a1", operation="something_else", payload={"origin": PRODUCER_ORIGIN}),
    SimpleNamespace(id="a2", operation="self_apply_lane.run", payload={"origin": "human"}),
    SimpleNamespace(id="a3", operation="self_apply_lane.run", payload=None),
], ids=["other_operation", "human_origin", "no_payload"])
def test_a_foreign_approval_never_opens_the_ledger(tmp_path: Path, spy: _Spy, item) -> None:
    """Заявка не от производителя не открывает реестр и ничего в нём не отмечает."""
    _record_producer_approval(tmp_path, item)

    assert spy.loads == []
    assert spy.outcomes == []


def test_a_producer_approval_is_recorded_as_approved(tmp_path: Path, spy: _Spy) -> None:
    """Контроль: одобрение заявки производителя записывается как approved."""
    item = SimpleNamespace(id="p1", operation="self_apply_lane.run", payload={"origin": PRODUCER_ORIGIN})

    _record_producer_approval(tmp_path, item)

    assert spy.outcomes == [("p1", "approved")]
