"""Tests for core.reasoning_action_check (MAST FM-2.6)."""

from core.reasoning_action_check import check_reasoning_actions


def test_no_mismatch_when_reasoning_mentions_each_tool():
    r = check_reasoning_actions(
        "I will read the file and then run the tests.",
        ["file_read", "run_tests"],
    )
    assert not r.has_mismatch
    assert set(r.matched_tools) == {"file_read", "run_tests"}


def test_unjustified_action_when_tool_not_mentioned():
    r = check_reasoning_actions(
        "Just answer from general knowledge — nothing to look up.",
        ["web_search"],
    )
    assert r.has_mismatch
    assert "web_search" in r.unjustified_actions


def test_mentioned_but_not_planned_flagged():
    r = check_reasoning_actions(
        "I should run pytest to verify, but the plan only reads the file.",
        ["file_read"],
    )
    assert "run_tests" in r.mentioned_but_not_planned
    # file_read is in the plan and 'reads the file' justifies it
    assert "file_read" in r.matched_tools


def test_empty_reasoning_returns_no_report():
    r = check_reasoning_actions("", ["file_read"])
    assert not r.has_mismatch


def test_placeholder_reasoning_skipped():
    r = check_reasoning_actions(
        "(no reasoning provided)",
        ["file_read", "run_tests"],
    )
    assert not r.has_mismatch
    assert r.matched_tools == ()


def test_russian_keywords_match():
    r = check_reasoning_actions(
        "Прочитаю файл и запущу тесты.",
        ["file_read", "run_tests"],
    )
    assert not r.has_mismatch


def test_log_payload_shape():
    r = check_reasoning_actions(
        "Need to search the web for the answer.",
        ["file_read"],
    )
    payload = r.to_log_payload()
    assert "unjustified_actions" in payload
    assert "mentioned_but_not_planned" in payload
    assert "matched_tools" in payload
    assert "file_read" in payload["unjustified_actions"]
    assert "web_search" in payload["mentioned_but_not_planned"]


def test_weak_keyword_does_not_trigger_mentioned_but_not_planned():
    # 'файл' alone should NOT imply a missing file_read step;
    # only strong tokens (literal tool name or long unique keyword) do.
    r = check_reasoning_actions(
        "Расскажу про файл и его роль в системе.",
        [],
    )
    assert "file_read" not in r.mentioned_but_not_planned
