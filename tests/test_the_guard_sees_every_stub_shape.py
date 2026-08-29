"""
Болезнь: сторож знал только четыре формы из своих тестов и пропускал семь других,
включая те, что реально ложились на диск вместо кода (PLACEHOLDER,
PLACEHOLDER_REPLACED_BY_EXECUTOR_WITH_FULL_FILE).

Лечение: структурный AST-анализ «есть ли реальное тело» вместо лексического списка.
"""

from core.placeholder_text import looks_like_unfilled_content


def test_placeholder_plain():
    assert looks_like_unfilled_content("PLACEHOLDER") is True


def test_placeholder_replaced_by_executor():
    assert looks_like_unfilled_content("PLACEHOLDER_REPLACED_BY_EXECUTOR_WITH_FULL_FILE") is True


def test_todo_later():
    assert looks_like_unfilled_content("TODO_LATER") is True


def test_bare_ellipsis():
    assert looks_like_unfilled_content("...") is True


def test_pass_body():
    assert looks_like_unfilled_content("def f():\n    pass\n") is True


def test_raise_not_implemented():
    assert looks_like_unfilled_content("def f():\n    raise NotImplementedError\n") is True


def test_docstring_will_be_filled():
    assert looks_like_unfilled_content('def f():\n    """Will be filled in later."""\n') is True


def test_todo_comment_with_code():
    assert looks_like_unfilled_content("# TODO: fix later\nx = 1\n") is False


def test_real_test_function():
    assert looks_like_unfilled_content("def test_x():\n    assert 1 == 1\n") is False


def test_documentation_only():
    assert looks_like_unfilled_content("# documentation only\n# more docs\n") is False


def test_abstractmethod_raise_not_implemented():
    assert looks_like_unfilled_content(
        "from abc import ABC, abstractmethod\n"
        "class A(ABC):\n"
        "    @abstractmethod\n"
        "    def f(self):\n"
        "        raise NotImplementedError\n"
    ) is False


def test_abstractmethod_pass():
    assert looks_like_unfilled_content(
        "from abc import ABC, abstractmethod\n"
        "class A(ABC):\n"
        "    @abstractmethod\n"
        "    def f(self):\n"
        "        pass\n"
    ) is False


def test_abstractmethod_ellipsis():
    assert looks_like_unfilled_content(
        "from abc import ABC, abstractmethod\n"
        "class A(ABC):\n"
        "    @abstractmethod\n"
        "    def f(self):\n"
        "        ...\n"
    ) is False


def test_real_code_with_ellipsis_expression():
    assert looks_like_unfilled_content(
        "from typing import Any\n"
        "x: Any = ...\n"
        "def g():\n"
        "    return x\n"
    ) is False


def test_old_prose_one_liner():
    assert looks_like_unfilled_content("PLACEHOLDER — will be filled by executor") is True


def test_old_comment_placeholder():
    assert looks_like_unfilled_content("# placeholder — will be replaced") is True


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
