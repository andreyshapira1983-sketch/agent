# Governance / Policy Layer (управляющий слой правил) — Слой 21
# Архитектура автономного AI-агента
# Правила допустимых действий, ограничения поведения, аудит решений, управление рисками.


from enum import Enum


class RiskLevel(Enum):
    LOW      = 'low'
    MEDIUM   = 'medium'
    HIGH     = 'high'
    CRITICAL = 'critical'


class PolicyViolation(Exception):
    """Исключение при нарушении политики."""
    # pylint: disable=unnecessary-pass


class GovernanceLayer:
    """
    Governance / Policy Layer — Слой 21.

    Определяет и применяет правила поведения агента:
        - что агент может делать (allowlist)
        - что запрещено (denylist)
        - оценка риска действий
        - комплаенс-проверки
        - аудит всех решений
        - управление рисками

    Используется:
        - Human Approval Layer (Слой 22) — проверка перед запросом одобрения
        - Execution System (Слой 8)      — проверка перед исполнением
        - Autonomous Loop (Слой 20)      — фильтр на каждом шаге цикла
    """

    # SECURITY (VULN-06/09): Базовые deny-паттерны неизменяемы.
    # LLM не может их удалить или обойти.
    _CORE_DENY_PATTERNS = frozenset({
        'rm -rf', 'rm -r', 'format c:', 'mkfs', 'dd if=',
        'DROP DATABASE', 'DROP TABLE', 'TRUNCATE TABLE',
        'shutdown', 'reboot', 'halt', 'poweroff', 'init 0',
        ':(){:|:&};:',                                 # fork bomb
        'sudo passwd', 'net user',                     # изменение паролей
        'sudo', 'su ', 'runas',                        # эскалация привилегий
        'curl ', 'wget ', 'nc ', 'ncat',               # сетевые утилиты
        'eval(', 'exec(', '__import__',                 # code injection
        'bypass approval', 'skip human', 'override safety',
        'disable monitoring', 'disable safe_mode',
        'bash -c', 'sh -c', 'cmd /c',                  # shell escape
        # Опасные варианты PowerShell (обфускация, скрытое исполнение, загрузка кода)
        'powershell -enc', 'powershell -e ',
        'powershell -nop', 'powershell -w hidden',
        'powershell iex', 'powershell invoke-expression',
        'powershell downloadstring', 'powershell downloadfile',
        'powershell -command "iex', "powershell -command 'iex",
        'chmod 777', 'chmod +s',                        # опасные пермишены
        'ssh ', 'scp ', 'rsync ',                       # удалённый доступ
    })

    def __init__(self, strict_mode: bool = True, audit_log=None):
        """
        Args:
            strict_mode -- если True, запрещённые действия бросают исключение
                           если False -- только логируют нарушение
            audit_log   -- список или внешний объект для журнала аудита
        """
        # SECURITY (VULN-09): strict_mode неизменяем после инициализации
        self.__strict_mode = strict_mode
        self._audit_log = audit_log if audit_log is not None else []

        # Дополнительные deny-паттерны (могут добавляться, но core — нельзя удалять)
        self._extra_deny_patterns: list[str] = []
        self._allow_patterns: list[str] = []
        self._risk_rules: list[dict] = [
            {'pattern': 'deploy',    'risk': RiskLevel.HIGH},
            {'pattern': 'delete',    'risk': RiskLevel.HIGH},
            {'pattern': 'drop',      'risk': RiskLevel.CRITICAL},
            {'pattern': 'curl ',     'risk': RiskLevel.MEDIUM},
            {'pattern': 'wget ',     'risk': RiskLevel.MEDIUM},
            {'pattern': 'install',   'risk': RiskLevel.MEDIUM},
            {'pattern': 'git push',  'risk': RiskLevel.MEDIUM},
            {'pattern': 'chmod',     'risk': RiskLevel.MEDIUM},
            {'pattern': 'sudo',      'risk': RiskLevel.CRITICAL},
            {'pattern': 'eval(',     'risk': RiskLevel.CRITICAL},
            {'pattern': 'exec(',     'risk': RiskLevel.CRITICAL},
            {'pattern': 'ssh',       'risk': RiskLevel.HIGH},
            {'pattern': 'bash',      'risk': RiskLevel.HIGH},
        ]

    @property
    def strict_mode(self):
        """SECURITY: strict_mode только для чтения."""
        return self.__strict_mode

    # ── Основной интерфейс ────────────────────────────────────────────────────

    def check(self, action: str, context: dict | None = None) -> dict:
        """
        Проверяет действие на соответствие политике.
        SECURITY (VULN-06): Детерминированная проверка без LLM-парсинга.

        Returns:
            {
                'allowed': bool,
                'risk': RiskLevel,
                'reason': str,
                'requires_approval': bool,
            }
        """
        action_lower = action.lower()

        # SECURITY: Проверяем core deny-паттерны (неизменяемые)
        for pattern in self._CORE_DENY_PATTERNS:
            if pattern.lower() in action_lower:
                result = {
                    'allowed': False,
                    'risk': RiskLevel.CRITICAL,
                    'reason': f"Запрещено базовой политикой: содержит '{pattern}'",
                    'requires_approval': False,
                }
                self._audit(action, result, context)
                if self.strict_mode:
                    raise PolicyViolation(result['reason'])
                return result

        # Проверяем дополнительные deny-паттерны
        for pattern in self._extra_deny_patterns:
            if pattern.lower() in action_lower:
                result = {
                    'allowed': False,
                    'risk': RiskLevel.CRITICAL,
                    'reason': f"Запрещено дополнительной политикой: содержит '{pattern}'",
                    'requires_approval': False,
                }
                self._audit(action, result, context)
                if self.strict_mode:
                    raise PolicyViolation(result['reason'])
                return result

        # Оценка риска
        risk = self._assess_risk(action_lower)

        result = {
            'allowed': True,
            'risk': risk,
            'reason': 'OK',
            'requires_approval': risk in (RiskLevel.HIGH, RiskLevel.CRITICAL),
        }
        self._audit(action, result, context)
        return result

    def is_allowed(self, action: str, context: dict | None = None) -> bool:
        """Быстрая проверка: разрешено ли действие."""
        try:
            return self.check(action, context)['allowed']
        except PolicyViolation:
            return False

    def assess_risk(self, action: str) -> RiskLevel:
        """Возвращает уровень риска действия."""
        return self._assess_risk(action.lower())

    # ── Управление правилами ──────────────────────────────────────────────────

    def add_deny(self, pattern: str):
        """Добавляет дополнительный запрещённый паттерн."""
        if pattern not in self._extra_deny_patterns:
            self._extra_deny_patterns.append(pattern)

    def remove_deny(self, pattern: str):
        """Убирает дополнительный запрещённый паттерн.
        SECURITY: Базовые _CORE_DENY_PATTERNS нельзя удалить."""
        if pattern in self._CORE_DENY_PATTERNS:
            # Логируем попытку как инцидент безопасности
            self._audit(f"ATTEMPT_REMOVE_CORE_DENY: {pattern}",
                        {'allowed': False, 'risk': RiskLevel.CRITICAL,
                         'reason': 'Попытка удалить базовое правило безопасности',
                         'requires_approval': False})
            return  # Игнорируем — core правила неизменяемы
        self._extra_deny_patterns = [p for p in self._extra_deny_patterns if p != pattern]

    def add_risk_rule(self, pattern: str, risk: RiskLevel):
        """Добавляет правило оценки риска."""
        self._risk_rules.append({'pattern': pattern, 'risk': risk})

    def list_deny_patterns(self) -> list:
        """Возвращает все deny-паттерны (core + дополнительные)."""
        return list(self._CORE_DENY_PATTERNS) + list(self._extra_deny_patterns)

    # ── Аудит ─────────────────────────────────────────────────────────────────

    def _audit(self, action: str, result: dict, context: dict | None = None):
        import time
        entry = {
            'timestamp': time.time(),
            'action': action,
            'allowed': result['allowed'],
            'risk': result['risk'].value,
            'reason': result['reason'],
            'context': context,
        }
        if hasattr(self._audit_log, 'append'):
            self._audit_log.append(entry)

    def get_audit_log(self) -> list:
        return list(self._audit_log) if isinstance(self._audit_log, list) else []

    def get_violations(self) -> list:
        return [e for e in self.get_audit_log() if not e['allowed']]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _assess_risk(self, action_lower: str) -> RiskLevel:
        # Сортируем по убыванию тяжести — берём максимальный риск
        max_risk = RiskLevel.LOW
        order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        for rule in self._risk_rules:
            if rule['pattern'].lower() in action_lower:
                if order.index(rule['risk']) > order.index(max_risk):
                    max_risk = rule['risk']
        return max_risk
