# Tenant Isolation — мультитенантная изоляция
# Архитектура: если для других людей, обязательно:
#   - отдельная память на каждого
#   - отдельные цели
#   - отдельные ключи/API
#   - отдельные папки/workspace
#   - отдельные лимиты бюджета и прав
#
# Меморандум (Часть 16): "мы вместе решаем, какие данные остаются локально,
#   отдельно храним секреты, токены и ключи доступа"

from __future__ import annotations

import os
import json
import time
import threading
from dataclasses import dataclass, field


@dataclass
class TenantBudget:
    """Бюджетные лимиты тенанта."""
    max_tokens_per_day: int = 100_000
    max_api_calls_per_hour: int = 60
    max_actions_per_day: int = 500
    max_storage_mb: int = 1024         # 1 GB default
    tokens_used_today: int = 0
    api_calls_this_hour: int = 0
    actions_today: int = 0
    last_reset_day: str = ''
    last_reset_hour: float = 0.0

    def check_tokens(self, count: int) -> bool:
        self._maybe_reset()
        return self.tokens_used_today + count <= self.max_tokens_per_day

    def use_tokens(self, count: int):
        self._maybe_reset()
        self.tokens_used_today += count

    def check_api_call(self) -> bool:
        self._maybe_reset()
        return self.api_calls_this_hour < self.max_api_calls_per_hour

    def use_api_call(self):
        self._maybe_reset()
        self.api_calls_this_hour += 1

    def check_action(self) -> bool:
        self._maybe_reset()
        return self.actions_today < self.max_actions_per_day

    def use_action(self):
        self._maybe_reset()
        self.actions_today += 1

    def _maybe_reset(self):
        import datetime
        today = datetime.date.today().isoformat()
        if self.last_reset_day != today:
            self.tokens_used_today = 0
            self.actions_today = 0
            self.last_reset_day = today
        now = time.time()
        if now - self.last_reset_hour > 3600:
            self.api_calls_this_hour = 0
            self.last_reset_hour = now

    def usage_summary(self) -> dict:
        self._maybe_reset()
        return {
            'tokens': f"{self.tokens_used_today}/{self.max_tokens_per_day}",
            'api_calls': f"{self.api_calls_this_hour}/{self.max_api_calls_per_hour}/hr",
            'actions': f"{self.actions_today}/{self.max_actions_per_day}/day",
            'storage_limit_mb': self.max_storage_mb,
        }

    def to_dict(self) -> dict:
        return {
            'max_tokens_per_day': self.max_tokens_per_day,
            'max_api_calls_per_hour': self.max_api_calls_per_hour,
            'max_actions_per_day': self.max_actions_per_day,
            'max_storage_mb': self.max_storage_mb,
            'tokens_used_today': self.tokens_used_today,
            'api_calls_this_hour': self.api_calls_this_hour,
            'actions_today': self.actions_today,
            'last_reset_day': self.last_reset_day,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TenantBudget:
        b = cls()
        for k, v in d.items():
            if hasattr(b, k):
                setattr(b, k, v)
        return b


@dataclass
class TenantPermissions:
    """Права тенанта — что разрешено и запрещено."""
    can_execute_code: bool = True
    can_access_network: bool = True
    can_write_files: bool = True
    can_manage_secrets: bool = False    # по умолчанию нет доступа к чужим секретам
    can_deploy: bool = False
    can_delete_files: bool = False
    can_access_financial: bool = False
    allowed_tools: list[str] = field(default_factory=lambda: [
        'search', 'read_file', 'write_file', 'python', 'browser',
    ])
    denied_tools: list[str] = field(default_factory=list)

    def is_tool_allowed(self, tool_name: str) -> bool:
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            'can_execute_code': self.can_execute_code,
            'can_access_network': self.can_access_network,
            'can_write_files': self.can_write_files,
            'can_manage_secrets': self.can_manage_secrets,
            'can_deploy': self.can_deploy,
            'can_delete_files': self.can_delete_files,
            'can_access_financial': self.can_access_financial,
            'allowed_tools': self.allowed_tools,
            'denied_tools': self.denied_tools,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TenantPermissions:
        p = cls()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


class Tenant:
    """
    Один тенант — изолированная среда для пользователя.

    Изоляция:
        - собственная папка (workspace)
        - собственные API ключи
        - собственный бюджет
        - собственные права
        - metadata
    """

    def __init__(self, tenant_id: str, owner_user_id: str, name: str = ''):
        self.tenant_id: str = tenant_id
        self.owner_user_id: str = owner_user_id
        self.name: str = name or tenant_id
        self.created_at: float = time.time()
        self.is_active: bool = True

        self.budget = TenantBudget()
        self.permissions = TenantPermissions()

        # Собственные API-ключи тенанта (ТОЛЬКО имена; значения в SecretsProxy)
        self.api_keys: dict[str, str] = {}  # key_name → env_var_name

    def to_dict(self) -> dict:
        return {
            'tenant_id': self.tenant_id,
            'owner_user_id': self.owner_user_id,
            'name': self.name,
            'created_at': self.created_at,
            'is_active': self.is_active,
            'budget': self.budget.to_dict(),
            'permissions': self.permissions.to_dict(),
            'api_keys': self.api_keys,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Tenant:
        t = cls(
            tenant_id=d['tenant_id'],
            owner_user_id=d['owner_user_id'],
            name=d.get('name', ''),
        )
        t.created_at = d.get('created_at', time.time())
        t.is_active = d.get('is_active', True)
        t.budget = TenantBudget.from_dict(d.get('budget', {}))
        t.permissions = TenantPermissions.from_dict(d.get('permissions', {}))
        t.api_keys = d.get('api_keys', {})
        return t


# ══════════════════════════════════════════════════════════════════════════════
# Tenant Manager — управление мультитенантной средой
# ══════════════════════════════════════════════════════════════════════════════

class TenantManager:
    """
    Менеджер тенантов — изоляция между пользователями.

    Обеспечивает:
        1. Отдельная память на каждого (через UserMemoryStore)
        2. Отдельные цели (через UserProfile.goals)
        3. Отдельные ключи/API (через Tenant.api_keys)
        4. Отдельные папки/workspace (через filesystem isolation)
        5. Отдельные лимиты бюджета и прав (через TenantBudget + TenantPermissions)

    Принцип: данные тенанта A НИКОГДА не попадают к тенанту B.
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._tenants_dir = os.path.join(data_dir, 'tenants')
        self._tenants: dict[str, Tenant] = {}
        self._user_to_tenant: dict[str, str] = {}  # user_id → tenant_id
        self._lock = threading.Lock()
        os.makedirs(self._tenants_dir, exist_ok=True)
        self._load_all()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def create_tenant(self, tenant_id: str, owner_user_id: str,
                      name: str = '') -> Tenant:
        """Создаёт нового тенанта."""
        with self._lock:
            if tenant_id in self._tenants:
                return self._tenants[tenant_id]

            tenant = Tenant(tenant_id, owner_user_id, name)
            self._tenants[tenant_id] = tenant
            self._user_to_tenant[owner_user_id] = tenant_id

            # Создаём изолированную структуру папок
            tenant_dir = self._tenant_dir(tenant_id)
            for subdir in ['workspace', 'knowledge', 'logs', 'secrets']:
                os.makedirs(os.path.join(tenant_dir, subdir), exist_ok=True)

            self._save_tenant(tenant_id)
            return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def get_tenant_for_user(self, user_id: str) -> Tenant | None:
        """Возвращает тенант для пользователя (если есть)."""
        tid = self._user_to_tenant.get(user_id)
        if tid:
            return self._tenants.get(tid)
        return None

    def get_or_create_for_user(self, user_id: str, name: str = '') -> Tenant:
        """Получает или создаёт тенант для пользователя."""
        existing = self.get_tenant_for_user(user_id)
        if existing:
            return existing
        tenant_id = f"tenant_{user_id}"
        return self.create_tenant(tenant_id, user_id, name)

    # ── Isolation API ─────────────────────────────────────────────────────

    def get_workspace_dir(self, tenant_id: str) -> str:
        """Изолированная рабочая папка тенанта."""
        d = os.path.join(self._tenant_dir(tenant_id), 'workspace')
        os.makedirs(d, exist_ok=True)
        return d

    def get_knowledge_dir(self, tenant_id: str) -> str:
        d = os.path.join(self._tenant_dir(tenant_id), 'knowledge')
        os.makedirs(d, exist_ok=True)
        return d

    def get_log_dir(self, tenant_id: str) -> str:
        d = os.path.join(self._tenant_dir(tenant_id), 'logs')
        os.makedirs(d, exist_ok=True)
        return d

    # ── Budget checks ─────────────────────────────────────────────────────

    def check_budget(self, tenant_id: str, resource: str, amount: int = 1) -> bool:
        """Проверяет бюджетный лимит."""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return False
        if resource == 'tokens':
            return tenant.budget.check_tokens(amount)
        if resource == 'api_call':
            return tenant.budget.check_api_call()
        if resource == 'action':
            return tenant.budget.check_action()
        return True

    def use_budget(self, tenant_id: str, resource: str, amount: int = 1):
        """Расходует бюджет."""
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return
        if resource == 'tokens':
            tenant.budget.use_tokens(amount)
        elif resource == 'api_call':
            tenant.budget.use_api_call()
        elif resource == 'action':
            tenant.budget.use_action()
        self._save_tenant(tenant_id)

    # ── Permission checks ─────────────────────────────────────────────────

    def check_permission(self, tenant_id: str, action: str) -> bool:
        """Проверяет разрешение тенанта."""
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.is_active:
            return False

        perm = tenant.permissions
        checks = {
            'execute_code': perm.can_execute_code,
            'network': perm.can_access_network,
            'write_files': perm.can_write_files,
            'manage_secrets': perm.can_manage_secrets,
            'deploy': perm.can_deploy,
            'delete_files': perm.can_delete_files,
            'financial': perm.can_access_financial,
        }
        return checks.get(action, False)

    def is_tool_allowed(self, tenant_id: str, tool_name: str) -> bool:
        """Проверяет, разрешён ли инструмент для тенанта."""
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.is_active:
            return False
        return tenant.permissions.is_tool_allowed(tool_name)

    # ── Data isolation validation ─────────────────────────────────────────

    def validate_path_access(self, tenant_id: str, path: str) -> bool:
        """Проверяет, что путь принадлежит рабочей директории тенанта."""
        tenant_dir = self._tenant_dir(tenant_id)
        real_tenant = os.path.realpath(tenant_dir)
        real_path = os.path.realpath(path)
        return real_path.startswith(real_tenant)

    # ── Persistence ───────────────────────────────────────────────────────

    def _tenant_dir(self, tenant_id: str) -> str:
        safe_id = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(tenant_id))
        return os.path.join(self._tenants_dir, safe_id)

    def _config_path(self, tenant_id: str) -> str:
        return os.path.join(self._tenant_dir(tenant_id), 'tenant.json')

    def _save_tenant(self, tenant_id: str):
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            return
        path = self._config_path(tenant_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(tenant.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_all(self):
        if not os.path.isdir(self._tenants_dir):
            return
        for entry in os.listdir(self._tenants_dir):
            config_path = os.path.join(self._tenants_dir, entry, 'tenant.json')
            if os.path.isfile(config_path):
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    tenant = Tenant.from_dict(data)
                    self._tenants[tenant.tenant_id] = tenant
                    self._user_to_tenant[tenant.owner_user_id] = tenant.tenant_id
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    def save_all(self):
        with self._lock:
            for tid in self._tenants:
                self._save_tenant(tid)

    # ── Summary ───────────────────────────────────────────────────────────

    def list_tenants(self) -> list[dict]:
        return [
            {
                'tenant_id': t.tenant_id,
                'owner': t.owner_user_id,
                'name': t.name,
                'active': t.is_active,
                'budget': t.budget.usage_summary(),
            }
            for t in self._tenants.values()
        ]
