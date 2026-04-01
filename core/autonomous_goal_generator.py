# Autonomous Goal Generator — самостоятельная постановка целей
# Архитектура автономного AI-агента — Слой 37 (Goal Management расширение)
#
# Анализирует рефлексию, пробелы в возможностях и историю ошибок →
# самостоятельно ставит новые цели в GoalManager, не дожидаясь команды человека.
#
# Три источника целей:
#   1. generate_from_reflection() — из инсайтов Reflection System
#   2. propose_from_gap(gap)      — из capability gap (CapabilityDiscovery)
#   3. propose_new_agent(desc)    — запускает полный цикл сборки агента
#
# Цели с высоким приоритетом (build_agent, fix_recurring_error) подхватываются
# AutonomousLoop в _plan() → выполняются модулем AgentSpawner/ModuleBuilder.
#
# pylint: disable=broad-except

from __future__ import annotations

import time


class AutonomousGoalGenerator:
    """
    Автономный генератор целей.

    Связан с:
        Cognitive Core (Слой 3)   — формулирует цели естественным языком
        GoalManager   (Слой 37)   — куда добавляются цели
        Reflection    (Слой 10)   — источник инсайтов
        ModuleBuilder             — выполняет цели типа 'build_agent'
        AgentSpawner              — регистрирует построенных агентов

    Поведение в _improve цикла:
        - каждые 10 циклов: generate_from_reflection()
        - при capability gap: propose_from_gap(gap)
    """

    # Теги целей которые генерирует этот модуль
    _TAG = 'auto_generated'

    def __init__(
        self,
        cognitive_core=None,
        goal_manager=None,
        reflection=None,
        module_builder=None,
        agent_spawner=None,
        monitoring=None,
        identity=None,
        governance=None,
        ethics=None,
    ):
        self.cognitive_core = cognitive_core
        self.goal_manager   = goal_manager
        self.reflection     = reflection
        self.module_builder = module_builder
        self.agent_spawner  = agent_spawner
        self.monitoring     = monitoring
        self.identity       = identity  # Слой 45 — реальный инвентарь способностей
        self.governance     = governance
        self.ethics         = ethics

        # Антиспам: не генерировать цель с таким же текстом чаще чем раз в N секунд
        self._recent_goals: dict[str, float] = {}   # text-hash → timestamp
        self._seen_workdir_files: set[str] = set()
        self._cooldown_sec = 3600  # 1 час между одинаковыми целями

    # ── Основной API ──────────────────────────────────────────────────────────

    def generate_from_reflection(self) -> list[str]:
        """
        Читает инсайты из Reflection System и ставит цели под задачи что
        агент не смог решить или решил плохо.

        Returns:
            Список добавленных описаний целей.
        """
        if not self.reflection:
            return []

        insights = []
        try:
            insights = self.reflection.get_insights() or []
        except Exception:
            return []

        if not insights:
            return []

        # Берём последние 5 инсайтов
        recent = insights[-5:] if len(insights) > 5 else insights
        added: list[str] = []

        for insight in recent:
            goal_desc = self._insight_to_goal(str(insight))
            if not goal_desc:
                continue
            if self._is_duplicate(goal_desc):
                continue
            self._add_goal(goal_desc, priority=2, tags=[self._TAG, 'from_reflection'])
            self._mark_recent(goal_desc)
            added.append(goal_desc)

        if added:
            self._log(f"[goal_gen] Добавлено {len(added)} целей из рефлексии.")
        return added

    def generate_from_inventory(self) -> list[str]:
        """
        Ставит цели на основе РЕАЛЬНОГО инвентаря способностей (Identity, Слой 45).

        Логика:
          1. Если агент что-то делает хорошо (proficiency > 0.7) → углубить это
          2. Если агент что-то делает плохо (proficiency < 0.4, uses > 3) → улучшить
          3. Если есть типы действий, которых агент ни разу не пробовал → попробовать

        Возвращает список добавленных целей.
        """
        if not self.identity:
            return []

        try:
            inv = self.identity.get_real_capability_inventory()
        except Exception:
            return []

        added: list[str] = []

        # 1. Слабые места — то что использует, но плохо получается
        for item in inv.get('proven', []):
            if item['uses'] >= 3 and item['proficiency'] < 0.4:
                goal = (
                    f"Улучшить навык '{item['action']}': "
                    f"{item['description']}. "
                    f"Выполнено {item['uses']} раз, успешность {item['proficiency']:.0%}. "
                    f"Найди причины ошибок и исправь подход."
                )
                if not self._is_duplicate(goal):
                    self._add_goal(goal, priority=3,
                                   tags=[self._TAG, 'from_inventory', 'weak_skill'])
                    self._mark_recent(goal)
                    added.append(goal)
                    self._log(f"[goal_gen/inventory] Слабый навык → цель: {goal[:80]}")

        # 2. Сильные стороны — использовать для реальных задач
        strong = [i for i in inv.get('proven', []) if i['proficiency'] > 0.7 and i['uses'] >= 5]
        if strong:
            top = strong[0]
            goal = (
                f"Используй свой лучший навык '{top['action']}' "
                f"(успешность {top['proficiency']:.0%}, {top['uses']} применений): "
                f"найди задачу где он максимально полезен и выполни её."
            )
            if not self._is_duplicate(goal):
                self._add_goal(goal, priority=2,
                               tags=[self._TAG, 'from_inventory', 'strong_skill'])
                self._mark_recent(goal)
                added.append(goal)

        # 3. Типы действий которые агент НИКОГДА не пробовал
        never = inv.get('never_tried', [])
        _DESCRIPTIONS = {
            'python':  'Напиши и выполни Python-скрипт для полезной задачи',
            'bash':    'Выполни полезную shell-команду для диагностики системы',
            'write':   'Создай файл с полезным содержимым (не в outputs/)',
            'github':  'Проверь GitHub репозиторий на наличие новых коммитов',
            'api':     'Выполни HTTP API запрос к публичному сервису',
            'browser': 'Открой веб-страницу и извлеки из неё данные',
            'docker':  'Проверь состояние Docker контейнеров',
            'db':      'Выполни SQL запрос к локальной базе данных',
        }
        # Предлагаем только 1 новый тип за раз, чтобы не перегружать очередь
        for action in never[:1]:
            desc = _DESCRIPTIONS.get(action)
            if not desc:
                continue
            goal = f"Попробуй новое действие '{action}': {desc}."
            if not self._is_duplicate(goal):
                self._add_goal(goal, priority=1,
                               tags=[self._TAG, 'from_inventory', 'never_tried'])
                self._mark_recent(goal)
                added.append(goal)
                self._log(f"[goal_gen/inventory] Не пробовал '{action}' → цель: {goal[:80]}")

        if added:
            self._log(
                f"[goal_gen/inventory] Добавлено {len(added)} целей из инвентаря. "
                f"Сводка: {inv.get('summary', '')[:60]}"
            )
        return added

    def propose_from_gap(self, gap_description: str) -> str | None:
        """
        Получает описание capability gap и ставит цель создать агента/инструмент.

        Вызывается из _improve когда CapabilityDiscovery нашёл gap.

        Returns:
            Описание добавленной цели или None.
        """
        if not gap_description or not gap_description.strip():
            return None

        # Формулируем цель создать нужный модуль
        goal_desc = self._gap_to_goal(gap_description)
        if not goal_desc:
            goal_desc = f"Создать агента или инструмент для: {gap_description[:200]}"

        if self._is_duplicate(goal_desc):
            return None

        self._add_goal(goal_desc, priority=3, tags=[self._TAG, 'capability_gap'])
        self._mark_recent(goal_desc)
        self._log(f"[goal_gen] Цель из capability gap: {goal_desc[:100]}")
        return goal_desc

    def propose_new_agent(
        self,
        name: str,
        description: str,
        cognitive_core=None,
        tools=None,
        immediate: bool = True,
    ) -> dict:
        """
        Немедленно строит нового агента (или ставит в очередь через GoalManager).

        Args:
            name        — snake_case имя агента
            description — что умеет
            cognitive_core — передаётся новому агенту
            tools          — передаётся новому агенту
            immediate   — если True: сразу вызываем AgentSpawner.build_and_register
                          если False: ставим цель в GoalManager

        Returns:
            {'ok': bool, 'name': str, 'error': str|None}
        """
        if immediate and self.agent_spawner:
            result = self.agent_spawner.build_and_register(
                name=name,
                description=description,
                cognitive_core=cognitive_core or self.cognitive_core,
                tools=tools,
            )
            if result['ok']:
                self._log(f"[goal_gen] ✓ Агент '{name}' создан немедленно.")
            else:
                # Не получилось — ставим в очередь
                goal_desc = f"Создать агента '{name}': {description[:200]}"
                self._add_goal(goal_desc, priority=3, tags=[self._TAG, 'build_agent'])
                self._log(f"[goal_gen] Агент '{name}' отправлен в очередь целей: {result['error']}")
            return result

        # Ставим цель
        goal_desc = f"Создать агента '{name}': {description[:200]}"
        if not self._is_duplicate(goal_desc):
            self._add_goal(goal_desc, priority=3, tags=[self._TAG, 'build_agent'])
            self._mark_recent(goal_desc)
        return {'ok': False, 'name': name, 'error': 'поставлено в очередь целей'}

    def propose_new_module(self, name: str, description: str, target_dir: str | None = None):
        """
        Строит произвольный Python-модуль через ModuleBuilder.

        Returns:
            ModuleBuildResult или None
        """
        if not self.module_builder:
            self._log("[goal_gen] propose_new_module: module_builder не подключён")
            return None
        result = self.module_builder.build_module(
            name=name,
            description=description,
            target_dir=target_dir,
        )
        if result.ok:
            self._log(f"[goal_gen] ✓ Модуль '{name}' создан: {result.file_path}")
        else:
            self._log(f"[goal_gen] ✗ Модуль '{name}' не создан: {result.error}")
        return result

    # ── Вспомогательные ───────────────────────────────────────────────────────

    def _insight_to_goal(self, insight_text: str) -> str | None:
        """
        Преобразует инсайт рефлексии в actionable-цель.
        Если cognitive_core доступен — просим LLM сформулировать.
        Иначе — простой шаблон.
        """
        # Сигналы что нужно что-то создать / улучшить
        build_signals = (
            'нет агента', 'нет инструмента', 'не хватает', 'отсутствует',
            'нужен', 'требуется', 'не умеет', 'не может', 'не справился',
            'no agent', 'missing', 'unable to', 'no tool',
        )
        insight_low = insight_text.lower()
        if not any(sig in insight_low for sig in build_signals):
            return None  # инсайт не требует создания нового компонента

        if self.cognitive_core:
            try:
                goal = self.cognitive_core.reasoning(
                    f"На основе следующего инсайта сформулируй ОДНУ конкретную цель "
                    f"для агента (максимум 120 символов, начни с глагола: Создать/Улучшить/Исправить):\n"
                    f"{insight_text[:400]}"
                )
                g = str(goal).strip()
                # Берём только первую строку
                g = g.split('\n')[0].strip()
                if 10 < len(g) < 250:
                    return g
            except Exception:
                pass

        # Fallback: берём первые 120 символов инсайта как цель
        short = insight_text.strip()[:120]
        return f"Улучшить: {short}" if short else None

    def _gap_to_goal(self, gap_description: str) -> str | None:
        """Формулирует цель из описания capability gap."""
        if not self.cognitive_core:
            return None
        try:
            goal = self.cognitive_core.reasoning(
                f"Какой один агент или модуль нужно создать чтобы закрыть следующий пробел? "
                f"Ответ: одна строка, начни с 'Создать' (max 150 символов):\n{gap_description[:300]}"
            )
            g = str(goal).strip().split('\n')[0].strip()
            if 5 < len(g) < 250:
                return g
        except Exception:
            pass
        return None

    def _add_goal(self, description: str, priority: int, tags: list[str]):
        """Добавляет цель в GoalManager после проверки governance и ethics."""
        if not self.goal_manager:
            return

        # ── Ethics gate: проверяем этичность цели ──
        if self.ethics:
            try:
                eth_eval = self.ethics.evaluate(
                    f"auto_goal: {description[:300]}",
                    context={'source': 'goal_generator', 'priority': priority},
                )
                if eth_eval.verdict.value == 'rejected':
                    self._log(
                        f"[goal_gen] Ethics отклонил цель: {description[:80]} — "
                        f"{'; '.join(eth_eval.reasons)}"
                    )
                    return
            except Exception:
                pass  # ethics недоступен — не блокируем

        # ── Governance gate: проверяем политику ──
        if self.governance:
            try:
                gov_result = self.governance.check(
                    f"add_goal: {description[:200]}",
                    context={'priority': priority, 'tags': tags},
                )
                if not gov_result.get('allowed', True):
                    self._log(
                        f"[goal_gen] Governance заблокировал цель: {description[:80]} — "
                        f"{gov_result.get('reason', '?')}"
                    )
                    return
            except Exception as gov_err:
                self._log(f"[goal_gen] Governance exception: {gov_err}")
                return

        try:
            from core.goal_manager import GoalPriority
            _pmap = {
                1: GoalPriority.CRITICAL,
                2: GoalPriority.MEDIUM,
                3: GoalPriority.HIGH,
            }
            prio = _pmap.get(priority, GoalPriority.MEDIUM)
            self.goal_manager.add_goal(
                description=description,
                priority=prio,
                tags=tags,
            )
        except Exception as e:
            self._log(f"[goal_gen] Ошибка добавления цели: {e}")

    def _is_duplicate(self, goal_desc: str) -> bool:
        """Проверяет не ставили ли мы уже такую цель недавно."""
        key = str(hash(goal_desc[:80]))
        last = self._recent_goals.get(key)
        if last and (time.time() - last) < self._cooldown_sec:
            return True
        # Дополнительно проверяем в GoalManager по тексту
        if self.goal_manager:
            try:
                active: list = []
                if hasattr(self.goal_manager, 'get_active_goals'):
                    maybe_active = self.goal_manager.get_active_goals()
                    if isinstance(maybe_active, list):
                        active = maybe_active
                    elif maybe_active:
                        active = [maybe_active]
                elif hasattr(self.goal_manager, 'get_all'):
                    # Совместимость с текущим GoalManager: фильтруем active вручную.
                    all_goals = self.goal_manager.get_all() or []
                    active = [
                        g for g in all_goals
                        if str((g or {}).get('status', '')).lower() == 'active'
                    ]

                for g in active:
                    existing = g.get('description', '') if isinstance(g, dict) else str(g)
                    if goal_desc[:60].lower() in existing.lower():
                        return True
            except Exception:
                pass
        return False

    def _mark_recent(self, goal_desc: str):
        key = str(hash(goal_desc[:80]))
        self._recent_goals[key] = time.time()
        # Чистим старые записи чтобы не росло бесконечно
        now = time.time()
        self._recent_goals = {
            k: v for k, v in self._recent_goals.items()
            if now - v < self._cooldown_sec * 2
        }

    def scan_working_dir(self, working_dir: str | None = None) -> list[str]:
        """
        Сканирует рабочую папку агента на наличие необработанных файлов.

        Если в корне появился .json/.txt/.csv файл (не системный), который ещё
        не записан в долгосрочную память — создаёт цель прочитать и выполнить его.

        Вызывается из _improve() каждые 5 циклов.

        Returns:
            Список добавленных целей.
        """
        import os as _os
        _wd = working_dir or _os.path.abspath('.')

        # Игнорируемые имена и расширения
        _SKIP_NAMES = {
            'requirements.txt', 'log.txt', 'task_log.txt', 'process_list.txt',
            '.env', 'credentials.json', 'dynamic_registry.json', 'agent_state.json',
            # Спецификации агента — грузятся при старте специально, не надо перетаскивать как задачу
            'архитектура автономного Агента.txt', 'Текстовый документ.txt',
        }
        _SKIP_PREFIXES = ('.', '__', 'agent_', 'run_')
        _SKIP_DIRS = {
            '.agent_memory', '.vector_store', '.venv', '__pycache__',
            'outputs', 'videos', 'downloaded_videos', 'monitor_output',
        }
        _TASK_EXTENSIONS = {'.json', '.txt', '.csv', '.md'}

        added: list[str] = []

        try:
            entries = _os.scandir(_wd)
        except OSError:
            return []

        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                name = entry.name
                ext  = _os.path.splitext(name)[1].lower()

                # Фильтры
                if ext not in _TASK_EXTENSIONS:
                    continue
                if name in _SKIP_NAMES:
                    continue
                if any(name.startswith(p) for p in _SKIP_PREFIXES):
                    continue

                # Файл интересный. Был ли он уже обработан?
                mem_key = f'workdir_file_seen:{name}'
                already_seen = False
                if self.goal_manager:
                    try:
                        active = self.goal_manager.get_active_goals() or []
                        for g in active:
                            desc = g.get('description', '') if isinstance(g, dict) else str(g)
                            if name in desc:
                                already_seen = True
                                break
                    except Exception:
                        pass

                # Проверяем в локальном кэше
                if not already_seen and hasattr(self, '_seen_workdir_files'):
                    already_seen = mem_key in self._seen_workdir_files

                if already_seen:
                    continue

                # Помечаем как замеченный
                if not hasattr(self, '_seen_workdir_files'):
                    self._seen_workdir_files = set()
                self._seen_workdir_files.add(mem_key)

                # Читаем первые 200 байт чтобы определить содержимое
                preview = ''
                try:
                    with open(entry.path, encoding='utf-8', errors='replace') as _f:
                        preview = _f.read(200).strip()
                except OSError:
                    pass

                # Формулируем цель
                goal_desc = (
                    f"В рабочей папке обнаружен файл '{name}'. "
                    f"Прочитай его полностью, определи что в нём содержится "
                    f"и выполни все задачи/инструкции которые там описаны. "
                    f"Начало файла: {preview[:150]}"
                )

                if self._is_duplicate(goal_desc):
                    continue

                # CRITICAL приоритет — файл положил сам пользователь
                self._add_goal(goal_desc, priority=1, tags=[self._TAG, 'workdir_file', name])
                self._mark_recent(goal_desc)
                added.append(goal_desc)
                self._log(
                    f"[goal_gen/scan] Новый файл в рабочей папке → цель: '{name}'"
                )

            except Exception:
                continue

        return added

    def _log(self, msg: str):
        if self.monitoring:
            try:
                self.monitoring.info(msg, source='goal_generator')
            except Exception:
                pass
        else:
            print(msg)
