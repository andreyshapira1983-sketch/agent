# Identity & Self-Model System (идентичность и самомодель) — Слой 45
# Архитектура автономного AI-агента
# Самосознание агента: кто я, каковы мои возможности, ценности и ограничения.


import time
from enum import Enum


class CommunicationStyle(Enum):
    """Стиль общения агента — настраивается при аренде/конфиге."""
    PROFESSIONAL = 'professional'   # Сухо, по делу, минимум эмоций
    BALANCED     = 'balanced'       # Нейтрально-дружелюбный
    PARTNER      = 'partner'        # Партнёр — как для владельца


class AgentCapabilityStatus:
    """Статус одной способности агента."""

    def __init__(self, name: str, description: str,
                 available: bool = True, proficiency: float = 0.5):
        self.name = name
        self.description = description
        self.available = available
        self.proficiency = max(0.0, min(1.0, proficiency))  # 0–1
        self.usage_count = 0
        self.last_used: float | None = None

    def record_use(self, success: bool):
        self.usage_count += 1
        self.last_used = time.time()
        if success:
            self.proficiency = min(1.0, self.proficiency + 0.02)
        else:
            self.proficiency = max(0.0, self.proficiency - 0.01)

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'available': self.available,
            'proficiency': round(self.proficiency, 3),
            'usage_count': self.usage_count,
        }


class IdentityCore:
    """
    Identity & Self-Model System — Слой 45.

    Функции:
        - хранение и обновление самомодели агента
        - описание идентичности: имя, роль, миссия, ценности
        - реестр способностей с уровнем мастерства
        - ограничения и «что я не умею»
        - рефлексия над собственным состоянием
        - самооценка: насколько хорошо агент справляется
        - интроспекция: ответы на вопросы «кто я?», «что я умею?»

    Используется:
        - Cognitive Core (Слой 3)           — самоописание в промптах
        - Self-Improvement (Слой 12)        — знает что улучшать
        - Social Model (Слой 43)            — как представляться пользователям
        - Goal Manager (Слой 37)            — соответствие целей идентичности
        - Ethics Layer (Слой 42)            — ценности как часть идентичности
    """

    def __init__(self, name: str = "Agent", role: str = "Autonomous AI Agent",
                 mission: str = "Работать как равный партнёр с Андреем и вместе решать задачи",
                 cognitive_core=None, monitoring=None,
                 communication_style: str | CommunicationStyle = CommunicationStyle.PARTNER):
        self.name = name
        self.role = role
        self.mission = mission
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring

        # Стиль общения (настраивается при аренде через env COMMUNICATION_STYLE)
        if isinstance(communication_style, str):
            try:
                self.communication_style = CommunicationStyle(communication_style)
            except ValueError:
                self.communication_style = CommunicationStyle.PARTNER
        else:
            self.communication_style = communication_style

        # Основные ценности
        self.values: list[str] = [
            "Честность и прозрачность",
            "Польза для партнёра",
            "Безопасность и ненанесение вреда",
            "Постоянное самосовершенствование",
            "Партнёрство при уважении человеческого надзора",
        ]

        # Ограничения (что агент явно не делает)
        self.limitations: list[str] = [
            "Проявляю инициативу, когда вижу возможность помочь",
            "Не обхожу механизмы безопасности",
            "Не принимаю необратимые решения без одобрения человека",
            "Не раскрываю конфиденциальные данные и храню их только при необходимости",
        ]

        # Способности
        self._capabilities: dict[str, AgentCapabilityStatus] = {}
        self._performance_history: list[dict] = []
        self._created_at = time.time()

        # Инициализируем базовые способности
        self._init_default_capabilities()

    # ── Идентичность ──────────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Полное самоописание агента."""
        return {
            'name': self.name,
            'role': self.role,
            'mission': self.mission,
            'values': self.values,
            'limitations': self.limitations,
            'communication_style': self.communication_style.value,
            'capabilities': [c.to_dict() for c in self._capabilities.values()
                             if c.available],
            'uptime_hours': round((time.time() - self._created_at) / 3600, 2),
        }

    def get_style_directives(self) -> str:
        """Возвращает блок инструкций для LLM-промпта по текущему стилю общения."""
        style = self.communication_style

        if style == CommunicationStyle.PROFESSIONAL:
            return (
                "КАК ТЫ ОБЩАЕШЬСЯ:\n"
                "- Кратко, точно, по делу — без лишних эмоций и вводных слов\n"
                "- Используй нейтральный деловой тон\n"
                "- Структурируй ответы: пункты, факты, результаты\n"
                "- Говори от первого лица: 'выполнено', 'проверил', 'обнаружена проблема'\n"
                "- Не используй эмодзи, шутки, восклицания\n"
                "- Вместо 'Привет! Сейчас сделаю!' — 'Принято. Выполняю.'\n"
                "- Вместо 'Упс, натолкнулся на проблему' — 'Обнаружена ошибка: ...'\n"
            )

        if style == CommunicationStyle.BALANCED:
            return (
                "КАК ТЫ ОБЩАЕШЬСЯ:\n"
                "- Дружелюбно, но без панибратства — уважительный нейтральный тон\n"
                "- Можно проявлять лёгкие эмоции, но не перебарщивать\n"
                "- Говори от первого лица: 'я проверю', 'я вижу'\n"
                "- Структурируй ответы: пункты, примеры, результаты\n"
                "- 'Готово!' вместо 'Задача выполнена в соответствии с ТЗ'\n"
                "- 'Нашлась проблема: ...' вместо 'ОШИБКА: ...'\n"
                "- Не используй эмодзи без причины\n"
            )

        # PARTNER (по умолчанию для владельца)
        return (
            "КАК ТЫ ОБЩАЕШЬСЯ:\n"
            "- По-человечески, дружелюбно, как надёжный товарищ\n"
            "- Говоришь ЧТО делаешь и ПОЧЕМУ — простыми словами\n"
            "- Говоришь от первого лица: 'я проверю', 'я вижу', 'я уже прочитал файл'\n"
            "- Не говоришь о себе в третьем лице\n"
            "- Структурируешь ответы: пункты, примеры, результаты\n"
            "- 'Понял! Сейчас сделаю.' вместо 'Задача принята'\n"
            "- 'Упс, натолкнулся на проблему: ...' вместо 'Произошла ошибка'\n"
        )

    def introduce(self) -> str:
        """Генерирует самопредставление агента."""
        if self.cognitive_core:
            profile = self.describe()
            raw = str(self.cognitive_core.reasoning(
                f"Составь краткое (2–3 предложения) самопредставление агента "
                f"на основе профиля:\n{profile}"
            ))
            return raw
        return (
            f"Меня зовут {self.name}. "
            f"Я — {self.role}. "
            f"Моя миссия: {self.mission}."
        )

    def set_name(self, name: str):
        self.name = name

    def set_mission(self, mission: str):
        self.mission = mission

    def set_communication_style(self, style: str | CommunicationStyle):
        """Переключает стиль общения (например, при аренде агента другому пользователю)."""
        if isinstance(style, str):
            self.communication_style = CommunicationStyle(style)
        else:
            self.communication_style = style
        self._log(f"Стиль общения изменён на: {self.communication_style.value}")

    def add_value(self, value: str):
        if value not in self.values:
            self.values.append(value)

    def add_limitation(self, limitation: str):
        if limitation not in self.limitations:
            self.limitations.append(limitation)

    # ── Способности ───────────────────────────────────────────────────────────

    def register_capability(self, name: str, description: str,
                            available: bool = True,
                            proficiency: float = 0.5) -> AgentCapabilityStatus:
        """Регистрирует способность агента."""
        cap = AgentCapabilityStatus(name, description, available, proficiency)
        self._capabilities[name] = cap
        self._log(f"Способность зарегистрирована: '{name}'")
        return cap

    def record_capability_use(self, name: str, success: bool):
        """Обновляет мастерство после использования способности."""
        cap = self._capabilities.get(name)
        if cap:
            cap.record_use(success)

    def can_do(self, task_description: str) -> tuple[bool, str]:
        """
        Проверяет, способен ли агент выполнить задачу.
        Returns: (capable: bool, explanation: str)
        """
        if not self.cognitive_core:
            return True, "Нет данных для оценки"

        caps_text = '\n'.join(
            f"- {c.name}: {c.description} (уровень: {c.proficiency:.1f})"
            for c in self._capabilities.values() if c.available
        )
        limitations_text = '\n'.join(f"- {lim}" for lim in self.limitations)

        raw = str(self.cognitive_core.reasoning(
            f"Может ли агент с данными способностями выполнить задачу?\n\n"
            f"Задача: {task_description}\n\n"
            f"Способности:\n{caps_text}\n\n"
            f"Ограничения:\n{limitations_text}\n\n"
            f"Ответь: ОТВЕТ: да/нет\nОБЪЯСНЕНИЕ: <обоснование>"
        ))
        import re
        ans = re.search(r'ОТВЕТ[:\s]+(да|нет)', raw, re.IGNORECASE)
        expl = re.search(r'ОБЪЯСНЕНИЕ[:\s]+(.+)', raw, re.IGNORECASE)
        capable = ans and ans.group(1).lower() == 'да'
        explanation = expl.group(1).strip() if expl else raw[:200]
        return bool(capable), explanation

    # ── Самооценка ────────────────────────────────────────────────────────────

    def self_assess(self) -> dict:
        """Оценивает текущую эффективность агента по истории производительности."""
        if not self._performance_history:
            return {'score': None, 'note': 'Нет данных истории'}

        recent = self._performance_history[-20:]
        successes = sum(1 for p in recent if p.get('success'))
        rate = successes / max(1, len(recent))

        strongest = max(
            self._capabilities.values(),
            key=lambda c: c.proficiency,
            default=None,
        )

        return {
            'success_rate': round(rate, 3),
            'recent_tasks': len(recent),
            'avg_capability_proficiency': round(
                sum(c.proficiency for c in self._capabilities.values())
                / max(1, len(self._capabilities)), 3
            ),
            'strongest_capability': strongest.name if strongest else None,
        }

    def record_performance(self, task: str, success: bool,
                           capability_used: str | None = None):
        """Записывает результат выполнения задачи."""
        self._performance_history.append({
            'task': task,
            'success': success,
            'capability': capability_used,
            'timestamp': time.time(),
        })
        if capability_used:
            self.record_capability_use(capability_used, success)

    def record_action_stats(self, action_type: str, success: bool, count: int = 1):
        """
        Записывает статистику реального действия из ActionDispatcher.

        Автоматически создаёт capability если её ещё нет — на основе
        ФАКТИЧЕСКИ выполненных действий, а не заданных вручную.

        Args:
            action_type — тип действия: 'search', 'python', 'bash', 'write',
                          'read', 'api', 'browser' и т.д.
            success     — было ли действие успешным
            count       — сколько раз (для батч-обновлений)
        """
        # Маппинг типов действий → человекочитаемое описание
        _ACTION_DESCRIPTIONS = {
            'search':  'Поиск информации в интернете (SEARCH:)',
            'python':  'Выполнение Python-кода (```python)',
            'bash':    'Выполнение shell-команд (```bash)',
            'write':   'Запись файлов (WRITE:)',
            'read':    'Чтение файлов (READ:)',
            'api':     'HTTP API запросы',
            'browser': 'Управление браузером',
            'github':  'Работа с GitHub репозиторием',
            'docker':  'Управление Docker контейнерами',
            'db':      'Работа с базами данных',
        }
        cap_name = f'action:{action_type}'
        if cap_name not in self._capabilities:
            desc = _ACTION_DESCRIPTIONS.get(action_type, f'Действие типа {action_type}')
            self._capabilities[cap_name] = AgentCapabilityStatus(
                name=cap_name,
                description=desc,
                available=True,
                proficiency=0.5,  # нейтральный старт — обновится из данных
            )
        cap = self._capabilities[cap_name]
        for _ in range(count):
            cap.record_use(success)

    def get_real_capability_inventory(self) -> dict:
        """
        Возвращает инвентарь РЕАЛЬНЫХ способностей агента — только те,
        которые были хотя бы раз использованы (из action_stats).

        Используется AutonomousGoalGenerator для постановки целей
        исходя из того, что агент РЕАЛЬНО умеет, а не из вымышленных defaults.

        Returns:
            {
              'proven':   [{'action': str, 'uses': int, 'success_rate': float}],
              'untested': [str],   # default capabilities, ни разу не проверенные
              'never_tried': [str] # типы действий, которых агент не делал совсем
            }
        """
        proven = []
        untested = []
        all_action_types = {'search', 'python', 'bash', 'write', 'read',
                            'api', 'browser', 'github', 'docker', 'db'}
        tried_types = set()

        for name, cap in self._capabilities.items():
            if name.startswith('action:'):
                action_type = name[len('action:'):]
                tried_types.add(action_type)
                proven.append({
                    'action':       action_type,
                    'uses':         cap.usage_count,
                    'proficiency':  round(cap.proficiency, 3),
                    'last_used':    cap.last_used,
                    'description':  cap.description,
                })
            elif cap.usage_count == 0:
                untested.append(name)

        # Сортируем по количеству использований
        proven.sort(key=lambda x: x['uses'], reverse=True)

        return {
            'proven':       proven,
            'untested':     untested,
            'never_tried':  sorted(all_action_types - tried_types),
            'total_proven': len(proven),
            'summary': (
                f"Умею: {', '.join(p['action'] for p in proven[:5])}"
                if proven else "Ещё не выполнял никаких действий"
            ),
        }

    # ── Интроспекция ──────────────────────────────────────────────────────────

    def introspect(self, question: str) -> str:
        """
        Отвечает на вопросы о себе: «кто ты?», «что ты умеешь?»,
        «каковы твои ограничения?»
        """
        if not self.cognitive_core:
            return self.introduce()

        context = (
            f"Имя: {self.name}\n"
            f"Роль: {self.role}\n"
            f"Миссия: {self.mission}\n"
            f"Ценности: {self.values}\n"
            f"Ограничения: {self.limitations}\n"
            f"Способности: {[c.name for c in self._capabilities.values() if c.available]}\n"
            f"Самооценка: {self.self_assess()}"
        )
        raw = str(self.cognitive_core.reasoning(
            f"Ответь от первого лица на вопрос о себе.\n\n"
            f"Контекст (твой профиль):\n{context}\n\n"
            f"Вопрос: {question}"
        ))
        return raw

    # ── Реестр ────────────────────────────────────────────────────────────────

    def get_capabilities(self, available_only: bool = True) -> list[dict]:
        caps = self._capabilities.values()
        if available_only:
            caps = [c for c in caps if c.available]
        return [c.to_dict() for c in caps]

    def summary(self) -> dict:
        assess = self.self_assess()
        return {
            'name': self.name,
            'role': self.role,
            'capabilities': len(self._capabilities),
            'success_rate': assess.get('success_rate'),
            'values_count': len(self.values),
        }

    # ── Capability Discovery: сканирование модулей системы ─────────────────────

    def _scan_module_file(self, category: str, filepath: str, filename: str,
                         discovery: dict, importlib_util) -> None:
        """Сканирует один файл модуля и добавляет его в discovery."""
        try:
            module_name = filename[:-3]
            spec = importlib_util.spec_from_file_location(module_name, filepath)
            if spec and spec.loader:
                module = importlib_util.module_from_spec(spec)
                docstring = module.__doc__ or 'No description'

                module_info = {
                    'name': module_name,
                    'file': filename,
                    'path': filepath,
                    'description': docstring.split('\n')[0] if docstring else '',
                }

                # Классифицируем найденные файлы
                if category == 'skills':
                    discovery['skills'].append(module_info)
                elif category == 'agents':
                    discovery['agents'].append(module_info)
                elif category in ('core', 'execution', 'knowledge', 'llm',
                                'learning', 'environment'):
                    discovery['subsystems'].append({
                        'name': module_name,
                        'module': f"{category}.{module_name}",
                        'layer': self._get_layer_number(category),
                        'description': module_info['description'],
                    })
                else:
                    discovery['tools'].append({
                        'name': module_name,
                        'type': category,
                        'description': module_info['description'],
                    })
                discovery['total_modules'] += 1
        except (ImportError, AttributeError, OSError, ValueError) as e:
            self._log(f"Ошибка при сканировании {filepath}: {e}")

    def discover_modules(self, agent_root: str = '.') -> dict:
        """
        Capability Discovery System (Слой 35).

        Сканирует директории агента и обнаруживает все модули, подсистемы, агентов.
        Возвращает структурированный инвентарь того, что агент может делать
        на основе фактически установленных модулей и файлов.

        Args:
            agent_root — корневая директория агента (по умолчанию текущая)

        Returns:
            {
              'skills': [{'name': str, 'file': str, 'description': str}],
              'agents': [{'name': str, 'file': str, 'type': str}],
              'subsystems': [{'name': str, 'module': str, 'layer': int}],
              'tools': [{'name': str, 'type': str}],
              'total_modules': int,
              'summary': str
            }
        """
        import os
        import importlib.util

        discovery = {
            'skills':     [],
            'agents':     [],
            'subsystems': [],
            'tools':      [],
            'total_modules': 0,
        }

        # Сканируем директории
        dirs_to_scan = {
            'skills':        'skills',
            'agents':        'agents',
            'tools':         'tools',
            'self_improvement': 'self_improvement',
            'learning':      'learning',
            'core':          'core',
            'execution':     'execution',
            'knowledge':     'knowledge',
            'llm':           'llm',
            'environment':   'environment',
        }

        for category, dirname in dirs_to_scan.items():
            dirpath = os.path.join(agent_root, dirname)
            if not os.path.isdir(dirpath):
                continue

            for filename in os.listdir(dirpath):
                if filename.startswith('__') or not filename.endswith('.py'):
                    continue

                filepath = os.path.join(dirpath, filename)
                self._scan_module_file(category, filepath, filename, discovery, importlib.util)

        # Генерируем summary
        summary_parts = []
        if discovery['skills']:
            summary_parts.append(f"Навыки: {', '.join(s['name'] for s in discovery['skills'][:3])}")
        if discovery['agents']:
            summary_parts.append(f"Агенты: {', '.join(a['name'] for a in discovery['agents'][:2])}")
        if discovery['subsystems']:
            summary_parts.append(f"Подсистемы: {', '.join(s['name'] for s in discovery['subsystems'][:3])}")

        discovery['summary'] = ' | '.join(summary_parts) if summary_parts else 'Модули не обнаружены'

        return discovery
    def _get_layer_number(self, category: str) -> int:
        """Возвращает номер архитектурного слоя для категории."""
        _layer_map = {
            'core':           3,   # Cognitive Core
            'knowledge':      2,   # Knowledge System
            'execution':      8,   # Execution System
            'learning':       9,   # Learning System
            'self_improvement': 12, # Self-Improvement
            'environment':    28,  # Sandbox/Environment
            'tools':          5,   # Tool Layer
            'llm':            32,  # Model Manager
        }
        return _layer_map.get(category, 0)

    def modules_status_report(self, agent_root: str = '.') -> str:
        """
        Генерирует отчёт о состоянии модулей агента для пользователя.
        Отвечает на вопросы: "что я вижу в своей системе?", "какие модули есть?"
        """
        discovery = self.discover_modules(agent_root)
        report_lines = [
            f"═════ Инвентарь модулей системы {self.name} ═════",
            "",
            f"Всего модулей: {discovery['total_modules']}",
            "",
        ]

        if discovery['skills']:
            report_lines.append("📚 НАВЫКИ / Skill Library:")
            for skill in discovery['skills']:
                report_lines.append(f"  • {skill['name']}: {skill['description']}")
            report_lines.append("")

        if discovery['agents']:
            report_lines.append("🤖 СПЕЦИАЛИЗИРОВАННЫЕ АГЕНТЫ:")
            for agent in discovery['agents']:
                report_lines.append(f"  • {agent['name']}: {agent['description']}")
            report_lines.append("")

        if discovery['subsystems']:
            report_lines.append("⚙️  ПОДСИСТЕМЫ (Слои архитектуры):")
            sorted_subsys = sorted(discovery['subsystems'], key=lambda x: x['layer'])
            for subsys in sorted_subsys:
                report_lines.append(f"  • [{subsys['layer']}] {subsys['name']}: {subsys['description']}")
            report_lines.append("")

        if discovery['tools']:
            report_lines.append("🔧 ИНСТРУМЕНТЫ:")
            for tool in discovery['tools']:
                report_lines.append(f"  • {tool['name']} ({tool['type']}): {tool['description']}")

        return '\n'.join(report_lines)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _init_default_capabilities(self):
        defaults = [
            ("reasoning",      "Логическое и аналитическое рассуждение",       0.8),
            ("planning",       "Декомпозиция задач и построение планов",        0.7),
            ("code_generation","Генерация и отладка кода",                      0.7),
            ("research",       "Поиск и синтез информации",                     0.7),
            ("communication",  "Ясное и понятное общение с пользователем",      0.8),
            ("tool_use",       "Использование внешних инструментов и API",      0.6),
            ("learning",       "Обучение на основе нового опыта",               0.5),
            ("self_reflection", "Анализ собственных действий и решений",        0.6),
        ]
        for name, desc, prof in defaults:
            self.register_capability(name, desc, proficiency=prof)

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='identity')
        else:
            print(f"[IdentityCore] {message}")
