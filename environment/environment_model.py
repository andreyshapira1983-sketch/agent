# Environment Modeling Layer (модель среды) — Слой 27
# Архитектура автономного AI-агента
# Внутреннее представление мира: состояние системы, контекст окружения,
# прогнозирование последствий действий, симуляция сценариев.
#
# Архитектура предсказания:
#   1. Детерминированный уровень — state machine + risk patterns (без LLM)
#   2. LLM-уровень — если cognitive_core подключён (для сложных случаев)


import time
import copy


class EnvironmentState:
    """Снимок состояния среды в момент времени."""

    def __init__(self, state_id: str, data: dict):
        self.state_id = state_id
        self.data = copy.deepcopy(data)
        self.timestamp = time.time()

    def to_dict(self):
        return {
            'state_id': self.state_id,
            'data': self.data,
            'timestamp': self.timestamp,
        }


class StateTransition:
    """
    Один переход в state machine.
    from_state -[action_pattern]-> to_state с ожидаемыми эффектами.
    """

    def __init__(self, from_state: str, action_pattern: str, to_state: str | None,
                 effects: list[str], reversible: bool = True,
                 risk_level: str = 'low'):
        self.from_state    = from_state      # '*' = любое состояние
        self.action_pattern = action_pattern.lower()   # подстрока для поиска в action
        self.to_state      = to_state
        self.effects       = effects         # список текстовых описаний эффектов
        self.reversible    = reversible      # можно ли откатить
        self.risk_level    = risk_level      # 'low' | 'medium' | 'high' | 'critical'

    def matches(self, current_state: str, action: str) -> bool:
        state_ok = (self.from_state == '*' or
                    self.from_state == current_state)
        action_ok = self.action_pattern in action.lower()
        return state_ok and action_ok


class EnvironmentModel:
    """
    Environment Modeling Layer — Слой 27.

    Функции:
        - модель текущего состояния системы и окружения
        - state machine с зарегистрированными переходами
        - детерминированное предсказание последствий (без LLM)
        - LLM-уровень для неизвестных/сложных действий
        - what-if анализ с ветвлением состояний
        - оценка риска действий по паттернам
        - история изменений состояния среды

    Используется:
        - Cognitive Core (Слой 3)          — контекст для рассуждений
        - Simulation/Sandbox (Слой 28)     — входная модель для симуляции
        - Autonomous Loop (Слой 20)        — фаза observe/analyze
        - Planning (внутри Cognitive Core) — планирование с учётом среды
    """

    # ── Паттерны оценки риска (детерминированные) ─────────────────────────────
    _RISK_PATTERNS: dict[str, list[str]] = {
        'critical': [
            'rm -rf', 'drop database', 'format disk', 'mkfs', 'dd if=',
            'del /f /s', 'rd /s /q', 'wipe', 'overwrite production',
        ],
        'high': [
            'delete', 'remove', 'drop table', 'truncate', 'kill process',
            'shutdown', 'reboot', 'uninstall', 'revoke', 'purge',
            'drop index', 'alter table', 'rm ', 'rmdir',
        ],
        'medium': [
            'update', 'modify', 'write', 'push to', 'deploy', 'publish',
            'install', 'upgrade', 'patch', 'restart', 'migrate',
            'send email', 'post request', 'create user',
        ],
        'low': [
            'read', 'search', 'fetch', 'get', 'list', 'show', 'view',
            'analyze', 'check', 'inspect', 'describe', 'query', 'find',
        ],
    }

    # ── Переходы по умолчанию (жизненный цикл агента) ─────────────────────────
    _DEFAULT_TRANSITIONS: list[dict] = [
        dict(from_state='idle',       action_pattern='set goal',
             to_state='goal_set',
             effects=['Цель зафиксирована', 'Начинается планирование'],
             reversible=True, risk_level='low'),

        dict(from_state='goal_set',   action_pattern='start',
             to_state='executing',
             effects=['Агент начал выполнение', 'Ресурсы выделены'],
             reversible=False, risk_level='low'),

        dict(from_state='*',          action_pattern='read file',
             to_state=None,           # состояние не меняется
             effects=['Файл прочитан', 'Данные доступны в памяти'],
             reversible=True, risk_level='low'),

        dict(from_state='*',          action_pattern='write file',
             to_state=None,
             effects=['Файл изменён на диске', 'Старое содержимое перезаписано'],
             reversible=False, risk_level='medium'),

        dict(from_state='*',          action_pattern='delete file',
             to_state=None,
             effects=['Файл удалён', 'Восстановление только из резервной копии'],
             reversible=False, risk_level='high'),

        dict(from_state='*',          action_pattern='install',
             to_state=None,
             effects=['Пакет установлен в систему', 'Зависимости добавлены'],
             reversible=True, risk_level='medium'),

        dict(from_state='*',          action_pattern='deploy',
             to_state=None,
             effects=['Приложение развёрнуто', 'Предыдущая версия заменена'],
             reversible=False, risk_level='high'),

        dict(from_state='*',          action_pattern='send',
             to_state=None,
             effects=['Данные отправлены во внешнюю систему', 'Действие необратимо'],
             reversible=False, risk_level='medium'),

        dict(from_state='executing',  action_pattern='complete',
             to_state='idle',
             effects=['Задача завершена', 'Ресурсы освобождены'],
             reversible=False, risk_level='low'),

        dict(from_state='executing',  action_pattern='error',
             to_state='error_state',
             effects=['Зафиксирована ошибка', 'Ожидается восстановление'],
             reversible=True, risk_level='medium'),

        dict(from_state='error_state', action_pattern='repair',
             to_state='recovering',
             effects=['Запущено самовосстановление', 'Система нестабильна'],
             reversible=True, risk_level='medium'),

        dict(from_state='recovering', action_pattern='complete',
             to_state='idle',
             effects=['Восстановление завершено', 'Система стабильна'],
             reversible=False, risk_level='low'),
    ]

    def __init__(self, cognitive_core=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

        self._state: dict = {}                       # текущее состояние среды
        self._history: list[EnvironmentState] = []   # история состояний
        self._entities: dict[str, dict] = {}         # известные сущности мира
        self._relations: list[dict] = []             # связи между сущностями
        self._constraints: list[str] = []            # ограничения среды

        # ── State machine ──────────────────────────────────────────────────────
        self._sm_state: str = 'idle'                 # текущее состояние машины
        self._sm_states: dict[str, str] = {}         # name → description
        self._transitions: list[StateTransition] = []  # все переходы

        # Загружаем встроенные состояния и переходы
        self._register_default_states()
        self._register_default_transitions()

    # ── Управление состоянием ─────────────────────────────────────────────────

    def update(self, updates: dict, snapshot: bool = True):
        """
        Обновляет модель среды новыми данными.

        Args:
            updates  — словарь изменений
            snapshot — сохранять ли снимок перед обновлением
        """
        if snapshot and self._state:
            self._save_snapshot()

        self._state.update(updates)
        self._log(f"Среда обновлена: {list(updates.keys())}")

    def set(self, key: str, value):
        """Устанавливает одно значение в модели среды."""
        self._state[key] = value

    def get(self, key: str, default=None):
        """Возвращает значение из модели среды."""
        return self._state.get(key, default)

    def get_full_state(self) -> dict:
        """Возвращает полную копию текущего состояния."""
        return copy.deepcopy(self._state)

    def clear(self):
        """Очищает модель среды."""
        self._save_snapshot()
        self._state.clear()

    # ── Сущности и связи ─────────────────────────────────────────────────────

    def register_entity(self, name: str, properties: dict):
        """Регистрирует сущность в модели мира (сервис, агент, ресурс, пользователь)."""
        self._entities[name] = {
            'name': name,
            'properties': properties,
            'registered_at': time.time(),
        }
        self._log(f"Сущность зарегистрирована: '{name}'")

    def update_entity(self, name: str, updates: dict):
        """Обновляет свойства сущности."""
        if name not in self._entities:
            self.register_entity(name, updates)
        else:
            self._entities[name]['properties'].update(updates)

    def get_entity(self, name: str) -> dict | None:
        return self._entities.get(name)

    def add_relation(self, from_entity: str, relation: str, to_entity: str):
        """Добавляет связь: from_entity -[relation]-> to_entity."""
        self._relations.append({
            'from': from_entity,
            'relation': relation,
            'to': to_entity,
        })

    def get_relations(self, entity: str | None = None) -> list[dict]:
        if entity:
            return [r for r in self._relations
                    if r['from'] == entity or r['to'] == entity]
        return list(self._relations)

    # ── Ограничения ───────────────────────────────────────────────────────────

    def add_constraint(self, constraint: str):
        """Добавляет ограничение среды (например: 'нет доступа к интернету')."""
        self._constraints.append(constraint)

    def get_constraints(self) -> list[str]:
        return list(self._constraints)

    # ── State machine API ─────────────────────────────────────────────────────

    def register_state(self, name: str, description: str = ''):
        """Регистрирует новое состояние в state machine."""
        self._sm_states[name] = description

    def register_transition(self, from_state: str, action_pattern: str,
                             to_state: str | None, effects: list[str],
                             reversible: bool = True, risk_level: str = 'low'):
        """
        Регистрирует переход в state machine.

        Args:
            from_state     — исходное состояние ('*' = любое)
            action_pattern — подстрока в описании действия (регистр игнорируется)
            to_state       — целевое состояние (None = не меняется)
            effects        — список текстовых эффектов перехода
            reversible     — можно ли откатить
            risk_level     — 'low' | 'medium' | 'high' | 'critical'
        """
        t = StateTransition(from_state, action_pattern, to_state,
                            effects, reversible, risk_level)
        self._transitions.append(t)

    def apply_transition(self, action: str) -> dict | None:
        """
        Находит первый подходящий переход для действия и применяет его.

        Returns:
            Словарь с деталями перехода или None если переход не найден.
        """
        for t in self._transitions:
            if t.matches(self._sm_state, action):
                old_state = self._sm_state
                if t.to_state:
                    self._sm_state = t.to_state
                result = {
                    'from_state':  old_state,
                    'to_state':    self._sm_state,
                    'effects':     t.effects,
                    'reversible':  t.reversible,
                    'risk_level':  t.risk_level,
                    'transition':  t.action_pattern,
                }
                self._log(f"Переход: {old_state} -[{t.action_pattern}]-> {self._sm_state}")
                return result
        return None

    def get_sm_state(self) -> str:
        """Возвращает текущее состояние state machine."""
        return self._sm_state

    def set_sm_state(self, state: str):
        """Принудительно устанавливает состояние (для восстановления после сбоя)."""
        self._sm_state = state

    # ── Оценка риска (детерминированная) ──────────────────────────────────────

    def assess_risk(self, action: str) -> dict:
        """
        Оценивает риск действия детерминированно по паттернам.
        Работает без LLM.

        Returns:
            {'level': 'low'|'medium'|'high'|'critical',
             'matched_pattern': str|None,
             'reversible': bool,
             'blocked_by_constraint': str|None}
        """
        action_l = action.lower()
        risk_level   = 'low'
        matched      = None

        # Проверяем паттерны от высокого к низкому
        for level in ('critical', 'high', 'medium', 'low'):
            for pattern in self._RISK_PATTERNS[level]:
                if pattern in action_l:
                    risk_level = level
                    matched    = pattern
                    break
            if matched:
                break

        # Проверяем блокировку ограничениями среды
        blocked_by = None
        for constraint in self._constraints:
            c = constraint.lower()
            if any(w in action_l for w in c.split() if len(w) > 3):
                blocked_by = constraint
                break

        # Обратимость: high/critical → необратимо
        reversible = risk_level in ('low', 'medium')

        return {
            'level':                  risk_level,
            'matched_pattern':        matched,
            'reversible':             reversible,
            'blocked_by_constraint':  blocked_by,
        }

    # ── Прогнозирование последствий ───────────────────────────────────────────

    def predict_outcome(self, action: str, context: dict | None = None) -> dict:
        """
        Прогнозирует последствия действия в текущей среде.

        Уровень 1 (детерминированный, без LLM):
            - Ищет подходящий переход в state machine
            - Оценивает риск по паттернам
            - Проверяет ограничения среды

        Уровень 2 (LLM, если подключён):
            - Дополняет детерминированный прогноз анализом сложных зависимостей

        Returns:
            {
                'action': str,
                'current_state': str,
                'next_state': str | None,
                'effects': list[str],
                'risk': dict,
                'reversible': bool,
                'constraints_violated': list[str],
                'llm_analysis': str | None,   # только если cognitive_core подключён
                'method': 'deterministic' | 'llm_enhanced'
            }
        """
        context = context or {}

        # ── Детерминированный уровень ──────────────────────────────────────────
        risk = self.assess_risk(action)

        # Ищем переход в state machine
        next_state = None
        effects    = []
        for t in self._transitions:
            if t.matches(self._sm_state, action):
                next_state = t.to_state or self._sm_state
                effects    = list(t.effects)
                break

        # Проверяем нарушение ограничений среды
        action_l = action.lower()
        violated = [
            c for c in self._constraints
            if any(w in action_l for w in c.lower().split() if len(w) > 3)
        ]

        result = {
            'action':               action,
            'current_state':        self._sm_state,
            'next_state':           next_state,
            'effects':              effects,
            'risk':                 risk,
            'reversible':           risk['reversible'],
            'constraints_violated': violated,
            'llm_analysis':         None,
            'method':               'deterministic',
        }

        # ── LLM уровень (если подключён) ──────────────────────────────────────
        if self.cognitive_core:
            state_summary = self._summarize_state()
            det_summary   = (
                f"Детерминированный прогноз: риск={risk['level']}, "
                f"обратимо={risk['reversible']}, "
                f"эффекты={effects if effects else 'неизвестны'}"
            )
            llm_text = self.cognitive_core.reasoning(
                f"Текущее состояние среды:\n{state_summary}\n\n"
                f"Ограничения: {self._constraints}\n"
                f"{det_summary}\n\n"
                f"Планируемое действие: {action}\n\n"
                f"Дополни прогноз: побочные эффекты, зависимости, "
                f"альтернативы с меньшим риском."
            )
            result['llm_analysis'] = str(llm_text)
            result['method']       = 'llm_enhanced'

        self._log(f"Прогноз [{result['method']}] '{action[:50]}': "
                  f"риск={risk['level']}, обратимо={risk['reversible']}")
        return result

    def what_if(self, hypothetical_state: dict, action: str) -> dict:
        """
        What-if анализ: что будет если среда окажется в hypothetical_state
        и агент совершит action.

        Работает на двух уровнях:
          1. Детерминированный — временно применяет гипотетическое состояние,
             оценивает риск и ищет переходы
          2. LLM — дополняет анализ если cognitive_core подключён

        Returns:
            {
                'hypothetical_state': dict,
                'action': str,
                'predicted_effects': list[str],
                'risk': dict,
                'diff_from_current': list[str],  # что изменится vs текущее
                'llm_analysis': str | None,
            }
        """
        # Вычисляем разницу с текущим состоянием
        diff = [
            f"{k}: {self._state.get(k)} → {v}"
            for k, v in hypothetical_state.items()
            if self._state.get(k) != v
        ]

        # Детерминированная оценка в гипотетическом контексте
        risk    = self.assess_risk(action)
        effects = []
        for t in self._transitions:
            # Оцениваем с учётом гипотетического состояния среды
            hyp_sm_state = hypothetical_state.get('sm_state', self._sm_state)
            if t.from_state in ('*', hyp_sm_state) and t.action_pattern in action.lower():
                effects = list(t.effects)
                break

        result = {
            'hypothetical_state':  hypothetical_state,
            'action':              action,
            'predicted_effects':   effects,
            'risk':                risk,
            'diff_from_current':   diff,
            'llm_analysis':        None,
        }

        if self.cognitive_core:
            llm_text = self.cognitive_core.reasoning(
                f"Текущее состояние среды:\n{self._summarize_state()}\n\n"
                f"Гипотетическое изменение: {diff}\n\n"
                f"Действие: {action}\n"
                f"Детерминированный прогноз: риск={risk['level']}, "
                f"эффекты={effects if effects else 'неизвестны'}\n\n"
                f"What-if: возможные исходы, риски, альтернативы."
            )
            result['llm_analysis'] = str(llm_text)

        return result

    # ── История состояний ─────────────────────────────────────────────────────

    def get_history(self, last_n: int | None = None) -> list[dict]:
        h = self._history
        if last_n:
            h = h[-last_n:]
        return [s.to_dict() for s in h]

    def rollback(self, steps: int = 1):
        """Откатывает состояние среды на steps назад."""
        if len(self._history) < steps:
            raise IndexError(f"Недостаточно снимков для отката на {steps} шагов")
        target = self._history[-(steps)]
        self._state = copy.deepcopy(target.data)
        self._log(f"Среда откатана на {steps} шаг(ов) назад")

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> EnvironmentState:
        """Явно сохраняет снимок текущего состояния."""
        return self._save_snapshot()

    def _save_snapshot(self) -> EnvironmentState:
        import uuid
        snap = EnvironmentState(str(uuid.uuid4())[:8], self._state)
        self._history.append(snap)
        return snap

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _register_default_states(self):
        """Регистрирует стандартные состояния жизненного цикла агента."""
        defaults = {
            'idle':         'Агент ожидает задачи',
            'goal_set':     'Цель установлена, планирование',
            'executing':    'Агент выполняет задачу',
            'error_state':  'Зафиксирована ошибка, требуется восстановление',
            'recovering':   'Идёт самовосстановление',
            'paused':       'Выполнение приостановлено',
            'learning':     'Агент в режиме обучения',
        }
        for name, desc in defaults.items():
            self.register_state(name, desc)

    def _register_default_transitions(self):
        """Загружает встроенные переходы из _DEFAULT_TRANSITIONS."""
        for td in self._DEFAULT_TRANSITIONS:
            self.register_transition(
                from_state     = td['from_state'],
                action_pattern = td['action_pattern'],
                to_state       = td.get('to_state'),
                effects        = td['effects'],
                reversible     = td.get('reversible', True),
                risk_level     = td.get('risk_level', 'low'),
            )

    def _summarize_state(self) -> str:
        lines = [f"  sm_state: {self._sm_state}"]
        for k, v in self._state.items():
            lines.append(f"  {k}: {str(v)[:100]}")
        if self._entities:
            lines.append(f"  Сущности: {list(self._entities.keys())}")
        if self._constraints:
            lines.append(f"  Ограничения: {self._constraints}")
        return '\n'.join(lines) if lines else '(пусто)'

    def summary(self) -> dict:
        """Краткая статистика модели среды."""
        return {
            'sm_state':          self._sm_state,
            'sm_states_count':   len(self._sm_states),
            'transitions_count': len(self._transitions),
            'entities_count':    len(self._entities),
            'constraints_count': len(self._constraints),
            'history_length':    len(self._history),
            'state_keys':        list(self._state.keys()),
        }

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='environment_model')
        else:
            print(f"[EnvironmentModel] {message}")
