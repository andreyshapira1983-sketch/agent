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


def test_read_does_not_match_inside_thread():
    # Regression: 'read ' must not match inside 'thread' and silently
    # justify a file_read step that the reasoning never argued for.
    r = check_reasoning_actions(
        "I will spawn a worker thread to keep the UI responsive.",
        ["file_read"],
    )
    assert r.has_mismatch
    assert "file_read" in r.unjustified_actions
    assert "file_read" not in r.matched_tools


def test_ls_does_not_match_inside_calls_or_class():
    # Regression: 'ls ' must not match inside 'calls'/'class'.
    r = check_reasoning_actions(
        "The class simply forwards all its calls to the base handler.",
        ["list_dir"],
    )
    assert "list_dir" in r.unjustified_actions


def test_url_does_not_match_inside_curl():
    # Regression: 'url' must not match inside 'curl'.
    r = check_reasoning_actions(
        "Run curl against the health endpoint to confirm it is up.",
        ["web_fetch"],
    )
    assert "web_fetch" in r.unjustified_actions


def test_whole_word_read_and_url_still_match():
    # The boundary fix must not create false negatives: real whole-word
    # mentions still count as justified.
    r = check_reasoning_actions(
        "I will read the config and fetch the url it points to.",
        ["file_read", "web_fetch"],
    )
    assert not r.has_mismatch
    assert set(r.matched_tools) == {"file_read", "web_fetch"}


def test_cyrillic_stems_unaffected_by_boundary_fix():
    # Cyrillic stems must keep matching inflected forms after the fix.
    r = check_reasoning_actions(
        "Изучу содержимое каталога, затем прочитаю нужный файл.",
        ["list_dir", "file_read"],
    )
    assert not r.has_mismatch
    assert set(r.matched_tools) == {"list_dir", "file_read"}
