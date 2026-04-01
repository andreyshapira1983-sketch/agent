# Resource & Budget Control Layer (контроль ресурсов) — Слой 26
# Архитектура автономного AI-агента
# Лимиты по токенам и бюджету, latency, вычислительные затраты,
# приоритизация задач, деградация при превышении лимитов.


import json
import os
import time
from enum import Enum


class BudgetStatus(Enum):
    OK        = 'ok'          # в норме
    WARNING   = 'warning'     # использовано > 80%
    EXCEEDED  = 'exceeded'    # лимит превышен
    STOPPED   = 'stopped'     # система остановлена


class ResourceType(Enum):
    TOKENS     = 'tokens'
    MONEY      = 'money'          # в USD или рублях
    REQUESTS   = 'requests'       # кол-во вызовов API
    COMPUTE    = 'compute'        # CPU/GPU время (сек)
    MEMORY     = 'memory'         # RAM (MB)


class BudgetControl:
    """
    Resource & Budget Control Layer — Слой 26.

    Функции:
        - лимиты по токенам, деньгам, кол-ву запросов, вычислениям
        - отслеживание текущего расхода в реальном времени
        - предупреждения при приближении к лимиту (> 80%)
        - остановка/деградация при превышении
        - приоритизация задач по соотношению ценность/стоимость
        - детальный отчёт о расходах

    Используется:
        - Cognitive Core (Слой 3)     — контроль токенов LLM
        - Execution System (Слой 8)   — контроль вычислительных ресурсов
        - Orchestration (Слой 18)     — приоритизация задач по бюджету
        - Autonomous Loop (Слой 20)   — проверка перед каждым шагом цикла
        - Monitoring (Слой 17)        — репортинг метрик
    """

    def __init__(self, monitoring=None, human_approval=None,
                 auto_stop: bool = True,
                 persist_path: str | None = None):
        self.monitoring    = monitoring
        self.human_approval = human_approval
        self.auto_stop     = auto_stop   # автостоп при превышении лимита
        # Путь к файлу для сохранения накопленных расходов между перезапусками
        self._persist_path = persist_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', '.agent_memory', 'budget_spent.json'
        )

        self._limits: dict[ResourceType, float] = {}     # ресурс → лимит
        self._spent: dict[ResourceType, float] = {}      # ресурс → потрачено
        self._warn_threshold: float = 0.8                # 80% → предупреждение
        self._status: BudgetStatus = BudgetStatus.OK
        self._transactions: list[dict] = []              # история расходов
        self._stopped: bool = False
        self._exceeded_resources: list[str] = []         # какие ресурсы превышены
        self._periodic_reset_enabled: bool = True
        self._periodic_reset_sec: int = 24 * 60 * 60
        self._last_period_reset_ts: float = time.time()

        # Загружаем накопленные расходы с прошлых сессий
        self._load_spent()

    # ── Настройка лимитов ─────────────────────────────────────────────────────

    def set_limit(self, resource: ResourceType, limit: float):
        """Устанавливает лимит для ресурса."""
        self._limits[resource] = limit
        if resource not in self._spent:
            self._spent[resource] = 0.0
        self._log(f"Лимит установлен: {resource.value} = {limit}", level='info')

    def set_token_limit(self, max_tokens: int):
        self.set_limit(ResourceType.TOKENS, max_tokens)

    def set_money_limit(self, amount: float):
        self.set_limit(ResourceType.MONEY, amount)

    def set_request_limit(self, max_requests: int):
        self.set_limit(ResourceType.REQUESTS, max_requests)

    # ── Учёт расходов ─────────────────────────────────────────────────────────

    def spend(self, resource: ResourceType, amount: float,
              description: str | None = None) -> BudgetStatus:
        """
        Фиксирует расход ресурса.

        Returns:
            Текущий BudgetStatus после расхода.
        """
        if resource not in self._spent:
            self._spent[resource] = 0.0
        self._spent[resource] += amount

        tx = {
            'timestamp': time.time(),
            'resource': resource.value,
            'amount': amount,
            'total': self._spent[resource],
            'description': description,
        }
        self._transactions.append(tx)

        if self.monitoring:
            self.monitoring.record_metric(f"budget.{resource.value}.spent", self._spent[resource])

        status = self._check_status(resource)
        return status

    def spend_tokens(self, prompt: int, completion: int, model: str | None = None) -> BudgetStatus:
        """Удобный метод: учёт токенов LLM-вызова."""
        total = prompt + completion
        desc = f"LLM вызов ({model or 'unknown'}): {prompt}+{completion}"
        return self.spend(ResourceType.TOKENS, total, description=desc)

    def spend_money(self, amount: float, description: str | None = None) -> BudgetStatus:
        status = self.spend(ResourceType.MONEY, amount, description=description)
        self._save_spent()   # сохраняем денежный расход немедленно
        return status

    def spend_request(self, description: str | None = None) -> BudgetStatus:
        return self.spend(ResourceType.REQUESTS, 1, description=description)

    # ── Проверки перед действием ──────────────────────────────────────────────

    def check(self, resource: ResourceType, expected_cost: float = 0) -> bool:
        """
        Проверяет, можно ли потратить ещё expected_cost единиц ресурса.
        Возвращает True если OK, False если лимит будет превышен.
        """
        if self._stopped:
            return False
        limit = self._limits.get(resource)
        if limit is None:
            return True   # лимит не установлен — разрешено
        spent = self._spent.get(resource, 0)
        return (spent + expected_cost) <= limit

    def require_budget(self, resource: ResourceType, expected_cost: float = 0):
        """Бросает исключение если бюджет превышен."""
        if not self.check(resource, expected_cost):
            limit = self._limits.get(resource, '∞')
            spent = self._spent.get(resource, 0)
            raise RuntimeError(
                f"Бюджет {resource.value} исчерпан: "
                f"потрачено {spent}, лимит {limit}, запрошено ещё {expected_cost}"
            )

    def can_afford(self, tokens: int = 0, money: float = 0.0) -> bool:
        """Быстрая проверка: хватает ли токенов и денег."""
        return (self.check(ResourceType.TOKENS, tokens) and
                self.check(ResourceType.MONEY, money))

    # ── Приоритизация задач ───────────────────────────────────────────────────

    def prioritize(self, tasks: list[dict]) -> list[dict]:
        """
        Сортирует задачи по соотношению ценность/стоимость.
        Каждая задача должна иметь поля 'value' и 'cost'.

        Returns:
            Отсортированный список (лучшие — первые).
        """
        def score(task):
            cost = task.get('cost', 1)
            value = task.get('value', 1)
            return value / cost if cost > 0 else 0

        return sorted(tasks, key=score, reverse=True)

    def filter_affordable(self, tasks: list[dict],
                          resource: ResourceType = ResourceType.TOKENS) -> list[dict]:
        """Оставляет только задачи, которые можно выполнить в рамках бюджета."""
        remaining = self._remaining(resource)
        if remaining is None:
            return tasks  # лимит не задан

        affordable = []
        budget_left = remaining
        for task in tasks:
            cost = task.get('cost', 0)
            if budget_left >= cost:
                affordable.append(task)
                budget_left -= cost
        return affordable

    # ── Деградация при превышении ─────────────────────────────────────────────

    def stop(self, reason: str | None = None):
        """Останавливает систему из-за превышения бюджета."""
        self._stopped = True
        self._status = BudgetStatus.STOPPED
        msg = f"Система остановлена: {reason or 'бюджет исчерпан'}"
        self._log(msg)
        if self.human_approval:
            self.human_approval.request_approval('budget_stop', msg)

    def resume(self):
        """Возобновляет работу после остановки."""
        self._stopped = False
        self._status = BudgetStatus.OK
        self._log("Работа системы возобновлена.", level='info')

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    # ── Отчёт ────────────────────────────────────────────────────────────────

    def get_status(self, resource: ResourceType | None = None) -> dict:
        """Возвращает текущий статус бюджета по ресурсу или всем ресурсам."""
        if resource:
            spent = self._spent.get(resource, 0)
            limit = self._limits.get(resource)
            return {
                'resource': resource.value,
                'spent': spent,
                'limit': limit,
                'remaining': (limit - spent) if limit else None,
                'usage_pct': round(spent / limit * 100, 1) if limit else None,
                'status': self._check_status(resource).value,
            }
        return {r.value: self.get_status(r) for r in ResourceType}

    def summary(self) -> dict:
        totals = {r.value: self._spent.get(r, 0) for r in ResourceType}
        return {
            'overall_status': self._status.value,
            'stopped': self._stopped,
            'spent': totals,
            'limits': {r.value: self._limits.get(r) for r in ResourceType},
            'transactions': len(self._transactions),
        }

    def get_transactions(self, resource: ResourceType | None = None,
                         last_n: int | None = None) -> list[dict]:
        txs = self._transactions
        if resource:
            txs = [t for t in txs if t['resource'] == resource.value]
        if last_n:
            txs = txs[-last_n:]
        return txs

    def reset(self, resource: ResourceType | None = None):
        """Сбрасывает счётчик расходов (новый период)."""
        if resource:
            self._spent[resource] = 0.0
            self._log(f"Счётчик {resource.value} сброшен.", level='info')
        else:
            self._spent = {r: 0.0 for r in self._limits}
            self._stopped = False
            self._status = BudgetStatus.OK
            self._log("Все счётчики сброшены.", level='info')
        self._save_spent()

    def reset_period(self):
        """
        Сбрасывает лимитные счётчики (токены, запросы) но НЕ деньги.
        Вызывать раз в сутки/неделю для периодических ограничений.
        """
        for resource in (ResourceType.TOKENS, ResourceType.REQUESTS,
                         ResourceType.COMPUTE, ResourceType.MEMORY):
            if resource in self._spent:
                self._spent[resource] = 0.0
        self._stopped = False
        self._status = BudgetStatus.OK
        self._exceeded_resources = [
            r for r in self._exceeded_resources
            if r == ResourceType.MONEY.value
        ]
        self._last_period_reset_ts = time.time()
        self._save_spent()
        self._log("Периодические счётчики сброшены (деньги сохранены).", level='info')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def gate(self) -> bool:
        """
        Шлагбаум перед каждым действием агента.

        Возвращает True если работа разрешена, False если нужно остановиться.
        Вызывается в AutonomousLoop перед фазой ACT.
        """
        self._maybe_auto_reset_period()
        if self._stopped:
            return False
        # Проверяем все ресурсы с лимитами
        for resource, limit in self._limits.items():
            spent = self._spent.get(resource, 0)
            if spent >= limit:
                self._trigger_stop(resource, spent, limit)
                return False
        return True

    def get_exceeded_details(self) -> list[str]:
        """Возвращает исчерпанные лимиты в формате `resource: spent/limit`."""
        details: list[str] = []
        for resource, limit in self._limits.items():
            spent = self._spent.get(resource, 0)
            if spent >= limit:
                details.append(f"{resource.value}: {spent:.1f}/{limit}")
        return details

    def _maybe_auto_reset_period(self):
        """Раз в сутки сбрасывает периодические лимиты и автоматически возобновляет работу."""
        if not self._periodic_reset_enabled:
            return
        now = time.time()
        if (now - self._last_period_reset_ts) < self._periodic_reset_sec:
            return

        self._log(
            "Автосброс периодических лимитов (tokens/requests/compute/memory)",
            level='info',
        )
        self.reset_period()
        self.resume()

    def _check_status(self, resource: ResourceType) -> BudgetStatus:
        limit = self._limits.get(resource)
        if limit is None:
            return BudgetStatus.OK
        spent = self._spent.get(resource, 0)
        ratio = spent / limit

        if ratio >= 1.0:
            self._status = BudgetStatus.EXCEEDED
            if resource.value not in self._exceeded_resources:
                self._exceeded_resources.append(resource.value)
                self._log(f"ЛИМИТ ПРЕВЫШЕН: {resource.value} ({spent:.1f}/{limit})")
                # Автоматическая остановка
                if self.auto_stop and not self._stopped:
                    self._trigger_stop(resource, spent, limit)
            return BudgetStatus.EXCEEDED
        elif ratio >= self._warn_threshold:
            self._log(
                f"Внимание: {resource.value} = {ratio*100:.0f}% "
                f"({spent:.1f}/{limit})"
            )
            if self._status == BudgetStatus.OK:
                self._status = BudgetStatus.WARNING
            return BudgetStatus.WARNING
        return BudgetStatus.OK

    def _trigger_stop(self, resource: ResourceType, spent: float, limit: float):
        """Вызывается автоматически при превышении любого лимита."""
        reason = (
            f"Лимит {resource.value} исчерпан: "
            f"потрачено {spent:.1f}, лимит {limit:.1f}"
        )
        self.stop(reason)

    def _remaining(self, resource: ResourceType) -> float | None:
        limit = self._limits.get(resource)
        if limit is None:
            return None
        return max(0.0, limit - self._spent.get(resource, 0))

    # ── Персистентность расходов ──────────────────────────────────────────────

    def _save_spent(self):
        """Сохраняет накопленные денежные расходы на диск."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._persist_path)), exist_ok=True)
            data = {
                'money': self._spent.get(ResourceType.MONEY, 0.0),
                'last_updated': time.time(),
                'last_period_reset': self._last_period_reset_ts,
            }
            with open(self._persist_path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass  # персистентность опциональна, не ломаем логику

    def _load_spent(self):
        """Загружает накопленные денежные расходы с диска."""
        try:
            if not os.path.exists(self._persist_path):
                return
            with open(self._persist_path, encoding='utf-8') as f:
                data = json.load(f)
            money_spent = float(data.get('money', 0.0))
            self._last_period_reset_ts = float(data.get('last_period_reset', time.time()))
            if money_spent > 0:
                self._spent[ResourceType.MONEY] = money_spent
                self._log(
                    f"Загружены расходы с прошлых сессий: ${money_spent:.4f}",
                    level='info'
                )
        except Exception:
            pass

    def _log(self, message: str, level: str = 'warning'):
        if self.monitoring:
            log_fn = getattr(self.monitoring, level, self.monitoring.warning)
            log_fn(message, source='budget_control')
        else:
            print(f"[BudgetControl] {message}")
