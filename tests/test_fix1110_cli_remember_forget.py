"""`:remember` keeps prose with a comma whole; `:forget` needs an explicit target."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cli.command_dispatch import handle_meta_command


def _forget_agent() -> tuple[SimpleNamespace, list]:
    calls: list = []

    def forget(record_id=None):
        calls.append(record_id)
        return 3 if record_id is None else 1

    return SimpleNamespace(forget=forget), calls


def test_forget_without_argument_refuses(tmp_path, capsys):
    agent, calls = _forget_agent()
    assert handle_meta_command(":forget", agent, tmp_path) is True
    assert calls == []
    err = capsys.readouterr().err
    assert "nothing deleted" in err
    assert ":forget --all" in err


@pytest.mark.parametrize("word", ["--all", "all", "ALL"])
def test_forget_all_needs_the_explicit_word(tmp_path, capsys, word):
    agent, calls = _forget_agent()
    assert handle_meta_command(f":forget {word}", agent, tmp_path) is True
    assert calls == [None]
    assert "deleted 3 persistent records" in capsys.readouterr().err


def test_forget_by_id_is_unchanged(tmp_path, capsys):
    agent, calls = _forget_agent()
    handle_meta_command(":forget mem_abc", agent, tmp_path)
    assert calls == ["mem_abc"]
    assert "(deleted mem_abc)" in capsys.readouterr().err
