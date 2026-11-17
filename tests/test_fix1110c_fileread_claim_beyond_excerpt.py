"""Утверждение, чей литерал стоит в прочитанном файле дальше выдержки, подтверждается — и только оно."""
from __future__ import annotations

import re

from core.evidence import MAX_EXCERPT_CHARS, ProvenanceChain, evidence_from_tool_result
from core.verifier import verify
from core.verifier_utils import claim_view
from tools.file_read import FileReadTool

_LINES = [f"строка {n}: значение {n * 7}" for n in range(1, 61)]
_LINES[54] = "строка 55: код склада: ЯБЛОКО-4417"
_FILE = "\n".join(_LINES) + "\n"
_TOKEN = "ghp_" + "a1B2c3D4e5" * 4


class _Judge:
    """Судья по смыслу: «да», только если значение утверждения дословно стоит в показанной выдержке."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, *, user: str, **_: object) -> str:
        self.prompts.append(user)
        shown, _sep, claim = user.partition("\n\nClaim:")
        values = re.findall(r"[А-ЯЁ]+-\d+|\d{3,}", claim.split("[", 1)[0])
        return "yes" if values and all(v in shown for v in values) else "no"


def _evidence(output: str, **window: int):
    ev = evidence_from_tool_result(
        tool_name="file_read", arguments={"path": "long.txt", **window}, output=output)
    assert ev is not None
    return ev


def _verdict(claim: str, output: str, cite: str = "file:long.txt",
             judge: _Judge | None = None, **window: int) -> str:
    report = verify(answer=f"Conclusion:\n{claim} [{cite}].\n",
                    chain=ProvenanceChain([_evidence(output, **window)]),
                    entailment_llm=judge or _Judge())
    assert report.total_chunks == 1, report.annotated_answer
    return report.chunks[0].verdict


def test_the_file_is_longer_than_the_stored_excerpt() -> None:
    ev = _evidence(_FILE)
    assert _FILE.index("4417") > MAX_EXCERPT_CHARS and "4417" not in ev.excerpt
    assert "source_text" not in ev.to_dict(), "полный текст не идёт на диск"
    assert ev.source_text == _FILE, "ровно то, что вернул инструмент"


def test_a_literal_standing_deep_in_the_file_is_verified() -> None:
    assert _verdict("Код склада в файле long.txt — ЯБЛОКО-4417", _FILE) == "verified"
    assert _verdict("В строке 60 записано значение 420", _FILE) == "verified"


def test_a_literal_absent_from_the_file_is_not_verified() -> None:
    assert "4471" not in _FILE and "9000" not in _FILE
    assert _verdict("Код склада в файле long.txt — ЯБЛОКО-4471", _FILE) != "verified"
    assert _verdict("Код склада в файле long.txt — ГРУША-9000", _FILE) != "verified"


def test_a_window_read_is_judged_by_the_window_only(tmp_path) -> None:
    (tmp_path / "long.txt").write_text(_FILE, encoding="utf-8")
    window = FileReadTool(tmp_path).run(path="long.txt", start_line=1, end_line=50)
    assert "4417" not in window and len(window) > MAX_EXCERPT_CHARS
    assert _evidence(window, start_line=1, end_line=50).source_text == window
    cite = {"cite": "file:long.txt:1-50", "start_line": 1, "end_line": 50}
    assert _verdict("Код склада в файле long.txt — ЯБЛОКО-4417", window, **cite) != "verified"
    assert _verdict("В строке 49 записано значение 343", window, **cite) == "verified"


def test_a_secret_in_the_file_does_not_reach_the_entailment_prompt() -> None:
    text = _FILE + f"token = {_TOKEN}\n" + "\n".join(_LINES) + "\n"
    ev = _evidence(text)
    assert _TOKEN not in ev.source_text and "[REDACTED:" in ev.source_text
    view = claim_view(ev, "Значение token в файле равно 12345")
    assert "[REDACTED:" in view.excerpt and _TOKEN not in view.excerpt
    judge = _Judge()
    assert _verdict("Значение token в файле равно 12345", text, judge=judge) != "verified"
    assert judge.prompts and all(_TOKEN not in p for p in judge.prompts)
    assert any("[REDACTED:" in p for p in judge.prompts), "судья видел место, где стоял секрет"


def test_a_secret_at_the_head_is_masked_in_the_excerpt_too() -> None:
    ev = _evidence(f"token = {_TOKEN}\n" + _FILE)
    assert _TOKEN not in ev.excerpt and _TOKEN not in ev.source_text


def test_a_short_file_is_judged_as_before() -> None:
    short = "\n".join(_LINES[50:]) + "\n"
    assert claim_view(ev := _evidence(short), "Код склада — ЯБЛОКО-4417") is ev
    assert _verdict("Код склада в файле long.txt — ЯБЛОКО-4417", short) == "verified"
    assert _verdict("Код склада в файле long.txt — ЯБЛОКО-4471", short) != "verified"
