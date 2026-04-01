# Task Decomposition Engine (декомпозиция задач) — Слой 30
# Архитектура автономного AI-агента
# Разбивка сложных задач на подзадачи, управление зависимостями, граф задач.
# pylint: disable=broad-except


import uuid
from enum import Enum


class SubtaskStatus(Enum):
    PENDING    = 'pending'
    READY      = 'ready'       # зависимости выполнены
    RUNNING    = 'running'
    DONE       = 'done'
    FAILED     = 'failed'
    SKIPPED    = 'skipped'


class Subtask:
    """Одна подзадача в дереве декомпозиции."""

    def __init__(self, task_id: str, goal: str, role: str | None = None,
                 depends_on: list | None = None, priority: int = 2, metadata: dict | None = None):
        self.task_id = task_id
        self.goal = goal
        self.role = role
        self.depends_on: list[str] = depends_on or []
        self.priority = priority
        self.metadata = metadata or {}
        self.status = SubtaskStatus.PENDING
        self.result: object = None
        self.depth: int = 0        # уровень вложенности в дереве

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'goal': self.goal,
            'role': self.role,
            'depends_on': self.depends_on,
            'priority': self.priority,
            'status': self.status.value,
            'result': self.result,
            'depth': self.depth,
        }


class TaskGraph:
    """Граф зависимостей подзадач."""

    def __init__(self):
        self._nodes: dict[str, Subtask] = {}

    def add(self, task: Subtask):
        self._nodes[task.task_id] = task

    def get(self, task_id: str) -> Subtask | None:
        return self._nodes.get(task_id)

    def get_ready(self) -> list[Subtask]:
        """Возвращает задачи готовые к выполнению (все зависимости выполнены)."""
        ready = []
        for task in self._nodes.values():
            if task.status != SubtaskStatus.PENDING:
                continue
            deps_done = all(
                self._nodes.get(dep_id) and
                self._nodes[dep_id].status == SubtaskStatus.DONE
                for dep_id in task.depends_on
            )
            if deps_done:
                task.status = SubtaskStatus.READY
                ready.append(task)
        return sorted(ready, key=lambda t: -t.priority)

    def mark_done(self, task_id: str, result=None):
        task = self._nodes.get(task_id)
        if task:
            task.status = SubtaskStatus.DONE
            task.result = result

    def mark_failed(self, task_id: str, error: str | None = None):
        task = self._nodes.get(task_id)
        if task:
            task.status = SubtaskStatus.FAILED
            task.result = error

    def all_done(self) -> bool:
        return all(t.status in (SubtaskStatus.DONE, SubtaskStatus.SKIPPED, SubtaskStatus.FAILED)
                   for t in self._nodes.values())

    def to_list(self) -> list[dict]:
        return [t.to_dict() for t in self._nodes.values()]

    def expand_node(self, task_id: str, subtasks: list['Subtask']):
        """
        Рекурсивное расширение: помечает узел как SKIPPED и добавляет подузлы.

        Используется когда подзадача оказалась слишком сложной и сама требует
        декомпозиции. Подузлы не имеют зависимостей (готовы к выполнению сразу).
        """
        original = self._nodes.get(task_id)
        if original:
            original.status = SubtaskStatus.SKIPPED
        for sub in subtasks:
            sub.depth = (original.depth + 1) if original else 0
            self._nodes[sub.task_id] = sub

    def topological_order(self) -> list[Subtask]:
        """Топологическая сортировка задач (порядок выполнения без параллелизма)."""
        visited = set()
        order = []

        def visit(task_id: str):
            if task_id in visited:
                return
            visited.add(task_id)
            task = self._nodes.get(task_id)
            if not task:
                return
            for dep in task.depends_on:
                visit(dep)
            order.append(task)

        for tid in self._nodes:
            visit(tid)
        return order


class TaskDecompositionEngine:
    """
    Task Decomposition Engine — Слой 30.

    Функции:
        - разбивка сложной задачи на подзадачи через Cognitive Core
        - построение графа зависимостей между подзадачами
        - определение порядка выполнения (топологическая сортировка)
        - управление выполнением дерева задач
        - рекурсивная декомпозиция (подзадача → подподзадачи)

    Используется:
        - Cognitive Core (Слой 3)      — генерация плана декомпозиции
        - Orchestration (Слой 18)      — исполнение декомпозированных задач
        - Agent System (Слой 4)        — делегирование подзадач агентам
        - Autonomous Loop (Слой 20)    — декомпозиция в фазе plan
    """

    def __init__(self, cognitive_core=None, agent_system=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.agent_system = agent_system
        self.monitoring = monitoring

    # ── Декомпозиция ──────────────────────────────────────────────────────────

    def decompose(self, goal: str, context: dict | None = None,
                  max_subtasks: int = 8) -> TaskGraph:
        """
        Разбивает цель на подзадачи и возвращает граф.

        Args:
            goal        — высокоуровневая цель для декомпозиции
            context     — дополнительный контекст
            max_subtasks — ограничение на количество подзадач

        Returns:
            TaskGraph с подзадачами и зависимостями.
        """
        self._log(f"Декомпозиция: '{goal[:60]}'")
        graph = TaskGraph()

        if not self.cognitive_core:
            return self._decompose_deterministic(goal, max_subtasks)


        raw = self.cognitive_core.plan(
            f"Декомпозируй задачу на {max_subtasks} или меньше подзадач.\n"
            f"Задача: {goal}\n"
            f"Контекст: {context or ''}\n\n"
            f"Для каждой подзадачи укажи:\n"
            f"- Описание (что нужно сделать)\n"
            f"- Роль агента: research / coding / analysis / planning / debugging / communication\n"
            f"- Зависит от (номера предыдущих подзадач, если есть)\n"
            f"- Приоритет: 1 (низкий), 2 (средний), 3 (высокий)\n\n"
            f"Формат: нумерованный список"
        )

        subtasks = self._parse_subtasks(str(raw))
        id_map: dict[int, str] = {}   # номер → task_id

        for i, s_data in enumerate(subtasks[:max_subtasks], start=1):
            task_id = str(uuid.uuid4())[:6]
            id_map[i] = task_id
            deps = [id_map[d] for d in s_data.get('depends_on', []) if d in id_map]
            task = Subtask(
                task_id=task_id,
                goal=s_data.get('goal', f"Подзадача {i}"),
                role=s_data.get('role'),
                depends_on=deps,
                priority=s_data.get('priority', 2),
            )
            _dep_nodes: list[Subtask] = [n for d in deps for n in (graph.get(d),) if n is not None]
            task.depth = max((n.depth + 1 for n in _dep_nodes), default=0)
            graph.add(task)

        self._log(f"Декомпозиция завершена: {len(subtasks)} подзадач")
        return graph

    def decompose_recursive(self, goal: str, depth: int = 2,
                            context: dict | None = None) -> TaskGraph:
        """
        Рекурсивная декомпозиция: каждую подзадачу разбивает ещё раз до depth уровней.
        """
        root_graph = self.decompose(goal, context=context)

        if depth <= 1:
            return root_graph

        all_tasks = root_graph.to_list()
        for t_dict in all_tasks:
            if t_dict['depth'] < depth - 1:
                sub_graph = self.decompose(t_dict['goal'], max_subtasks=4)
                for sub_task_dict in sub_graph.to_list():
                    task = Subtask(
                        task_id=sub_task_dict['task_id'],
                        goal=sub_task_dict['goal'],
                        role=sub_task_dict.get('role'),
                        depends_on=[t_dict['task_id']],
                        priority=sub_task_dict.get('priority', 2),
                    )
                    task.depth = t_dict['depth'] + 1
                    root_graph.add(task)

        return root_graph

    # ── Выполнение графа ──────────────────────────────────────────────────────

    def execute_graph(self, graph: TaskGraph) -> list[dict]:
        """
        Последовательно выполняет подзадачи через Agent System
        в топологическом порядке.

        Returns:
            Список результатов подзадач.
        """
        results = []
        ordered = graph.topological_order()

        for task in ordered:
            if task.status == SubtaskStatus.FAILED:
                self._log(f"Пропуск [{task.task_id}] из-за ошибки зависимости")
                continue

            self._log(f"Выполнение [{task.task_id}]: '{task.goal[:60]}'")
            task.status = SubtaskStatus.RUNNING

            try:
                if self.agent_system:
                    result = self.agent_system.handle({
                        'goal': task.goal,
                        'role': task.role,
                        **task.metadata,
                    })
                    task.result = result
                    task.status = SubtaskStatus.DONE
                else:
                    task.result = {'warning': 'agent_system не подключён'}
                    task.status = SubtaskStatus.DONE
            except Exception as e:
                graph.mark_failed(task.task_id, str(e))
                self._log(f"Ошибка [{task.task_id}]: {e}")

            results.append(task.to_dict())

        self._log(f"Граф выполнен: {sum(1 for t in results if t['status'] == 'done')}"
                  f"/{len(results)} задач завершено")
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    # Шаблоны декомпозиции по типу задачи (детерминированные, без LLM)
    _TEMPLATES = {
        'research': [
            ('Сформулировать поисковый запрос по теме: {goal}', 'planning',   3, []),
            ('Найти и собрать источники информации',             'research',   3, [1]),
            ('Проанализировать и систематизировать данные',      'analysis',   2, [2]),
            ('Составить итоговый отчёт',                        'communication', 2, [3]),
        ],
        'code': [
            ('Определить требования и интерфейс для: {goal}',   'planning',   3, []),
            ('Разработать структуру и архитектуру',             'planning',   3, [1]),
            ('Реализовать основную логику',                     'coding',     3, [2]),
            ('Написать тесты и проверить корректность',         'debugging',  2, [3]),
        ],
        'debug': [
            ('Воспроизвести и описать проблему: {goal}',        'analysis',   3, []),
            ('Найти корневую причину ошибки',                   'debugging',  3, [1]),
            ('Разработать и применить исправление',             'coding',     3, [2]),
            ('Верифицировать что исправление работает',         'debugging',  2, [3]),
        ],
        'analyze': [
            ('Определить параметры и критерии анализа',         'planning',   3, []),
            ('Собрать данные для анализа',                      'research',   2, [1]),
            ('Применить аналитические методы к данным',         'analysis',   3, [2]),
            ('Сформулировать выводы и рекомендации',            'analysis',   2, [3]),
        ],
        'write': [
            ('Собрать информацию по теме: {goal}',              'research',   2, []),
            ('Составить план и структуру материала',            'planning',   2, [1]),
            ('Написать черновик',                               'communication', 3, [2]),
            ('Отредактировать и доработать текст',              'communication', 2, [3]),
        ],
        'deploy': [
            ('Проверить готовность и зависимости',              'analysis',   3, []),
            ('Подготовить конфигурацию для деплоя',             'coding',     3, [1]),
            ('Выполнить деплой в целевую среду',                'coding',     3, [2]),
            ('Верифицировать работоспособность после деплоя',   'debugging',  3, [3]),
        ],
        'default': [
            ('Понять и уточнить требования задачи: {goal}',    'planning',   3, []),
            ('Спланировать подход к решению',                   'planning',   2, [1]),
            ('Выполнить основную часть задачи',                 'research',   3, [2]),
            ('Проверить и оценить результат',                   'analysis',   2, [3]),
        ],
    }

    # Ключевые слова для определения типа задачи
    _TASK_KEYWORDS = {
        'research': ['найди', 'исследуй', 'изучи', 'search', 'find', 'research', 'look up'],
        'code':     ['напиши', 'создай', 'реализуй', 'build', 'create', 'implement', 'code', 'develop'],
        'debug':    ['исправь', 'отладь', 'debug', 'fix', 'repair', 'resolve', 'ошибка', 'баг'],
        'analyze':  ['проанализируй', 'оцени', 'analyze', 'evaluate', 'assess', 'measure'],
        'write':    ['напиши текст', 'составь', 'write', 'generate text', 'document'],
        'deploy':   ['задеплой', 'разверни', 'deploy', 'release', 'publish', 'запусти'],
    }

    def _decompose_deterministic(self, goal: str, max_subtasks: int = 8) -> TaskGraph:
        """
        Шаблонная декомпозиция по типу задачи — работает без LLM.
        Определяет тип задачи по ключевым словам и применяет соответствующий шаблон.
        """
        goal_l = goal.lower()

        # Определяем тип задачи по ключевым словам
        task_type = 'default'
        for t_type, keywords in self._TASK_KEYWORDS.items():
            if any(kw in goal_l for kw in keywords):
                task_type = t_type
                break

        template = self._TEMPLATES[task_type]
        self._log(f"Шаблон декомпозиции: '{task_type}' ({len(template)} подзадач)")

        graph = TaskGraph()
        id_map: dict[int, str] = {}   # номер в шаблоне → task_id

        for i, (step_goal, role, priority, dep_nums) in enumerate(template[:max_subtasks], start=1):
            task_id = str(uuid.uuid4())[:6]
            id_map[i] = task_id
            deps = [id_map[d] for d in dep_nums if d in id_map]

            # Подставляем {goal} в описание первой подзадачи
            if '{goal}' in step_goal:
                step_goal = step_goal.replace('{goal}', goal[:60])

            task = Subtask(
                task_id=task_id,
                goal=step_goal,
                role=role,
                depends_on=deps,
                priority=priority,
                metadata={'parent_goal': goal[:100], 'template': task_type},
            )
            task.depth = len(deps)
            graph.add(task)

        return graph

    def _parse_subtasks(self, raw: str) -> list[dict]:
        """Парсит список подзадач из ответа LLM."""
        import re
        subtasks = []
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        current = {}

        for line in lines:
            # Новая подзадача начинается с цифры
            if re.match(r'^\d+[.)]\s+', line):
                if current:
                    subtasks.append(current)
                goal = re.sub(r'^\d+[.)]\s+', '', line).strip()
                current = {'goal': goal, 'role': None, 'depends_on': [], 'priority': 2}
            elif current:
                line_lower = line.lower()
                # Роль
                for role in ('research', 'coding', 'analysis', 'planning',
                             'debugging', 'communication', 'learning'):
                    if role in line_lower:
                        current['role'] = role
                        break
                # Приоритет
                if 'приоритет' in line_lower or 'priority' in line_lower:
                    for p in ('3', '1', '2'):
                        if p in line:
                            current['priority'] = int(p)
                            break
                # Зависимости
                if 'зависит' in line_lower or 'depends' in line_lower:
                    nums = re.findall(r'\d+', line)
                    current['depends_on'] = [int(n) for n in nums]

        if current:
            subtasks.append(current)
        return subtasks

    def needs_decomposition(self, goal: str) -> bool:
        """
        Определяет, нужна ли декомпозиция задачи.

        Возвращает True только если задача явно многошаговая или слишком абстрактная
        для прямого исполнения. Атомарные задачи (одно действие, уже содержат код,
        короткие и конкретные) возвращают False — не трогаем.

        Логика: НЕ декомпозировать → проверяем атомарность.
                Остальное → ищем сигналы сложности.
        """
        import re as _re

        if not goal:
            return False

        # ── Признаки атомарности (сразу False) ───────────────────────────────
        # 1. Уже содержит исполняемый код
        if _re.search(r'```(?:python|bash|shell)', goal):
            return False
        if _re.search(r'^(?:SEARCH|READ|WRITE):', goal, _re.MULTILINE):
            return False
        # 2. Прямой вызов tool_layer
        if 'tool_layer.use(' in goal:
            return False
        # 3. Очень короткие задачи (одно конкретное действие)
        if len(goal) < 40:
            return False

        goal_l = goal.lower()

        # 4. Одноактные глагольные команды с конкретным объектом (без союзов)
        _ATOMIC_VERBS = (
            'создай файл', 'запиши в файл', 'прочитай файл', 'удали файл',
            'сделай скриншот', 'отправь уведомление', 'пингуй', 'проверь порт',
            'переведи текст', 'зашифруй', 'расшифруй', 'создай архив',
            'create file', 'read file', 'write file', 'ping', 'take screenshot',
        )
        # ── Сигналы сложности (True если набирается достаточно) ──────────────
        score = 0

        if any(v in goal_l for v in _ATOMIC_VERBS) and len(goal) < 180:
            # Убеждаемся что нет второго действия через "и [глагол]" или союза
            _has_second_action = _re.search(
                r'\b(и потом|затем|после|а также|и ещё|then|after|also'
                r'|и сохрани|и запиши|и отправь|и напиши|и создай|и добавь'
                r'|и рассчитай|и посчитай|и выполни|и запусти)\b',
                goal_l
            )
            if not _has_second_action:
                return False
            # Нашли второе действие — задача точно многошаговая
            score += 3

        # Нумерованный список шагов внутри задачи
        if _re.search(r'(?:^|\n)\s*\d+[.)]\s+\w', goal):
            score += 3

        # Явные слова-последовательности
        if _re.search(
            r'\b(сначала|во.первых|первым делом|firstly|first[,:]|step \d|шаг \d)\b',
            goal_l
        ):
            score += 2

        # Союзы последовательности
        if _re.search(
            r'\b(затем|после этого|и потом|и затем|а затем|then|after that|afterwards)\b',
            goal_l
        ):
            score += 2

        # Несколько разных глаголов-действий через "и"
        _multi_action = _re.findall(
            r'\b(создай|напиши|проверь|запусти|сохрани|отправь|скачай|загрузи'
            r'|определи|найди|узнай|посчитай|вычисли|получи|преобразуй'
            r'|create|write|check|run|save|send|download|upload'
            r'|find|calculate|convert|get|fetch|determine)\b',
            goal_l
        )
        # 2 разных глагола — уже признак многошаговости (найди И посчитай, и т.д.)
        if len(_multi_action) >= 2:
            score += len(_multi_action)
        elif len(_multi_action) == 1 and len(goal) > 80:
            # Один глагол, но длинная задача — слабый сигнал
            score += 1

        # Паттерн "fetch + compute": определи/получи X и посчитай/конвертируй Y
        if (_re.search(r'\b(определи|узнай|получи|найди|fetch|get|find)\b', goal_l)
                and _re.search(
                    r'\b(посчитай|вычисли|рассчитай|конвертируй|переведи в'
                    r'|calculate|convert|compute)\b', goal_l)):
            score += 2

        # "Все N инструментов", "каждый из", "all tools"
        if _re.search(
            r'\b(все|каждый|каждую|каждое|all|each|every)\b.{0,30}\b(\d+|\w+ов|\w+ей)\b',
            goal_l
        ):
            score += 2

        # Явная многозадачность по смыслу
        _MULTISTEP_KW = (
            'протестируй все', 'проверь все', 'test all', 'check all',
            'список задач', 'task list', 'несколько шагов', 'multiple steps',
            'поочерёдно', 'one by one', 'последовательно',
        )
        if any(kw in goal_l for kw in _MULTISTEP_KW):
            score += 3

        # Длинный текст сам по себе добавляет +1 (неопределённость → осторожнее)
        if len(goal) > 300:
            score += 1
        if len(goal) > 600:
            score += 1

        return score >= 3

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='task_decomposition')
        else:
            print(f"[TaskDecomposition] {message}")
