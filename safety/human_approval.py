# Human Approval Layer (Human-in-the-Loop) — Слой 22
# Архитектура автономного AI-агента
# Слой обязательного участия человека в критичных шагах.
#
# SECURITY: Режим работы неизменяем после инициализации.
# LLM не может переключить на auto_approve.

import sys
from typing import Any


class HumanApprovalLayer:
    """
    Human Approval Layer — Human-in-the-Loop (Слой 22).

    Обеспечивает обязательное участие человека перед критичными действиями:
        - подтверждение опасных или необратимых действий
        - эскалация спорных решений оператору
        - ручное вмешательство в выполнение
        - журнал всех согласований

    Используется:
        - Cognitive Core (Слой 3) — подтверждение планов и решений
        - Execution System (Слой 8) — подтверждение исполнения
        - Governance / Policy Layer (Слой 21)

    Режимы работы:
        - 'interactive' — запрашивает подтверждение у человека в реальном времени
        - 'auto_approve' — автоматически одобряет всё (только для dev/тестов)
        - 'auto_reject'  — автоматически отклоняет всё (заглушка/безопасный режим)
        - 'callback'     — передаёт решение внешнему обработчику
    """

    MODES = ('interactive', 'auto_approve', 'auto_reject', 'callback')

    def __init__(self, mode='interactive', callback=None, audit_log=None):
        """
        Args:
            mode      -- режим работы ('interactive', 'auto_approve', 'auto_reject', 'callback')
            callback  -- внешняя функция(action_type, payload) -> bool, используется в режиме 'callback'
            audit_log -- список или внешний объект для сохранения журнала согласований
        """
        if mode not in self.MODES:
            raise ValueError(f"mode должен быть одним из: {self.MODES}")

        # SECURITY (VULN-14): mode хранится в защищённом атрибуте — LLM не может
        # переключить на auto_approve через setattr/assignment.
        self.__mode = mode
        self.callback = callback
        self._audit_log: Any = audit_log if audit_log is not None else []

    @property
    def mode(self):
        """SECURITY: mode только для чтения — LLM не может переключить на auto_approve."""
        return self.__mode

    # ── Основной интерфейс ────────────────────────────────────────────────────

    def request_approval(self, action_type: str, payload) -> bool:
        """
        Запрашивает подтверждение действия у человека.

        Args:
            action_type — тип действия ('plan', 'decision', 'execution', 'delete', ...)
            payload     — содержимое действия для показа/оценки

        Returns:
            True  — действие одобрено
            False — действие отклонено
        """
        approved = self._resolve(action_type, payload)
        self._log(action_type, payload, approved)
        return approved

    def __call__(self, action_type: str, payload) -> bool:
        """Позволяет передавать экземпляр как callback напрямую в Cognitive Core."""
        return self.request_approval(action_type, payload)

    # ── Внутренняя логика ─────────────────────────────────────────────────────

    def _resolve(self, action_type: str, payload) -> bool:
        if self.mode == 'auto_approve':
            return True

        if self.mode == 'auto_reject':
            return False

        if self.mode == 'callback':
            if not callable(self.callback):
                raise RuntimeError("callback не задан или не вызываем")
            return bool(self.callback(action_type, payload))

        # mode == 'interactive'
        return self._ask_human(action_type, payload)

    def _ask_human(self, action_type: str, payload) -> bool:
        """Запрашивает подтверждение в консоли (интерактивный режим)."""
        if not sys.stdin or not sys.stdin.isatty():
            # В неинтерактивном окружении не блокируем поток.
            return False

        print("\n" + "=" * 60)
        print(f"[HUMAN APPROVAL REQUIRED] Тип действия: {action_type}")
        print("-" * 60)
        print(f"Содержимое:\n{payload}")
        print("=" * 60)

        while True:
            answer = input("Одобрить? (да/нет) [y/n]: ").strip().lower()
            if answer in ('y', 'yes', 'да', 'д'):
                return True
            if answer in ('n', 'no', 'нет', 'н'):
                return False
            print("Пожалуйста, введите y/n или да/нет.")

    # ── Журнал согласований ───────────────────────────────────────────────────

    def _log(self, action_type: str, payload, approved: bool):
        """Записывает результат согласования в журнал."""
        entry = {
            'action_type': action_type,
            'payload': payload,
            'approved': approved,
        }
        if hasattr(self._audit_log, 'append'):
            self._audit_log.append(entry)
        elif hasattr(self._audit_log, 'write'):
            import json
            self._audit_log.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def get_audit_log(self):
        """Возвращает журнал всех согласований."""
        return list(self._audit_log) if isinstance(self._audit_log, list) else None

    def clear_audit_log(self):
        """Очищает журнал согласований."""
        if isinstance(self._audit_log, list):
            self._audit_log.clear()

    # ── Удобные методы для конкретных типов действий ─────────────────────────

    def approve_plan(self, plan) -> bool:
        """Запрашивает подтверждение плана действий."""
        return self.request_approval('plan', plan)

    def approve_decision(self, decision) -> bool:
        """Запрашивает подтверждение принятого решения."""
        return self.request_approval('decision', decision)

    def approve_execution(self, command) -> bool:
        """Запрашивает подтверждение перед исполнением команды/скрипта."""
        return self.request_approval('execution', command)

    def approve_deletion(self, target) -> bool:
        """Запрашивает подтверждение перед удалением данных или ресурсов."""
        return self.request_approval('delete', target)

    def approve_deployment(self, details) -> bool:
        """Запрашивает подтверждение перед деплоем."""
        return self.request_approval('deployment', details)
