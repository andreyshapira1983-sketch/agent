"""`:remember` takes tags only from an explicit form; the help says `:forget` needs a target."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cli.command_dispatch import handle_meta_command
from cli.parsers import _parse_remember


@pytest.mark.parametrize(("text", "tags", "content"), [
    ("[project] Alpha is due", ["project"], "Alpha is due"),
    ("[Fact] the build was red", ["fact"], "the build was red"),
    ("[fact,decision] shipped v2", ["fact", "decision"], "shipped v2"),
    ("[важное] текст заметки", ["важное"], "текст заметки"),
    ("[release-2] cut on Monday", ["release-2"], "cut on Monday"),
    ("preference,fact I prefer concise Russian answers", ["preference", "fact"],
     "I prefer concise Russian answers"),
    ("важное,факт текст заметки", ["важное", "факт"], "текст заметки"),
])
def test_an_explicit_tag_form_is_read_as_tags(text: str, tags: list[str], content: str) -> None:
    assert _parse_remember(text) == (tags, content)


def test_tags_without_a_note_still_print_usage(tmp_path, capsys) -> None:
    agent = SimpleNamespace(remember=lambda **k: pytest.fail("nothing to store"))
    handle_meta_command(":remember [fact]", agent, tmp_path)
    err = capsys.readouterr().err
    assert ":remember [fact] " in err and ":remember preference,fact " in err


def test_the_dispatcher_stores_the_whole_note(tmp_path) -> None:
    stored: list[dict] = []

    def remember(**kwargs):
        stored.append(kwargs)
        return SimpleNamespace(decision="save", reasons=["ok"]), SimpleNamespace(id="mem_1")

    handle_meta_command(":remember project Alpha is due", SimpleNamespace(remember=remember), tmp_path)
    assert stored == [
        {"content": "project Alpha is due", "tags": ["user-approved"], "source": "user-explicit"}
    ]
