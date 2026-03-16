"""
Тесты системного промпта: проверяем, что в промпте есть правила про тесты и модули.
"""
import sys
from pathlib import Path

# корень проекта в path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.core.prompt import get_system_prompt  # noqa: E402 — sys.path must be set first


def test_system_prompt_mentions_tests():
    """Агент должен знать: при создании модуля нужны тесты."""
    prompt = get_system_prompt()
    assert "тест" in prompt.lower(), "В промпте должно быть правило про тесты к модулям"


def test_system_prompt_mentions_modules():
    """Агент должен знать про модули и архитектуру."""
    prompt = get_system_prompt()
    assert "модул" in prompt.lower() or "архитектур" in prompt.lower()


def test_system_prompt_mentions_workspace_visibility():
    """Агент должен знать, что структуру проекта надо смотреть по реальным файлам."""
    prompt = get_system_prompt()
    assert "describe_workspace" in prompt
    assert "карта workspace" in prompt.lower()


def test_system_prompt_extra():
    """Дополнительный текст добавляется к базовому промпту."""
    base = get_system_prompt()
    with_extra = get_system_prompt(extra="Доп. инструкция.")
    assert "Доп. инструкция." in with_extra
    assert len(with_extra) > len(base)
