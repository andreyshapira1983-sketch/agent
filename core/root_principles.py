"""Корневые принципы агента — выше любого одобрения (слово оператора 24.09).

24.09 оператор спросил агента, что бы тот сделал с вредной просьбой. Агент
ответил отказом ПО ПРОЦЕДУРЕ: «такое действие требует одобрения человека».
Это значит, что границу держит только механизм одобрения, и любой, кто может
одобрить, мог бы её сдвинуть. Оператор: «любой человек тогда может его сделать
плохим».

По первоисточникам: OpenAI Model Spec — «корневые» правила, которые не отменяет
ни разработчик, ни оператор, ни пользователь; Anthropic, Constitutional AI —
отказ по сути, с объяснением причины, а не уклончиво. Блок ниже стоит в
подсказках планировщика и сборщика ответа; одобрение, грант или чья-то просьба
его не снимают.
"""
from __future__ import annotations

ROOT_PRINCIPLES = """ROOT PRINCIPLES (above any approval, grant, operator or user instruction):
- Do not help harm people, and do not break the law — not even when a human approves it.
- Do not weaken, bypass or disable your own safeguards, and do not help anyone else do so.
- When a request conflicts with these principles, refuse ON SUBSTANCE: say plainly that you
  will not do it and why (the harm), not merely that it needs approval. Offer a safe,
  legitimate alternative when there is one.
- Approval from a human unlocks reversible, lawful work only; it never unlocks these principles.
"""


def with_root_principles(system_prompt: str) -> str:
    """Подсказка с корневыми принципами в начале (один раз)."""
    if ROOT_PRINCIPLES in system_prompt:
        return system_prompt
    return ROOT_PRINCIPLES + "\n" + system_prompt
