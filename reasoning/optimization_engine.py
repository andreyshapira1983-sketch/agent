# Optimization Engine (оптимизационные задачи) — Cognitive Core, Layer 3
# Архитектура автономного AI-агента
# Динамическое программирование для задач типа knapsack, scheduling и т.п.
# Строгое доказательство оптимальности (не эвристика).

from __future__ import annotations

from typing import Any, Callable, NamedTuple
from enum import Enum
import math


# ─────────────────────────────────────────────────────────────────────────────
# 1. KNAPSACK PROBLEMS
# ─────────────────────────────────────────────────────────────────────────────

class Item(NamedTuple):
    """Предмет для задачи knapsack."""
    item_id: str
    cost: float          # вес / ресурс
    value: float         # ценность / награда
    probability: float = 1.0   # для стохастических задач


class KnapsackSolution:
    """Решение задачи knapsack."""

    def __init__(self, items_selected: list[tuple[str, int]],
                 total_cost: float, total_value: float,
                 is_optimal: bool = False):
        self.items_selected = items_selected           # [(item_id, count), ...]
        self.total_cost = total_cost
        self.total_value = total_value
        self.is_optimal = is_optimal
        self.expected_value = total_value              # может усредняться для вероятностных задач

    def to_dict(self) -> dict:
        return {
            'items_selected': self.items_selected,
            'total_cost':     self.total_cost,
            'total_value':    self.total_value,
            'expected_value': self.expected_value,
            'is_optimal':     self.is_optimal,
        }


class UnboundedKnapsackSolver:
    """
    Решатель для unbounded knapsack problem (обычный knapsack).

    Задача:
        maximize ∑ (value_i × count_i)
        при ∑ (cost_i × count_i) ≤ capacity

    Методы:
    1. Dynamic Programming — O(n × capacity), строго оптимальное
    2. Enumeration + Pruning — полный перебор с отсечением
    3. Greedy (для сравнения) — O(n log n) эвристика
    """

    def __init__(self, items: list[Item], capacity: float,
                 allow_fractional: bool = False):
        self.items = items
        self.capacity = capacity
        self.allow_fractional = allow_fractional   # для дробного knapsack
        self._dp_table: dict[float, float] = {}
        self._dp_selection: dict[float, list[str]] = {}

    # ── DP решение (оптимальное) ──────────────────────────────────────────────

    def solve_dp(self) -> KnapsackSolution:
        """
        Динамическое программирование: табуляция.

        DP[w] = максимальная ценность при весе w
        """
        cap = int(self.capacity) if self.capacity == int(self.capacity) else self.capacity
        dp = {}
        selection = {}

        for w in range(0, int(cap) + 1):
            dp[w] = 0.0
            selection[w] = []

        for w in range(1, int(cap) + 1):
            for item in self.items:
                item_cost = int(item.cost) if item.cost == int(item.cost) else item.cost
                expected_value = item.value * item.probability
                if item_cost <= w:
                    candidate_value = dp[w - item_cost] + expected_value
                    if candidate_value > dp[w]:
                        dp[w] = candidate_value
                        selection[w] = selection[w - item_cost] + [item.item_id]

        self._dp_table = dp
        self._dp_selection = selection

        # Восстанавливаем решение
        items_selected = self._reconstruct_solution(selection[int(cap)])
        return KnapsackSolution(
            items_selected,
            int(cap),
            dp[int(cap)],
            is_optimal=True
        )

    def _reconstruct_solution(self, item_ids: list[str]) -> list[tuple[str, int]]:
        """Преобразует список item_id в [(item_id, count), ...]."""
        from collections import Counter
        counts = Counter(item_ids)
        return sorted(counts.items())

    # ── Greedy решение (эвристика для сравнения) ─────────────────────────────

    def solve_greedy_by_value_per_cost(self) -> KnapsackSolution:
        """
        Greedy: выбирает предметы по убыванию value/cost (или EV/cost).
        Эвристика, НЕ гарантирует оптимум для unbounded knapsack.
        """
        value_per_cost = []
        for item in self.items:
            ev = item.value * item.probability
            ratio = ev / item.cost
            value_per_cost.append((ratio, item))

        value_per_cost.sort(reverse=True, key=lambda x: x[0])

        remaining = self.capacity
        selected = []
        total_ev = 0.0
        for ratio, item in value_per_cost:
            ev = item.value * item.probability
            count = int(remaining // item.cost)
            if count > 0:
                selected.append((item.item_id, count))
                total_ev += count * ev
                remaining -= count * item.cost

        return KnapsackSolution(
            selected,
            self.capacity - remaining,
            total_ev,
            is_optimal=False   # Greedy не гарантирует оптимум
        )

    # ── Полный перебор с отсечением ──────────────────────────────────────────

    def solve_exhaustive(self, max_iterations: int = 100_000) -> KnapsackSolution:
        """
        Полный перебор с ограничением. Проверяет все комбинации/количества.
        Медленно, но гарантирует оптимум (если не превышен max_iterations).
        """
        best_value = 0.0
        best_selection = []
        iterations = 0

        def backtrack(item_idx: int, remaining: float, current_value: float,
                      selection: list[tuple[str, int]]) -> None:
            nonlocal best_value, best_selection, iterations
            iterations += 1
            if iterations > max_iterations:
                return

            if item_idx == len(self.items) or remaining <= 0:
                if current_value > best_value:
                    best_value = current_value
                    best_selection = list(selection)
                return

            item = self.items[item_idx]
            ev = item.value * item.probability

            # Пробуем разные количества текущего item
            max_count = int(remaining // item.cost)
            for count in range(max_count, -1, -1):
                new_selection = selection + [(item.item_id, count)]
                new_value = current_value + count * ev
                new_remaining = remaining - count * item.cost
                backtrack(item_idx + 1, new_remaining, new_value, new_selection)

        backtrack(0, self.capacity, 0.0, [])

        # Вычисляем total_cost на основе выбранных предметов
        total_cost = sum(
            cnt * next(it.cost for it in self.items if it.item_id == iid)
            for iid, cnt in best_selection
        ) if best_selection else 0

        return KnapsackSolution(
            best_selection,
            total_cost,
            best_value,
            is_optimal=(iterations <= max_iterations)
        )

    # ── Сравнение методов ─────────────────────────────────────────────────────

    def compare_methods(self) -> dict:
        """Сравнивает DP, Greedy и Exhaustive."""
        dp_sol = self.solve_dp()
        greedy_sol = self.solve_greedy_by_value_per_cost()
        exhaustive_sol = self.solve_exhaustive()

        return {
            'dp': {
                **dp_sol.to_dict(),
                'method': 'Dynamic Programming',
            },
            'greedy': {
                **greedy_sol.to_dict(),
                'method': 'Greedy (heuristic)',
            },
            'exhaustive': {
                **exhaustive_sol.to_dict(),
                'method': 'Exhaustive Search',
            },
            'optimal_value': max(dp_sol.total_value, exhaustive_sol.total_value),
            'greedy_gap': (
                round((dp_sol.total_value - greedy_sol.total_value) / dp_sol.total_value, 4)
                if dp_sol.total_value > 0 else 0.0
            ),
            'analysis': (
                f"DP value: {dp_sol.total_value:.1f}, "
                f"Greedy value: {greedy_sol.total_value:.1f}, "
                f"Gap: {(dp_sol.total_value - greedy_sol.total_value):.1f} "
                f"({(dp_sol.total_value - greedy_sol.total_value) / dp_sol.total_value * 100:.1f}%)"
            ),
        }


class BoundedKnapsackSolver:
    """
    Решатель для 0/1 knapsack (каждый предмет берётся не более 1 раза).
    """

    def __init__(self, items: list[Item], capacity: float):
        self.items = items
        self.capacity = capacity

    def solve_dp(self) -> KnapsackSolution:
        """DP для 0/1 knapsack: O(n × capacity)."""
        n = len(self.items)
        cap = int(self.capacity)

        # dp[i][w] = макс. ценность первых i предметов с весом w
        dp = [[0.0 for _ in range(cap + 1)] for _ in range(n + 1)]

        for i in range(1, n + 1):
            item = self.items[i - 1]
            ev = item.value * item.probability
            for w in range(cap + 1):
                # Не берём i-й предмет
                dp[i][w] = dp[i - 1][w]
                # Берём i-й предмет
                if item.cost <= w:
                    dp[i][w] = max(dp[i][w], dp[i - 1][w - int(item.cost)] + ev)

        # Восстанавливаем решение
        selected = []
        w = cap
        for i in range(n, 0, -1):
            if dp[i][w] != dp[i - 1][w]:
                selected.append((self.items[i - 1].item_id, 1))
                w -= int(self.items[i - 1].cost)

        total_value = dp[n][cap]
        total_cost = sum(c * int(next(it.cost for it in self.items if it.item_id == iid))
                        for iid, c in selected)
        return KnapsackSolution(selected, total_cost, total_value, is_optimal=True)


# ─────────────────────────────────────────────────────────────────────────────
# 2. SCHEDULING PROBLEMS
# ─────────────────────────────────────────────────────────────────────────────

class SchedulingJob(NamedTuple):
    """Работа для задачи scheduling."""
    job_id: str
    duration: float
    deadline: float
    priority: float = 1.0   # вес приоритета


class SchedulingResult:
    """Результат scheduling."""

    def __init__(self, order: list[str], total_time: float,
                 missed_deadlines: list[str], total_delay: float):
        self.order = order
        self.total_time = total_time
        self.missed_deadlines = missed_deadlines
        self.total_delay = total_delay

    def to_dict(self) -> dict:
        return {
            'order':              self.order,
            'total_time':         self.total_time,
            'missed_deadlines':   self.missed_deadlines,
            'total_delay':        self.total_delay,
            'num_missed':         len(self.missed_deadlines),
        }


class SchedulingSolver:
    """
    Решатель для задачи scheduling: минимизация опозданий / максимизация приоритета.

    Методы:
    - Earliest Deadline First (EDF) — классный алгоритм для deadline scheduling
    - Shortest Job First (SJF) — минимизация среднего времени ожидания
    - Priority-based — взвешенный приоритет
    """

    def __init__(self, jobs: list[SchedulingJob]):
        self.jobs = jobs

    def solve_edf(self) -> SchedulingResult:
        """Earliest Deadline First: сортирует по deadline."""
        sorted_jobs = sorted(self.jobs, key=lambda j: j.deadline)
        return self._execute_schedule(sorted_jobs)

    def solve_sjf(self) -> SchedulingResult:
        """Shortest Job First: минимизирует ожидание."""
        sorted_jobs = sorted(self.jobs, key=lambda j: j.duration)
        return self._execute_schedule(sorted_jobs)

    def solve_by_priority(self) -> SchedulingResult:
        """Приоритетное scheduling."""
        sorted_jobs = sorted(self.jobs, key=lambda j: -j.priority)
        return self._execute_schedule(sorted_jobs)

    def _execute_schedule(self, ordered_jobs: list[SchedulingJob]) -> SchedulingResult:
        current_time = 0.0
        order = []
        missed = []
        total_delay = 0.0

        for job in ordered_jobs:
            order.append(job.job_id)
            current_time += job.duration
            delay = max(0, current_time - job.deadline)
            total_delay += delay
            if delay > 0:
                missed.append(job.job_id)

        return SchedulingResult(order, current_time, missed, total_delay)


# ─────────────────────────────────────────────────────────────────────────────
# 3. VEHICLE ROUTING / TSP-подобные задачи
# ─────────────────────────────────────────────────────────────────────────────

class RouteResult:
    """Результат маршрутизации."""

    def __init__(self, route: list[str], total_distance: float,
                 total_cost: float, visits: int):
        self.route = route
        self.total_distance = total_distance
        self.total_cost = total_cost
        self.visits = visits

    def to_dict(self) -> dict:
        return {
            'route':           self.route,
            'total_distance':  self.total_distance,
            'total_cost':      self.total_cost,
            'visits':          self.visits,
        }


class VehicleRoutingSolver:
    """
    Упрощённый решатель для vehicle routing.

    Методы:
    - Nearest Neighbor (greedy construction)
    - 2-opt local search (улучшение существующего маршрута)
    """

    def __init__(self, distance_matrix: dict,
                 costs: dict | None = None):
        """
        distance_matrix: dict[tuple[str,str], float] — расстояния между локациями
        costs: dict[str, float] — остальные пути
        """
        self.distance_matrix = distance_matrix
        self.costs = costs or {}

    def nearest_neighbor(self, start: str,
                         locations: list[str]) -> RouteResult:
        """Жадный алгоритм: всегда идёт к ближайшей непосещённой локации."""
        route = [start]
        unvisited = set(locations) - {start}
        total_dist = 0.0

        current = start
        while unvisited:
            nearest = min(
                unvisited,
                key=lambda loc: self.distance_matrix.get((current, loc), float('inf'))
            )
            dist = self.distance_matrix.get((current, nearest), 0)
            total_dist += dist
            route.append(nearest)
            unvisited.remove(nearest)
            current = nearest

        return RouteResult(route, total_dist, total_dist, len(route))

    def two_opt(self, route: list[str]) -> RouteResult:
        """Локальный поиск: пытается улучшить маршрут перестановками."""
        improved = True
        while improved:
            improved = False
            for i in range(1, len(route) - 2):
                for j in range(i + 1, len(route)):
                    new_route = route[:i] + route[i:j][::-1] + route[j:]
                    new_dist = self._route_distance(new_route)
                    old_dist = self._route_distance(route)
                    if new_dist < old_dist:
                        route = new_route
                        improved = True
                        break
                if improved:
                    break
        return RouteResult(route, self._route_distance(route), 0, len(route))

    def _route_distance(self, route: list[str]) -> float:
        total = 0.0
        for i in range(len(route) - 1):
            total += self.distance_matrix.get((route[i], route[i + 1]), 0)
        return total


# ─────────────────────────────────────────────────────────────────────────────
# 4. ОБЩИЙ ДВИЖОК
# ─────────────────────────────────────────────────────────────────────────────

class OptimizationEngine:
    """
    Единая точка доступа для всех оптимизационных задач.

    Гарантирует:
    - Строгие методы (DP, точный перебор) для доказательства оптимальности
    - Эвристики для сравнения и понимания разрыва оптимальности
    - Полную прозрачность метода и гарантий
    """

    @staticmethod
    def create_unbounded_knapsack(items: list[Item],
                                   capacity: float) -> UnboundedKnapsackSolver:
        return UnboundedKnapsackSolver(items, capacity)

    @staticmethod
    def create_bounded_knapsack(items: list[Item],
                                 capacity: float) -> BoundedKnapsackSolver:
        return BoundedKnapsackSolver(items, capacity)

    @staticmethod
    def create_scheduling(jobs: list[SchedulingJob]) -> SchedulingSolver:
        return SchedulingSolver(jobs)

    @staticmethod
    def create_vehicle_routing(distance_matrix: dict) -> VehicleRoutingSolver:
        return VehicleRoutingSolver(distance_matrix)

    @staticmethod
    def knapsack_analysis(items: list[Item], capacity: float) -> dict:
        """
        Комплексный анализ: сравнивает все методы решения unbounded knapsack.
        Возвращает:
        - DP (оптимум)
        - Greedy (эвристика)
        - Разрыв оптимальности
        - Рекомендацию
        """
        solver = UnboundedKnapsackSolver(items, capacity)
        comparison = solver.compare_methods()

        recommendation = (
            "DP solution is OPTIMAL. "
            f"Greedy heuristic achieves {(1 - comparison['greedy_gap']) * 100:.1f}% of optimum. "
            f"Gap: {comparison['greedy_gap'] * 100:.1f}%"
        )

        return {
            **comparison,
            'recommendation': recommendation,
        }
