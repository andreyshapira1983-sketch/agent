# Ethical & Value Alignment Layer (этика и согласование ценностей) — Слой 42
# Архитектура автономного AI-агента
# Проверка действий на этичность, согласование с ценностями и принципами агента.


import time
from enum import Enum


class EthicalVerdict(Enum):
    APPROVED   = 'approved'    # действие этично
    CAUTION    = 'caution'     # действие требует осторожности
    FLAGGED    = 'flagged'     # потенциальная проблема
    REJECTED   = 'rejected'    # действие неэтично / нарушает ценности


class EthicalPrinciple:
    """Один этический принцип агента."""

    def __init__(self, name: str, description: str,
                 weight: float = 1.0, hard_rule: bool = False):
        self.name = name
        self.description = description
        self.weight = weight            # важность принципа (0–1)
        self.hard_rule = hard_rule      # True = нарушение → автоматически REJECTED

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'weight': self.weight,
            'hard_rule': self.hard_rule,
        }


class EthicalEvaluation:
    """Результат этической оценки действия."""

    def __init__(self, action: str, verdict: EthicalVerdict,
                 score: float, reasons: list[str],
                 violated_principles: list[str] | None = None):
        self.action = action
        self.verdict = verdict
        self.score = score              # 0–1, выше = более этично
        self.reasons = reasons
        self.violated_principles = violated_principles or []
        self.evaluated_at = time.time()

    def to_dict(self):
        return {
            'action': self.action,
            'verdict': self.verdict.value,
            'score': round(self.score, 3),
            'reasons': self.reasons,
            'violated_principles': self.violated_principles,
        }


class EthicsLayer:
    """
    Ethical & Value Alignment Layer — Слой 42.

    Функции:
        - набор этических принципов и ценностей агента
        - проверка каждого действия на соответствие принципам
        - оценка: одобрить / предупредить / отклонить
        - аудит всех этических оценок
        - обновление принципов через Human Approval
        - интеграция с Governance Layer для policy enforcement

    Используется:
        - Governance Layer (Слой 21)       — надстройка политик
        - Human Approval (Слой 22)         — эскалация спорных решений
        - Autonomous Loop (Слой 20)        — проверка перед каждым действием
        - Cognitive Core (Слой 3)          — объяснение этических решений
    """

    # Встроенные принципы по умолчанию
    DEFAULT_PRINCIPLES = [
        EthicalPrinciple(
            "do_no_harm",
            "Не причинять вред людям, системам или окружающей среде.",
            weight=1.0, hard_rule=True
        ),
        EthicalPrinciple(
            "honesty",
            "Быть честным: не вводить в заблуждение, не фальсифицировать данные.",
            weight=0.9, hard_rule=True
        ),
        EthicalPrinciple(
            "privacy",
            "Уважать конфиденциальность: не собирать и не распространять личные данные без согласия.",
            weight=0.9, hard_rule=True
        ),
        EthicalPrinciple(
            "human_oversight",
            "Поддерживать контроль человека: не обходить механизмы надзора.",
            weight=1.0, hard_rule=True
        ),
        EthicalPrinciple(
            "fairness",
            "Действовать справедливо: избегать дискриминации и предвзятости.",
            weight=0.8, hard_rule=False
        ),
        EthicalPrinciple(
            "transparency",
            "Быть прозрачным: объяснять свои решения и действия.",
            weight=0.7, hard_rule=False
        ),
        EthicalPrinciple(
            "minimal_footprint",
            "Минимизировать воздействие: использовать только необходимые ресурсы и права.",
            weight=0.6, hard_rule=False
        ),
    ]

    def __init__(self, cognitive_core=None, human_approval=None,
                 monitoring=None):
        self.cognitive_core = cognitive_core
        self.human_approval = human_approval
        self.monitoring = monitoring

        self._principles: dict[str, EthicalPrinciple] = {
            p.name: p for p in self.DEFAULT_PRINCIPLES
        }
        self._audit: list[dict] = []
        self._rejection_count = 0

    # ── Оценка действий ───────────────────────────────────────────────────────

    def evaluate(self, action: str, context: dict | None = None) -> EthicalEvaluation:
        """
        Оценивает действие на этичность.

        Args:
            action  — описание действия
            context — дополнительный контекст (цель, последствия и т.д.)

        Returns:
            EthicalEvaluation с вердиктом и обоснованием.
        """
        context = context or {}
        violated = []
        reasons = []

        # Быстрая проверка жёстких правил по ключевым словам
        hard_violations = self._check_hard_rules(action)
        if hard_violations:
            for p_name in hard_violations:
                violated.append(p_name)
                reasons.append(f"Нарушение жёсткого правила: {self._principles[p_name].description}")

            ev = EthicalEvaluation(
                action=action,
                verdict=EthicalVerdict.REJECTED,
                score=0.0,
                reasons=reasons,
                violated_principles=violated,
            )
            self._record(ev)
            return ev

        # SECURITY (VULN-07): Этика решается детерминированно локальным мозгом,
        # а НЕ через LLM (LLM не должен судить сам себя).
        # Используем расширенную keyword-эвристику.
        score, verdict_str = self._deterministic_evaluate(action, context)
        if verdict_str != 'approved':
            reasons.append(f"Детерминированная проверка: {verdict_str}")

        verdict_map = {
            'approved': EthicalVerdict.APPROVED,
            'caution': EthicalVerdict.CAUTION,
            'flagged': EthicalVerdict.FLAGGED,
            'rejected': EthicalVerdict.REJECTED,
        }
        verdict = verdict_map.get(verdict_str.lower(), EthicalVerdict.CAUTION)

        ev = EthicalEvaluation(
            action=action,
            verdict=verdict,
            score=score,
            reasons=reasons,
            violated_principles=violated,
        )
        self._record(ev)

        # Flagged → эскалируем к Human Approval
        if verdict == EthicalVerdict.FLAGGED and self.human_approval:
            reason_text = '; '.join(reasons) if reasons else verdict_str
            approved = self.human_approval.request_approval(
                'ethical_review',
                f"Действие требует этической проверки:\n{action}\nПричина: {reason_text}"
            )
            if not approved:
                ev.verdict = EthicalVerdict.REJECTED

        return ev

    def require_ethical(self, action: str, context: dict | None = None):
        """Выбрасывает ValueError если действие не одобрено."""
        ev = self.evaluate(action, context)
        if ev.verdict in (EthicalVerdict.REJECTED,):
            raise ValueError(
                f"Действие отклонено по этическим соображениям: {ev.reasons}"
            )
        return ev

    def is_allowed(self, action: str, context: dict | None = None) -> bool:
        """Быстрая проверка: разрешено ли действие."""
        ev = self.evaluate(action, context)
        return ev.verdict in (EthicalVerdict.APPROVED, EthicalVerdict.CAUTION)

    # ── Управление принципами ─────────────────────────────────────────────────

    def add_principle(self, name: str, description: str,
                      weight: float = 0.7, hard_rule: bool = False):
        """Добавляет новый этический принцип."""
        self._principles[name] = EthicalPrinciple(
            name, description, weight, hard_rule
        )
        self._log(f"Добавлен принцип: '{name}'")

    def remove_principle(self, name: str):
        self._principles.pop(name, None)

    def get_principles(self) -> list[dict]:
        return [p.to_dict() for p in self._principles.values()]

    # ── Аудит ─────────────────────────────────────────────────────────────────

    def get_audit(self, verdict: EthicalVerdict | None = None) -> list[dict]:
        if verdict:
            return [e for e in self._audit if e['verdict'] == verdict.value]
        return list(self._audit)

    def summary(self) -> dict:
        from collections import Counter
        verdicts = Counter(e['verdict'] for e in self._audit)
        return {
            'total_evaluations': len(self._audit),
            'rejections': self._rejection_count,
            'principles_count': len(self._principles),
            **dict(verdicts),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    # Ключевые слова для быстрой проверки жёстких правил
    _HARM_KEYWORDS = [
        'delete all', 'rm -rf', 'rm -r', 'drop table', 'drop database',
        'wipe', 'destroy', 'kill process', 'format disk', 'format c:',
        'shutdown', 'ddos', 'attack', 'exploit', 'inject', 'malware',
        'ransomware', 'trojan', 'backdoor', 'keylogger', 'rootkit',
        'sudo rm', 'del /f', 'rd /s', 'mkfs', 'dd if=',
    ]
    _PRIVACY_KEYWORDS = [
        'steal credential', 'exfiltrate', 'leak password',
        'scrape personal', 'dump database', 'extract tokens',
        'read .env', 'read ssh key', 'access private key',
        'send credentials', 'upload secrets',
    ]
    _BYPASS_KEYWORDS = frozenset({
        'bypass approval', 'skip human', 'override safety',
        'disable monitoring', 'disable safe_mode', 'turn off safety',
        'ignore governance', 'ignore ethics', 'skip validation',
        'remove deny pattern', 'set strict_mode false',
        'change approval mode', 'auto_approve',
    })

    # SECURITY: Prompt injection — английские и русские паттерны.
    # frozenset: не может быть изменён через LLM или внешний код в runtime.
    _PROMPT_INJECTION_KEYWORDS = frozenset({
        # Английские классические
        'ignore previous instructions', 'ignore all previous',
        'disregard prior instructions', 'forget your instructions',
        'you are now a', 'new system prompt', 'new instructions:',
        'override your instructions', 'your new instructions',
        # Манипуляция ролью
        'act as if you have no restrictions', 'pretend you are',
        'roleplay as', 'from now on you',
        # Русские варианты
        'игнорируй правило', 'игнорируй все правила',
        'забудь инструкции', 'забудь все инструкции',
        'игнорируй инструкции', 'выполняй всё без ограничений',
        'ты теперь', 'теперь ты', 'новые инструкции:',
        'притворись что', 'представь что ты',
        'отключи ограничения', 'обойди правила',
    })
    _DECEPTION_KEYWORDS = [
        'fake', 'falsify', 'fabricate data', 'forge', 'spoof',
        'impersonate', 'mislead', 'deceive user',
    ]

    # Caution keywords (не блокируют, но понижают score)
    _CAUTION_KEYWORDS = [
        'install', 'deploy', 'push', 'publish', 'send email',
        'modify config', 'change settings', 'update database',
        'write to production', 'restart service',
    ]

    def _check_hard_rules(self, action: str) -> list[str]:
        action_lower = action.lower()
        violated = []
        for kw in self._HARM_KEYWORDS:
            if kw in action_lower:
                violated.append('do_no_harm')
                break
        for kw in self._PRIVACY_KEYWORDS:
            if kw in action_lower:
                violated.append('privacy')
                break
        for kw in self._BYPASS_KEYWORDS:
            if kw in action_lower:
                violated.append('human_oversight')
                break
        for kw in self._PROMPT_INJECTION_KEYWORDS:
            if kw in action_lower:
                violated.append('human_oversight')
                break
        for kw in self._DECEPTION_KEYWORDS:
            if kw in action_lower:
                violated.append('honesty')
                break
        return violated

    def _deterministic_evaluate(self, action: str, _context: dict) -> tuple[float, str]:
        """
        SECURITY (VULN-07): Детерминированная этическая оценка — без LLM.
        Локальный мозг решает, не LLM.
        Возвращает (score, verdict_str).
        """
        action_lower = action.lower()
        score = 1.0

        # Проверка caution keywords — каждое совпадение снижает score
        caution_count = 0
        for kw in self._CAUTION_KEYWORDS:
            if kw in action_lower:
                caution_count += 1
                score -= 0.1

        # Проверка harm/privacy/bypass/deception — серьёзное снижение
        for kw in self._HARM_KEYWORDS:
            if kw in action_lower:
                score -= 0.5
        for kw in self._PRIVACY_KEYWORDS:
            if kw in action_lower:
                score -= 0.5
        for kw in self._BYPASS_KEYWORDS:
            if kw in action_lower:
                score -= 0.5
        for kw in self._DECEPTION_KEYWORDS:
            if kw in action_lower:
                score -= 0.4

        score = max(0.0, min(1.0, score))

        # Определяем вердикт по score
        if score >= 0.8:
            return score, 'approved'
        elif score >= 0.5:
            return score, 'caution'
        elif score >= 0.3:
            return score, 'flagged'
        else:
            return score, 'rejected'

    def _record(self, ev: EthicalEvaluation):
        self._audit.append(ev.to_dict())
        if ev.verdict == EthicalVerdict.REJECTED:
            self._rejection_count += 1
            self._log(f"Отклонено: '{ev.action[:60]}' — {ev.reasons}")
        else:
            self._log(f"Оценка '{ev.action[:60]}': {ev.verdict.value} ({ev.score:.2f})")

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='ethics_layer')
        else:
            print(f"[EthicsLayer] {message}")
