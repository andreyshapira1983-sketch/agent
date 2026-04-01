# Data Validation & Contracts Layer (валидация данных) — Слой 24
# Архитектура автономного AI-агента
# Валидация входных данных, проверка схем, контроль целостности, защита от повреждённых данных.


from enum import Enum
from collections.abc import Callable


class ValidationStatus(Enum):
    OK      = 'ok'
    WARNING = 'warning'
    ERROR   = 'error'


class ValidationResult:
    """Результат валидации одного объекта."""

    def __init__(self):
        self.status = ValidationStatus.OK
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.field_errors: dict[str, str] = {}

    @property
    def is_valid(self) -> bool:
        return self.status != ValidationStatus.ERROR

    def add_error(self, message: str, field: str | None = None):
        self.status = ValidationStatus.ERROR
        self.errors.append(message)
        if field:
            self.field_errors[field] = message

    def add_warning(self, message: str):
        if self.status == ValidationStatus.OK:
            self.status = ValidationStatus.WARNING
        self.warnings.append(message)

    def to_dict(self):
        return {
            'status': self.status.value,
            'is_valid': self.is_valid,
            'errors': self.errors,
            'warnings': self.warnings,
            'field_errors': self.field_errors,
        }

    def __str__(self):
        if self.is_valid:
            w = f", {len(self.warnings)} предупреждений" if self.warnings else ""
            return f"OK{w}"
        return f"ОШИБКА: {'; '.join(self.errors)}"


class DataValidator:
    """
    Data Validation & Contracts Layer — Слой 24.

    Функции:
        - валидация входных данных по схемам
        - проверка типов, обязательных полей, диапазонов значений
        - контроль форматов API-ответов
        - защита от повреждённых и некорректных данных
        - contract-проверки между слоями системы

    Используется:
        - Perception Layer (Слой 1)   — валидация входящих данных
        - Execution System (Слой 8)   — проверка команд перед исполнением
        - Knowledge System (Слой 2)   — контроль качества знаний
        - Agent System (Слой 4)       — проверка задач перед делегированием
        - Все слои на системных границах
    """

    def __init__(self, strict: bool = False, monitoring=None):
        """
        Args:
            strict     — если True, предупреждения приравниваются к ошибкам
            monitoring — Monitoring (Слой 17)
        """
        self.strict = strict
        self.monitoring = monitoring
        self._schemas: dict[str, dict] = {}       # зарегистрированные схемы
        self._contracts: dict[str, Callable] = {} # зарегистрированные контракты
        self._register_system_schemas()           # предзагрузка системных схем

    # ── Регистрация схем ──────────────────────────────────────────────────────

    def register_schema(self, name: str, schema: dict):
        """
        Регистрирует схему валидации.

        Формат схемы:
        {
            'field_name': {
                'type': str|int|float|bool|list|dict,
                'required': True|False,
                'min': ...,         # для чисел и строк (len)
                'max': ...,
                'allowed': [...],   # допустимые значения
                'pattern': '...',   # regex для строк
            },
            ...
        }
        """
        self._schemas[name] = schema

    def register_contract(self, name: str, validator: Callable):
        """
        Регистрирует контракт — произвольную функцию(data) → ValidationResult.
        Используется для сложных межслойных проверок.
        """
        self._contracts[name] = validator

    # ── Валидация ─────────────────────────────────────────────────────────────

    def validate(self, data, schema_name: str | None = None, schema: dict | None = None) -> ValidationResult:
        """
        Валидирует данные по именованной или переданной схеме.

        Args:
            data        — данные для проверки (dict)
            schema_name — имя зарегистрированной схемы
            schema      — схема напрямую (если schema_name не задан)
        """
        result = ValidationResult()

        if not isinstance(data, dict):
            result.add_error(f"Ожидался dict, получен {type(data).__name__}")
            return result

        s = schema or (self._schemas.get(schema_name) if schema_name is not None else None)
        if not s:
            result.add_warning(f"Схема '{schema_name}' не найдена — валидация пропущена")
            return result

        for field, rules in s.items():
            self._validate_field(data, field, rules, result)

        if self.strict and result.warnings:
            for w in result.warnings:
                result.add_error(f"(strict) {w}")

        if not result.is_valid:
            self._log(f"Валидация провалена: {result}")

        return result

    def validate_contract(self, name: str, data) -> ValidationResult:
        """Запускает зарегистрированный контракт."""
        contract = self._contracts.get(name)
        if not contract:
            result = ValidationResult()
            result.add_warning(f"Контракт '{name}' не зарегистрирован")
            return result
        return contract(data)

    def require_valid(self, data, schema_name: str | None = None, schema: dict | None = None):
        """Валидирует и бросает ValueError если данные некорректны."""
        result = self.validate(data, schema_name=schema_name, schema=schema)
        if not result.is_valid:
            raise ValueError(f"Невалидные данные: {result}")
        return result

    # ── Встроенные быстрые проверки ───────────────────────────────────────────

    def check_type(self, value, expected_type: type, field: str | None = None) -> ValidationResult:
        result = ValidationResult()
        if not isinstance(value, expected_type):
            result.add_error(
                f"Поле '{field or 'value'}': ожидался {expected_type.__name__}, "
                f"получен {type(value).__name__}",
                field=field,
            )
        return result

    def check_not_empty(self, value, field: str | None = None) -> ValidationResult:
        result = ValidationResult()
        if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0):
            result.add_error(f"Поле '{field or 'value'}' не может быть пустым", field=field)
        return result

    def check_range(self, value: float, min_val=None, max_val=None, field: str | None = None) -> ValidationResult:
        result = ValidationResult()
        if min_val is not None and value < min_val:
            result.add_error(f"Поле '{field}': {value} < минимум {min_val}", field=field)
        if max_val is not None and value > max_val:
            result.add_error(f"Поле '{field}': {value} > максимум {max_val}", field=field)
        return result

    def check_pattern(self, value: str, pattern: str, field: str | None = None) -> ValidationResult:
        import re
        result = ValidationResult()
        if not re.match(pattern, str(value)):
            result.add_error(
                f"Поле '{field or 'value'}': '{value}' не соответствует паттерну '{pattern}'",
                field=field,
            )
        return result

    def sanitize_string(self, value: str, max_length: int = 10000) -> str:
        """Очищает строку: обрезает длину, убирает опасные символы."""
        if not isinstance(value, str):
            value = str(value)
        value = value[:max_length]
        # Убираем null-байты
        value = value.replace('\x00', '')
        return value.strip()

    # ── Встроенные схемы для слоёв системы ───────────────────────────────────

    def _register_system_schemas(self):
        """
        Предзагрузка схем для всех основных типов данных системы.
        Охватывает реальные структуры данных между слоями агента.
        """
        # ── Слой 4: Agent System ─────────────────────────────────────────────
        self.register_schema('task', {
            'goal': {'type': str, 'required': True, 'min': 1, 'max': 2000},
            'role': {'type': str, 'required': False},
            'priority': {'type': int, 'required': False, 'min': 1, 'max': 4},
        })

        # ── Слой 2: Knowledge System — эпизодическая память ──────────────────
        self.register_schema('episode', {
            'task':    {'type': str, 'required': True,  'min': 1, 'max': 500},
            'action':  {'type': str, 'required': True,  'min': 1, 'max': 500},
            'result':  {'required': True},
            'success': {'type': bool, 'required': True},
        })

        # ── Слой 3: Cognitive Core — ответ LLM ───────────────────────────────
        self.register_schema('llm_response', {
            'content': {'type': str, 'required': True, 'min': 1},
        })

        # ── Слой 1: Perception Layer — веб-данные ────────────────────────────
        self.register_schema('web_fetch', {
            'url':     {'type': str, 'required': True, 'min': 7},   # http://x
            'content': {'type': str, 'required': False},
        })

        # ── Слой 5: Tool Layer — вызов инструмента ───────────────────────────
        self.register_schema('tool_call', {
            'tool':   {'type': str, 'required': True, 'min': 1, 'max': 100},
            'params': {'type': dict, 'required': False},
        })

        # ── Слой 8: Execution System — команда ───────────────────────────────
        self.register_schema('command', {
            'cmd':     {'type': str, 'required': True, 'min': 1, 'max': 4000},
            'timeout': {'type': (int, float), 'required': False, 'min': 0, 'max': 3600},
        })

        # ── Слой 20: Autonomous Loop — цикл ──────────────────────────────────
        self.register_schema('cycle_result', {
            'cycle_id': {'type': int,  'required': True,  'min': 1},
            'success':  {'type': bool, 'required': True},
            'errors':   {'type': list, 'required': False},
        })

        # ── Слой 28: Sandbox — результат симуляции ───────────────────────────
        self.register_schema('sandbox_action', {
            'action':  {'type': str, 'required': True, 'min': 1, 'max': 2000},
            'verdict': {
                'type': str, 'required': False,
                'allowed': ['safe', 'risky', 'unsafe', 'simulated'],
            },
        })

        # ── Слой 31: Knowledge Acquisition — источник ────────────────────────
        self.register_schema('knowledge_source', {
            'url_or_path':  {'type': str, 'required': True, 'min': 1, 'max': 2000},
            'source_type':  {
                'type': str, 'required': False,
                'allowed': ['web', 'file', 'api', 'github', 'article',
                            'gutenberg', 'arxiv', 'wikipedia'],
            },
            'tags': {'type': list, 'required': False},
        })

        # ── Слой 42: Ethics — оценка действия ────────────────────────────────
        self.register_schema('ethical_evaluation', {
            'action':  {'type': str, 'required': True, 'min': 1, 'max': 2000},
            'verdict': {
                'type': str, 'required': False,
                'allowed': ['approved', 'caution', 'flagged', 'rejected'],
            },
            'score': {'type': float, 'required': False, 'min': 0.0, 'max': 1.0},
        })

        # ── Слой 36: Experience Replay — паттерн ─────────────────────────────
        self.register_schema('experience_pattern', {
            'pattern':     {'type': str,  'required': True, 'min': 1},
            'frequency':   {'type': int,  'required': True, 'min': 1},
            'avg_success': {'type': float,'required': False, 'min': 0.0, 'max': 1.0},
        })

        # ── Слой 46: Knowledge Verification — результат проверки ─────────────
        self.register_schema('verification_result', {
            'claim':      {'type': str,  'required': True, 'min': 1},
            'verified':   {'type': bool, 'required': True},
            'confidence': {'type': float,'required': False, 'min': 0.0, 'max': 1.0},
        })

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _validate_field(self, data: dict, field: str, rules: dict, result: ValidationResult):
        required = rules.get('required', False)
        value = data.get(field)

        if value is None:
            if required:
                result.add_error(f"Обязательное поле '{field}' отсутствует", field=field)
            return

        # Проверка типа (поддерживает одиночный тип и кортеж типов)
        expected_type = rules.get('type')
        if expected_type and not isinstance(value, expected_type):
            if isinstance(expected_type, tuple):
                type_name = '|'.join(t.__name__ for t in expected_type)
            else:
                type_name = expected_type.__name__
            result.add_error(
                f"Поле '{field}': ожидался {type_name}, получен {type(value).__name__}",
                field=field,
            )
            return

        # Проверка длины/размера
        min_val = rules.get('min')
        max_val = rules.get('max')
        comparable = len(value) if isinstance(value, (str, list, dict)) else value

        if min_val is not None and comparable < min_val:
            result.add_error(f"Поле '{field}': значение {comparable} < минимум {min_val}", field=field)
        if max_val is not None and comparable > max_val:
            result.add_error(f"Поле '{field}': значение {comparable} > максимум {max_val}", field=field)

        # Допустимые значения
        allowed = rules.get('allowed')
        if allowed and value not in allowed:
            result.add_error(f"Поле '{field}': '{value}' не из допустимых {allowed}", field=field)

        # Regex паттерн
        pattern = rules.get('pattern')
        if pattern and isinstance(value, str):
            import re
            if not re.match(pattern, value):
                result.add_error(
                    f"Поле '{field}': '{value}' не соответствует паттерну", field=field
                )

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.warning(message, source='validation')
        else:
            print(f"[Validation] {message}")
