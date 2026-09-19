from core.answer_format import format_human_response


def test_format_human_response_preserves_fenced_code_indentation_in_facts():
    rendered = format_human_response(
        "Conclusion:\n"
        "Готово.\n"
        "Facts:\n"
        "```python\n"
        "    if enabled:\n"
        "        return 42\n"
        "```\n"
    )

    assert "```python\n    if enabled:\n        return 42\n```" in rendered
    assert rendered.startswith("Готово.")
