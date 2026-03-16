from __future__ import annotations

from src.tools.impl import patch_request_tool


def test_request_patch_resolves_existing_suffix_path(monkeypatch):
    captured: list[tuple[str, str]] = []

    def fake_run_tool(name: str, arguments: dict[str, str] | None = None) -> str:
        arguments = arguments or {}
        if name == "read_file":
            captured.append((name, arguments["path"]))
            return 'def run() -> str:\n    return "ok"\n'
        if name == "propose_file_edit":
            captured.append((name, arguments["path"]))
            return f"Patch proposed for {arguments['path']}"
        raise AssertionError(name)

    monkeypatch.setattr(patch_request_tool, "run_tool", fake_run_tool)
    monkeypatch.setattr(patch_request_tool, "_call_llm_for_diff", lambda path, current, goal: "")
    monkeypatch.setattr(
        patch_request_tool,
        "_call_llm_for_content",
        lambda path, current, goal: 'def run() -> str:\n    return "still ok"\n',
    )

    result = patch_request_tool._request_patch("tests/test_finance_manager.py", "replace placeholder")

    assert "src/tests/test_finance_manager.py" in result
    assert captured == [
        ("read_file", "src/tests/test_finance_manager.py"),
        ("propose_file_edit", "src/tests/test_finance_manager.py"),
    ]


def test_request_patch_returns_error_for_ambiguous_path():
    result = patch_request_tool._request_patch("test_tts.py", "update tests")
    assert result.startswith("Error: ambiguous path")