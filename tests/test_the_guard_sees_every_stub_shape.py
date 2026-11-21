"""
Болезнь: сторож знал только четыре формы из своих тестов и пропускал семь других,
включая те, что реально ложились на диск вместо кода (PLACEHOLDER,
PLACEHOLDER_REPLACED_BY_EXECUTOR_WITH_FULL_FILE).

Лечение: структурный AST-анализ «есть ли реальное тело» вместо лексического списка.
"""

from core.placeholder_text import looks_like_unfilled_content


def test_placeholder_plain():
    assert looks_like_unfilled_content("PLACEHOLDER") is True


def test_bare_ellipsis():
    assert looks_like_unfilled_content("...") is True


def test_pass_body():
    assert looks_like_unfilled_content("def f():\n    pass\n") is True


def test_raise_not_implemented():
    assert looks_like_unfilled_content("def f():\n    raise NotImplementedError\n") is True


def test_docstring_will_be_filled():
    assert looks_like_unfilled_content('def f():\n    """Will be filled in later."""\n') is True


def test_old_prose_one_liner():
    assert looks_like_unfilled_content("PLACEHOLDER — will be filled by executor") is True


def test_old_multiline_comment_assert_false():
    assert looks_like_unfilled_content(
        "# placeholder\n"
        "# will be replaced\n"
        "assert False\n"
    ) is True


def test_old_multiline_comment_pass():
    assert looks_like_unfilled_content(
        "# placeholder\n"
        "# will be replaced\n"
        "pass\n"
    ) is True
