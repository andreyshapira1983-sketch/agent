# Agent Spawner — динамическое создание и регистрация агентов
# Архитектура автономного AI-агента — надстройка над ModuleBuilder (Слой 4 + 12)
#
# Позволяет агенту:
#   1. Создать нового специализированного агента через LLM (build_and_register)
#   2. Зарегистрировать уже существующий класс в ManagerAgent (register_instance)
#   3. При рестарте восстановить всех динамически созданных агентов (restore_from_registry)
#
# pylint: disable=broad-except

from __future__ import annotations

import time

# ── Безопасность: лимиты на порождение агентов ────────────────────────────
MAX_SPAWNED_AGENTS = 15          # жёсткий потолок одновременно живых агентов
MAX_SPAWN_RATE_PER_MIN = 5       # макс. spawn-запросов за 60 сек


class AgentSpawner:
    """
    Agent Spawner — динамически создаёт, регистрирует и восстанавливает агентов.

    Связан с:
        ModuleBuilder  — генерирует .py файл нового агента
        agent_system   — ManagerAgent, куда регистрируются агенты
        DynamicRegistry (через ModuleBuilder) — персистентный реестр

    Методы:
        build_and_register(name, description)  — создать агента и сразу подключить
        register_instance(agent_instance)       — зарегистрировать готовый объект
        spawn_from_entry(entry, ...)            — восстановить из записи реестра
        restore_from_registry(...)              — при старте подгрузить всех
        list_dynamic()                          — список всех динамических агентов
    """

    def __init__(
        self,
        module_builder=None,
        agent_system=None,       # ManagerAgent
        monitoring=None,
        governance=None,
    ):
        self.module_builder = module_builder
        self.agent_system   = agent_system    # ManagerAgent
        self.monitoring     = monitoring
        self.governance     = governance

        # Динамические агенты этой сессии
        # {name: {'instance': ..., 'class_name': ..., 'file_path': ..., 'description': ...}}
        self._spawned: dict[str, dict] = {}
        self._spawn_timestamps: list[float] = []   # rate-limiter

    # ── Основной API ──────────────────────────────────────────────────────────

    def build_and_register(
        self,
        name: str,
        description: str,
        cognitive_core=None,
        tools=None,
    ) -> dict:
        """
        Строит нового агента через ModuleBuilder + регистрирует в ManagerAgent.

        Args:
            name        — snake_case имя, напр. 'finance_analyst'
            description — что умеет агент, какие задачи решает
            cognitive_core — Cognitive Core для нового агента
            tools          — инструменты для нового агента

        Returns:
            {'ok': bool, 'name': str, 'error': str|None}
        """
        if not self.module_builder:
            return {'ok': False, 'name': name, 'error': 'module_builder не подключён'}

        # ── Лимит количества агентов ──
        if len(self._spawned) >= MAX_SPAWNED_AGENTS:
            self._log(f"[spawner] Превышен лимит агентов ({MAX_SPAWNED_AGENTS}). "
                       f"Отклоняю '{name}'.")
            return {'ok': False, 'name': name,
                    'error': f'Лимит агентов ({MAX_SPAWNED_AGENTS}) достигнут'}

        # ── Rate-limit ──
        now = time.time()
        self._spawn_timestamps = [t for t in self._spawn_timestamps if now - t < 60]
        if len(self._spawn_timestamps) >= MAX_SPAWN_RATE_PER_MIN:
            self._log(f"[spawner] Rate-limit: {MAX_SPAWN_RATE_PER_MIN}/мин. Отклоняю '{name}'.")
            return {'ok': False, 'name': name,
                    'error': f'Rate-limit: макс. {MAX_SPAWN_RATE_PER_MIN} агентов/мин'}
        self._spawn_timestamps.append(now)

        # ── Governance gate: проверяем допустимость создания агента ──
        if self.governance:
            try:
                gov_result = self.governance.check(
                    f"spawn_agent: {name}: {description[:200]}",
                    context={'agent_name': name},
                )
                if not gov_result.get('allowed', True):
                    self._log(f"[spawner] Governance заблокировал создание агента '{name}': "
                              f"{gov_result.get('reason', '?')}")
                    return {'ok': False, 'name': name,
                            'error': f"Governance: {gov_result.get('reason', 'запрещено политикой')}"}
            except Exception as gov_err:
                self._log(f"[spawner] Governance exception при создании '{name}': {gov_err}")
                return {'ok': False, 'name': name, 'error': f'Governance отклонил: {gov_err}'}

        self._log(f"[spawner] Создаю агента: '{name}'")
        result = self.module_builder.build_agent(name, description)

        if not result.ok:
            self._log(f"[spawner] Не удалось создать агента '{name}': {result.error}")
            return {'ok': False, 'name': name, 'error': result.error}

        # Создаём экземпляр
        instance = self._instantiate(
            mod=result.module_object,
            class_name=result.class_name,
            cognitive_core=cognitive_core,
            tools=tools,
        )
        if instance is None:
            return {
                'ok': False,
                'name': name,
                'error': f"Не удалось создать экземпляр класса {result.class_name}",
            }

        # Регистрируем в ManagerAgent
        self._register_in_manager(instance)

        self._spawned[name] = {
            'instance':   instance,
            'class_name': result.class_name,
            'file_path':  result.file_path,
            'description': description,
            'created_at': time.time(),
        }
        self._log(f"[spawner] ✓ Агент '{name}' создан и зарегистрирован.")
        return {'ok': True, 'name': name, 'error': None}

    def register_instance(self, agent_instance) -> bool:
        """
        Регистрирует уже готовый объект агента в ManagerAgent.
        Используется когда агент создан вручную или через другой путь.
        """
        name = getattr(agent_instance, 'name', None)
        if not name:
            self._log("[spawner] register_instance: агент без имени (нет .name)")
            return False
        self._register_in_manager(agent_instance)
        self._spawned[name] = {
            'instance': agent_instance,
            'class_name': type(agent_instance).__name__,
            'file_path': None,
            'description': '',
            'created_at': time.time(),
        }
        self._log(f"[spawner] Зарегистрирован существующий агент: '{name}'")
        return True

    def restore_from_registry(
        self,
        cognitive_core=None,
        tools=None,
    ):
        """
        При старте agent.py загружает всех ранее созданных агентов из
        dynamic_registry.json и регистрирует их в ManagerAgent.

        Вызывается один раз в build_agent_components() сразу после создания AgentSpawner.
        """
        if not self.module_builder:
            return

        loaded = self.module_builder.load_all_from_registry()
        agents_loaded = loaded.get('agents', [])
        count = 0
        for entry in agents_loaded:
            mod        = entry.get('module')
            cls_name   = entry.get('class_name', '')
            agent_name = entry.get('name', '')
            if not mod or not cls_name:
                continue
            instance = self._instantiate(
                mod=mod,
                class_name=cls_name,
                cognitive_core=cognitive_core,
                tools=tools,
            )
            if instance:
                self._register_in_manager(instance)
                self._spawned[agent_name] = {
                    'instance':   instance,
                    'class_name': cls_name,
                    'file_path':  entry.get('file_path'),
                    'description': '',
                    'created_at': time.time(),
                }
                count += 1
        if count:
            self._log(f"[spawner] Восстановлено {count} динамических агентов из реестра.")

    def spawn_from_entry(
        self,
        entry: dict,
        cognitive_core=None,
        tools=None,
    ):
        """
        Загружает агента из словаря реестра (для ручного управления).
        {'name': ..., 'class_name': ..., 'file_path': ...}
        """
        if not self.module_builder:
            return None
        file_path = entry.get('file_path', '')
        cls_name  = entry.get('class_name', '')
        name      = entry.get('name', '')
        if not file_path or not cls_name:
            return None
        _import_file = getattr(self.module_builder, '_import_file', None)
        if not callable(_import_file):
            self._log(f"[spawner] module_builder не имеет метода _import_file — пропускаем '{name}'")
            return None
        mod = getattr(self.module_builder, '_import_file')(file_path, name)
        if not mod:
            return None
        return self._instantiate(mod, cls_name, cognitive_core, tools)

    # ── Справка ───────────────────────────────────────────────────────────────

    def list_dynamic(self) -> list[dict]:
        """Список всех динамически созданных/восстановленных агентов сессии."""
        return [
            {
                'name':       name,
                'class_name': info['class_name'],
                'file_path':  info.get('file_path'),
                'created_at': info.get('created_at'),
            }
            for name, info in self._spawned.items()
        ]

    def get(self, name: str):
        """Возвращает экземпляр динамического агента по имени."""
        entry = self._spawned.get(name)
        return entry['instance'] if entry else None

    # ── Вспомогательные ───────────────────────────────────────────────────────

    def _instantiate(self, mod, class_name: str, cognitive_core, tools):
        """Создаёт экземпляр класса из модуля.

        SECURITY: sub-agent получает ограниченный набор инструментов —
        без прямого доступа к секретам и без сетевых tool-обёрток.
        """
        cls = getattr(mod, class_name, None)
        if cls is None:
            self._log(f"[spawner] Класс '{class_name}' не найден в модуле.")
            return None
        safe_tools = self._sanitize_tools_for_subagent(tools)
        try:
            return cls(cognitive_core=cognitive_core, tools=safe_tools)
        except TypeError:
            try:
                return cls(cognitive_core=cognitive_core)
            except TypeError:
                try:
                    return cls()
                except Exception as e:
                    self._log(f"[spawner] Не удалось создать экземпляр {class_name}: {e}")
                    return None

    # ── SECURITY: ограничение tools для sub-agents ────────────────────────

    # Инструменты, которые sub-agent НЕ получает (содержат секреты или сетевой доступ)
    _BLOCKED_TOOL_KEYS = frozenset({
        'email', 'telegram', 'upwork', 'figma', 'twilio', 'vonage',
        'reddit', 'github_api', 'google_calendar', 'sms',
    })

    def _sanitize_tools_for_subagent(self, tools):
        """Убирает из tools-dict ключи с сетевыми/секретными обёртками."""
        if tools is None:
            return None
        if isinstance(tools, dict):
            return {k: v for k, v in tools.items()
                    if k not in self._BLOCKED_TOOL_KEYS}
        # Если tools — объект с .use(), оборачиваем в прокси
        if hasattr(tools, 'use'):
            return _ToolProxy(tools, self._BLOCKED_TOOL_KEYS)
        return tools

    def _register_in_manager(self, instance):
        """Регистрирует агента в ManagerAgent если он подключён."""
        if self.agent_system is None:
            return
        # agent_system может быть AgentSystem или ManagerAgent напрямую
        manager = None
        if hasattr(self.agent_system, 'register'):
            # Это ManagerAgent
            manager = self.agent_system
        elif hasattr(self.agent_system, 'handle'):
            # Это AgentSystem с manager внутри
            manager = getattr(self.agent_system, '_manager', None)
        if manager and hasattr(manager, 'register'):
            try:
                manager.register(instance)
            except Exception as e:
                self._log(f"[spawner] Ошибка регистрации: {e}")

    def _log(self, msg: str):
        if self.monitoring:
            try:
                self.monitoring.info(msg, source='agent_spawner')
            except Exception:
                pass
        else:
            print(msg)


class _ToolProxy:
    """Прокси-обёртка: блокирует sub-agent доступ к инструментам с секретами."""

    def __init__(self, inner, blocked_keys: frozenset):
        self._inner = inner
        self._blocked = blocked_keys

    def use(self, tool_name: str, **kwargs):
        if tool_name in self._blocked:
            return {'error': f"sub-agent не имеет доступа к '{tool_name}'",
                    'success': False}
        return self._inner.use(tool_name, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)
