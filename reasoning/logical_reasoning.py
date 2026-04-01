# Logical Reasoning System (логическое рассуждение) — Cognitive Core, Layer 3
# Архитектура автономного AI-агента
# Три ключевых компонента:
#   1. ParadoxDetector     — обнаружение и разрешение логических парадоксов
#   2. ExhaustiveSearch    — полный перебор вариантов (backtracking, CSP)
#   3. FormalStrategyModel — формальные модели стратегии (minimax, Nash, decision tree)

from __future__ import annotations

import itertools
import math
import heapq
from enum import Enum
from typing import Any, Callable, Generator


# ─────────────────────────────────────────────────────────────────────────────
# 1. PARADOX DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class ParadoxType(Enum):
    SELF_REFERENCE   = 'self_reference'   # утверждение ссылается на само себя
    CONTRADICTION    = 'contradiction'    # A и ¬A одновременно истинны
    CIRCULAR_CHAIN   = 'circular_chain'  # A→B→C→A
    UNDEFINED        = 'undefined'        # истинность не определима

class ParadoxResolution(Enum):
    FLAG_UNDEFINED   = 'flag_undefined'   # пометить как неопределённое
    REJECT_PREMISE   = 'reject_premise'   # отклонить исходную посылку
    SPLIT_LEVELS     = 'split_levels'     # разделить уровни (теория типов)
    ACCEPT_BOTH      = 'accept_both'      # принять оба значения (диалетеизм)


class LogicalStatement:
    """Логическое высказывание с идентификатором и истинностным значением."""

    def __init__(self, stmt_id: str, content: str,
                 truth_value: bool | None = None,
                 refers_to: list[str] | None = None):
        self.stmt_id = stmt_id
        self.content = content
        self.truth_value = truth_value      # None = неопределено
        self.refers_to: list[str] = refers_to or []   # ссылки на другие stmt_id

    def to_dict(self) -> dict:
        return {
            'stmt_id':     self.stmt_id,
            'content':     self.content,
            'truth_value': self.truth_value,
            'refers_to':   self.refers_to,
        }


class ParadoxResult:
    """Результат анализа парадокса."""

    def __init__(self, paradox_type: ParadoxType,
                 involved: list[str],
                 resolution: ParadoxResolution,
                 explanation: str):
        self.paradox_type = paradox_type
        self.involved = involved                # stmt_id участников
        self.resolution = resolution
        self.explanation = explanation

    def to_dict(self) -> dict:
        return {
            'paradox_type': self.paradox_type.value,
            'involved':     self.involved,
            'resolution':   self.resolution.value,
            'explanation':  self.explanation,
        }


class ParadoxDetector:
    """
    Обнаруживает и разрешает логические парадоксы в наборе высказываний.

    Поддерживаемые парадоксы:
    - Парадокс лжеца: «Это утверждение ложно» (самореференция)
    - Прямое противоречие: A и ¬A
    - Циклические цепи: A→B→A или более длинные циклы
    """

    _SELF_REF_MARKERS = (
        'это утверждение', 'данное утверждение', 'эта фраза',
        'this statement', 'this sentence', 'this claim',
        'я лгу', 'i am lying',
    )

    def __init__(self):
        self._statements: dict[str, LogicalStatement] = {}

    def add_statement(self, stmt: LogicalStatement) -> None:
        self._statements[stmt.stmt_id] = stmt

    def add(self, stmt_id: str, content: str,
            truth_value: bool | None = None,
            refers_to: list[str] | None = None) -> LogicalStatement:
        s = LogicalStatement(stmt_id, content, truth_value, refers_to)
        self._statements[stmt_id] = s
        return s

    # ── Обнаружение парадоксов ────────────────────────────────────────────────

    def analyze(self) -> list[ParadoxResult]:
        results: list[ParadoxResult] = []
        results.extend(self._check_self_reference())
        results.extend(self._check_contradictions())
        results.extend(self._check_circular_chains())
        return results

    def analyze_statement(self, content: str) -> ParadoxResult | None:
        """Быстрая проверка одного высказывания на парадоксальность."""
        lower = content.lower()
        for marker in self._SELF_REF_MARKERS:
            if marker in lower:
                return ParadoxResult(
                    paradox_type=ParadoxType.SELF_REFERENCE,
                    involved=[content[:60]],
                    resolution=ParadoxResolution.FLAG_UNDEFINED,
                    explanation=(
                        f"Высказывание содержит самореференцию ({marker!r}). "
                        "Истинностное значение не определимо классической логикой. "
                        "Применяется стратегия: пометить как UNDEFINED."
                    )
                )
        return None

    def _check_self_reference(self) -> list[ParadoxResult]:
        out = []
        for s in self._statements.values():
            # Прямая самоссылка через refers_to
            if s.stmt_id in s.refers_to:
                out.append(ParadoxResult(
                    ParadoxType.SELF_REFERENCE,
                    [s.stmt_id],
                    ParadoxResolution.SPLIT_LEVELS,
                    f"Высказывание '{s.stmt_id}' ссылается на само себя. "
                    "Разрешение: теория типов (split levels) — утверждение "
                    "и мета-утверждение о нём разделяются по уровням."
                ))
            # Лингвистическая самореференция
            elif self.analyze_statement(s.content) is not None:
                pr = self.analyze_statement(s.content)
                pr.involved = [s.stmt_id]
                out.append(pr)
        return out

    def _check_contradictions(self) -> list[ParadoxResult]:
        out = []
        stmts = list(self._statements.values())
        for i, a in enumerate(stmts):
            for b in stmts[i + 1:]:
                if (a.truth_value is not None and b.truth_value is not None
                        and a.truth_value != b.truth_value
                        and self._semantically_opposite(a.content, b.content)):
                    out.append(ParadoxResult(
                        ParadoxType.CONTRADICTION,
                        [a.stmt_id, b.stmt_id],
                        ParadoxResolution.REJECT_PREMISE,
                        f"'{a.stmt_id}' (truth={a.truth_value}) и "
                        f"'{b.stmt_id}' (truth={b.truth_value}) противоречат друг другу. "
                        "Разрешение: отклонить посылку с меньшей уверенностью."
                    ))
        return out

    def _check_circular_chains(self) -> list[ParadoxResult]:
        """Поиск циклов в графе ссылок методом DFS."""
        out = []
        visited: set[str] = set()

        def dfs(node: str, path: list[str]) -> None:
            if node in path:
                cycle = path[path.index(node):]
                out.append(ParadoxResult(
                    ParadoxType.CIRCULAR_CHAIN,
                    cycle,
                    ParadoxResolution.FLAG_UNDEFINED,
                    f"Обнаружен цикл: {' → '.join(cycle)} → {node}. "
                    "Истинностные значения в цикле неопределимы."
                ))
                return
            if node in visited:
                return
            visited.add(node)
            stmt = self._statements.get(node)
            if stmt:
                for ref in stmt.refers_to:
                    dfs(ref, path + [node])

        for sid in self._statements:
            dfs(sid, [])
        return out

    @staticmethod
    def _semantically_opposite(a: str, b: str) -> bool:
        """Грубая эвристика: одинаковые слова, но одно отрицается."""
        tokens_a = set(a.lower().split())
        tokens_b = set(b.lower().split())
        negations = {'не', 'нет', 'ложь', 'not', 'false', 'never', 'no'}
        shared = tokens_a & tokens_b - negations
        neg_a = bool(tokens_a & negations)
        neg_b = bool(tokens_b & negations)
        return bool(shared) and neg_a != neg_b

    def report(self) -> dict:
        results = self.analyze()
        return {
            'total_statements': len(self._statements),
            'paradoxes_found':  len(results),
            'details':          [r.to_dict() for r in results],
        }


class GraphPathResult:
    """Результат поиска пути в графе."""

    def __init__(self, path: list[str], total_cost: float,
                 visited_nodes: int, is_optimal: bool = False):
        self.path = path
        self.total_cost = total_cost
        self.visited_nodes = visited_nodes
        self.is_optimal = is_optimal

    def to_dict(self) -> dict:
        return {
            'path': self.path,
            'total_cost': self.total_cost,
            'visited_nodes': self.visited_nodes,
            'is_optimal': self.is_optimal,
        }


class AStarSolver:
    """Точный поиск пути: A* с верификацией через Dijkstra."""

    def __init__(self, graph: dict[str, list[tuple[str, float]]],
                 heuristics: dict[str, float] | None = None):
        self.graph = graph
        self.heuristics = heuristics or {}

    def solve(self, start: str, goal: str) -> GraphPathResult:
        frontier: list[tuple[float, float, str, list[str]]] = []
        heapq.heappush(frontier, (self._heuristic(start), 0.0, start, [start]))
        best_cost: dict[str, float] = {start: 0.0}
        visited = 0

        while frontier:
            priority, cost_so_far, node, path = heapq.heappop(frontier)
            _ = priority
            visited += 1

            if node == goal:
                dijkstra = self.solve_dijkstra(start, goal)
                is_optimal = abs(dijkstra.total_cost - cost_so_far) < 1e-9
                return GraphPathResult(path, cost_so_far, visited, is_optimal=is_optimal)

            if cost_so_far > best_cost.get(node, float('inf')):
                continue

            for neighbor, edge_cost in self.graph.get(node, []):
                new_cost = cost_so_far + float(edge_cost)
                if new_cost < best_cost.get(neighbor, float('inf')):
                    best_cost[neighbor] = new_cost
                    estimate = new_cost + self._heuristic(neighbor)
                    heapq.heappush(frontier, (estimate, new_cost, neighbor, path + [neighbor]))

        return GraphPathResult([], float('inf'), visited, is_optimal=False)

    def solve_dijkstra(self, start: str, goal: str) -> GraphPathResult:
        frontier: list[tuple[float, str, list[str]]] = [(0.0, start, [start])]
        best_cost: dict[str, float] = {start: 0.0}
        visited = 0

        while frontier:
            cost_so_far, node, path = heapq.heappop(frontier)
            visited += 1
            if node == goal:
                return GraphPathResult(path, cost_so_far, visited, is_optimal=True)
            if cost_so_far > best_cost.get(node, float('inf')):
                continue
            for neighbor, edge_cost in self.graph.get(node, []):
                new_cost = cost_so_far + float(edge_cost)
                if new_cost < best_cost.get(neighbor, float('inf')):
                    best_cost[neighbor] = new_cost
                    heapq.heappush(frontier, (new_cost, neighbor, path + [neighbor]))

        return GraphPathResult([], float('inf'), visited, is_optimal=False)

    def _heuristic(self, node: str) -> float:
        value = self.heuristics.get(node, 0.0)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. EXHAUSTIVE SEARCH (полный перебор / backtracking / CSP)
# ─────────────────────────────────────────────────────────────────────────────

class SearchResult:
    """Результат поиска."""

    def __init__(self, solutions: list[Any], nodes_explored: int,
                 pruned: int = 0, complete: bool = True):
        self.solutions = solutions
        self.nodes_explored = nodes_explored
        self.pruned = pruned           # количество ветвей, отсечённых pruning
        self.complete = complete       # был ли перебор полным

    def to_dict(self) -> dict:
        return {
            'solution_count':  len(self.solutions),
            'nodes_explored':  self.nodes_explored,
            'pruned':          self.pruned,
            'complete':        self.complete,
            'solutions':       self.solutions,
        }


class ExhaustiveSearch:
    """
    Полный перебор вариантов.

    Методы:
    - enumerate_combinations  — все комбинации/перестановки из множества
    - backtrack               — backtracking с произвольными ограничениями
    - solve_csp               — решение задачи удовлетворения ограничений (CSP)
    """

    def __init__(self, max_nodes: int = 1_000_000):
        self._max_nodes = max_nodes   # защита от бесконечного перебора

    # ── Комбинаторный перебор ─────────────────────────────────────────────────

    def enumerate_combinations(
        self,
        items: list[Any],
        length: int | None = None,
        mode: str = 'combinations',   # 'combinations' | 'permutations' | 'product'
        repeat: int = 1,
    ) -> SearchResult:
        """
        Генерирует все комбинации/перестановки/декартово произведение.

        mode:
            'combinations'  — C(n,k), порядок не важен
            'permutations'  — P(n,k), порядок важен
            'product'       — декартово произведение (с повторениями, repeat раз)
        """
        r = length if length is not None else len(items)
        if mode == 'combinations':
            gen: Any = itertools.combinations(items, r)
        elif mode == 'permutations':
            gen = itertools.permutations(items, r)
        elif mode == 'product':
            gen = itertools.product(items, repeat=repeat)
        else:
            raise ValueError(f"Unknown mode: {mode!r}")

        solutions = []
        explored = 0
        for sol in gen:
            solutions.append(list(sol))
            explored += 1
            if explored >= self._max_nodes:
                return SearchResult(solutions, explored, complete=False)
        return SearchResult(solutions, explored)

    # ── Backtracking ──────────────────────────────────────────────────────────

    def backtrack(
        self,
        variables: list[str],
        domains: dict[str, list[Any]],
        is_consistent: Callable[[dict[str, Any]], bool],
        find_all: bool = False,
    ) -> SearchResult:
        """
        Общий backtracking для задач присваивания значений переменным.

        variables       — список переменных
        domains         — допустимые значения для каждой переменной
        is_consistent   — функция проверки частичного присваивания
        find_all        — найти все решения (True) или только первое (False)
        """
        solutions: list[dict] = []
        nodes = [0]
        pruned = [0]

        def _bt(assignment: dict[str, Any], depth: int) -> bool:
            if nodes[0] >= self._max_nodes:
                return False
            if depth == len(variables):
                solutions.append(dict(assignment))
                return not find_all   # False → продолжить; True → остановить
            var = variables[depth]
            for value in domains[var]:
                nodes[0] += 1
                assignment[var] = value
                if is_consistent(assignment):
                    stop = _bt(assignment, depth + 1)
                    if stop:
                        return True
                else:
                    pruned[0] += 1
                del assignment[var]
            return False

        _bt({}, 0)
        complete = nodes[0] < self._max_nodes
        return SearchResult(solutions, nodes[0], pruned[0], complete)

    # ── CSP (Constraint Satisfaction Problem) ────────────────────────────────

    def solve_csp(
        self,
        variables: list[str],
        domains: dict[str, list[Any]],
        constraints: list[Callable[[str, Any, str, Any], bool]],
        find_all: bool = False,
    ) -> SearchResult:
        """
        Решение CSP с arc-consistency (AC-3) и backtracking.

        constraints — список функций constraint(var1, val1, var2, val2) → bool
        """
        # Применяем AC-3 для предварительного сужения доменов
        pruned_domains = self._ac3(variables, domains, constraints)

        def is_consistent(assignment: dict[str, Any]) -> bool:
            assigned = list(assignment.items())
            for i, (v1, val1) in enumerate(assigned):
                for v2, val2 in assigned[i + 1:]:
                    for c in constraints:
                        if not c(v1, val1, v2, val2):
                            return False
            return True

        return self.backtrack(variables, pruned_domains, is_consistent, find_all)

    def _ac3(
        self,
        variables: list[str],
        domains: dict[str, list[Any]],
        constraints: list[Callable],
    ) -> dict[str, list[Any]]:
        """Алгоритм AC-3: удаляет несовместимые значения из доменов."""
        domains = {v: list(d) for v, d in domains.items()}
        queue = [(xi, xj) for xi in variables for xj in variables if xi != xj]
        while queue:
            xi, xj = queue.pop(0)
            if self._revise(xi, xj, domains, constraints):
                if not domains[xi]:
                    return domains   # домен пуст — нет решений
                for xk in variables:
                    if xk != xi and xk != xj:
                        queue.append((xk, xi))
        return domains

    @staticmethod
    def _revise(
        xi: str, xj: str,
        domains: dict[str, list[Any]],
        constraints: list[Callable],
    ) -> bool:
        revised = False
        for vi in list(domains[xi]):
            # Есть ли хотя бы одно vj, совместимое со всеми ограничениями?
            supported = any(
                all(c(xi, vi, xj, vj) for c in constraints)
                for vj in domains[xj]
            )
            if not supported:
                domains[xi].remove(vi)
                revised = True
        return revised

    # ── Удобный интерфейс ─────────────────────────────────────────────────────

    def generate_subsets(self, items: list[Any],
                         min_size: int = 0,
                         max_size: int | None = None) -> list[list[Any]]:
        """Все подмножества items заданного размера."""
        max_size = max_size if max_size is not None else len(items)
        result = []
        for r in range(min_size, max_size + 1):
            for combo in itertools.combinations(items, r):
                result.append(list(combo))
                if len(result) >= self._max_nodes:
                    return result
        return result


# ─────────────────────────────────────────────────────────────────────────────
# 3. FORMAL STRATEGY MODEL (формальные модели стратегии)
# ─────────────────────────────────────────────────────────────────────────────

class GameOutcome:
    """Исход игры для конкретного набора стратегий игроков."""

    def __init__(self, strategies: tuple[int, ...], payoffs: tuple[float, ...]):
        self.strategies = strategies   # индексы стратегий для каждого игрока
        self.payoffs = payoffs          # выплаты каждому игроку

    def to_dict(self) -> dict:
        return {'strategies': list(self.strategies), 'payoffs': list(self.payoffs)}


class NashEquilibrium:
    """Равновесие Нэша (чистые стратегии)."""

    def __init__(self, strategies: tuple[int, ...], payoffs: tuple[float, ...],
                 is_dominant: bool = False):
        self.strategies = strategies
        self.payoffs = payoffs
        self.is_dominant = is_dominant   # является ли доминирующим равновесием

    def to_dict(self) -> dict:
        return {
            'strategies':   list(self.strategies),
            'payoffs':      list(self.payoffs),
            'is_dominant':  self.is_dominant,
        }


class NormalFormGame:
    """
    Игра в нормальной форме (матричная игра).

    Поддерживает:
    - поиск Nash equilibria (чистые стратегии)
    - нахождение доминирующих стратегий
    - вычисление minimax-значения для игр с нулевой суммой
    """

    def __init__(self, player_names: list[str],
                 payoff_matrix: list[Any]):
        """
        player_names    — имена игроков
        payoff_matrix   — тензор выплат.
            Для 2 игроков: payoff_matrix[i][j] = (payoff_p1, payoff_p2)
        """
        self.player_names = player_names
        self.n_players = len(player_names)
        self.payoff_matrix = payoff_matrix

    def _get_payoff(self, strategy_profile: tuple[int, ...]) -> tuple[float, ...]:
        """Возвращает кортеж выплат для профиля стратегий."""
        cell = self.payoff_matrix
        for idx in strategy_profile:
            cell = cell[idx]
        if isinstance(cell, (int, float)):
            return (float(cell),)
        return tuple(float(v) for v in cell)

    def _strategy_counts(self) -> list[int]:
        """Количество стратегий каждого игрока."""
        counts = []
        cell = self.payoff_matrix
        for _ in range(self.n_players):
            counts.append(len(cell))
            cell = cell[0]
        return counts

    def find_nash_equilibria(self) -> list[NashEquilibrium]:
        """Находит все Nash equilibria в чистых стратегиях."""
        strategy_counts = self._strategy_counts()
        all_profiles = list(itertools.product(*[range(n) for n in strategy_counts]))
        equilibria = []
        for profile in all_profiles:
            if self._is_nash(profile, strategy_counts):
                payoffs = self._get_payoff(profile)
                equilibria.append(NashEquilibrium(profile, payoffs))
        return equilibria

    def _is_nash(self, profile: tuple[int, ...],
                 strategy_counts: list[int]) -> bool:
        """Проверяет, является ли профиль равновесием Нэша."""
        base_payoffs = self._get_payoff(profile)
        for player_idx in range(self.n_players):
            base_payoff = base_payoffs[player_idx]
            for alt_strategy in range(strategy_counts[player_idx]):
                if alt_strategy == profile[player_idx]:
                    continue
                alt_profile = list(profile)
                alt_profile[player_idx] = alt_strategy
                alt_payoffs = self._get_payoff(tuple(alt_profile))
                if alt_payoffs[player_idx] > base_payoff:
                    return False   # игрок может отклониться и выиграть
        return True

    def find_dominant_strategies(self) -> dict[str, list[int]]:
        """
        Находит строго доминирующие стратегии для каждого игрока.
        Стратегия s* строго доминирует s, если для любого профиля противников
        payoff(s*) > payoff(s).
        """
        strategy_counts = self._strategy_counts()
        dominant: dict[str, list[int]] = {}
        for player_idx, name in enumerate(self.player_names):
            n_strats = strategy_counts[player_idx]
            dom_strategies = []
            for s in range(n_strats):
                is_dominant = True
                for s_alt in range(n_strats):
                    if s == s_alt:
                        continue
                    opponent_profiles = itertools.product(*[
                        range(strategy_counts[p])
                        for p in range(self.n_players) if p != player_idx
                    ])
                    for opp in opponent_profiles:
                        profile_s = list(opp)
                        profile_s.insert(player_idx, s)
                        profile_s_alt = list(opp)
                        profile_s_alt.insert(player_idx, s_alt)
                        pay_s = self._get_payoff(tuple(profile_s))[player_idx]
                        pay_s_alt = self._get_payoff(tuple(profile_s_alt))[player_idx]
                        if pay_s <= pay_s_alt:
                            is_dominant = False
                            break
                    if not is_dominant:
                        break
                if is_dominant:
                    dom_strategies.append(s)
            if dom_strategies:
                dominant[name] = dom_strategies
        return dominant

    def minimax_value(self, maximizing_player: int = 0) -> float:
        """
        Minimax-значение для игры с нулевой суммой (2 игрока).
        maximizing_player — индекс максимизирующего игрока.
        """
        if self.n_players != 2:
            raise ValueError("minimax_value поддерживает только 2-игровые игры.")
        strategy_counts = self._strategy_counts()
        min_player = 1 - maximizing_player
        best = -math.inf
        for s_max in range(strategy_counts[maximizing_player]):
            worst = math.inf
            for s_min in range(strategy_counts[min_player]):
                if maximizing_player == 0:
                    profile = (s_max, s_min)
                else:
                    profile = (s_min, s_max)
                payoff = self._get_payoff(profile)[maximizing_player]
                worst = min(worst, payoff)
            best = max(best, worst)
        return best


class DecisionNode:
    """Узел дерева решений."""

    def __init__(self, name: str, is_decision: bool = True,
                 probability: float = 1.0, value: float = 0.0):
        self.name = name
        self.is_decision = is_decision   # True = узел решения, False = случайный
        self.probability = probability    # для случайных узлов
        self.value = value                # конечная выплата (у листьев)
        self.children: list[tuple[str, 'DecisionNode']] = []   # (action_label, node)

    def add_child(self, action: str, node: 'DecisionNode') -> None:
        self.children.append((action, node))


class DecisionTree:
    """
    Дерево решений с вычислением ожидаемого значения (EMV).

    Поддерживает:
    - смешанные деревья: узлы решений и случайные узлы
    - roll-back вычисление EMV
    - нахождение оптимального пути решений
    """

    def __init__(self, root: DecisionNode):
        self.root = root

    def compute_emv(self, node: DecisionNode | None = None) -> float:
        """Вычисляет Expected Monetary Value (ожидаемое значение) методом rollback."""
        if node is None:
            node = self.root
        if not node.children:
            return node.value
        child_values = [
            (label, self.compute_emv(child) * child.probability, child)
            for label, child in node.children
        ]
        if node.is_decision:
            # Выбираем максимальное значение
            return max(v for _, v, _ in child_values)
        else:
            # Случайный узел: взвешенная сумма
            return sum(v for _, v, _ in child_values)

    def optimal_path(self, node: DecisionNode | None = None) -> list[str]:
        """Возвращает список меток действий оптимального пути."""
        if node is None:
            node = self.root
        if not node.children:
            return []
        child_values = [
            (label, self.compute_emv(child) * child.probability, child)
            for label, child in node.children
        ]
        if node.is_decision:
            best_label, _, best_child = max(child_values, key=lambda t: t[1])
            return [best_label] + self.optimal_path(best_child)
        else:
            # В случайных узлах следуем наиболее вероятной ветке для отображения пути
            best_label, _, best_child = max(child_values, key=lambda t: t[1])
            return [f"[chance:{best_label}]"] + self.optimal_path(best_child)

    def to_dict(self, node: DecisionNode | None = None) -> dict:
        if node is None:
            node = self.root
        return {
            'name':       node.name,
            'type':       'decision' if node.is_decision else 'chance',
            'emv':        round(self.compute_emv(node), 4),
            'children':   [
                {'action': lbl, **self.to_dict(child)}
                for lbl, child in node.children
            ],
        }


class MinimaxTree:
    """
    Дерево Minimax с alpha-beta отсечением.

    Используется для игр с полной информацией (шахматы, крестики-нолики и т.п.).
    """

    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth
        self.nodes_explored = 0
        self.nodes_pruned = 0

    def search(
        self,
        state: Any,
        depth: int,
        is_maximizing: bool,
        evaluate: Callable[[Any], float],
        get_children: Callable[[Any, bool], list[Any]],
        alpha: float = -math.inf,
        beta: float = math.inf,
    ) -> float:
        """
        Выполняет Minimax с alpha-beta отсечением.

        state           — текущее состояние игры
        depth           — оставшаяся глубина поиска
        is_maximizing   — ход максимизирующего игрока
        evaluate        — эвристическая функция оценки состояния (у листьев)
        get_children    — генератор дочерних состояний
        """
        self.nodes_explored += 1
        children = get_children(state, is_maximizing)
        if depth == 0 or not children:
            return evaluate(state)

        if is_maximizing:
            value = -math.inf
            for child in children:
                child_value = self.search(
                    child, depth - 1, False, evaluate, get_children, alpha, beta)
                value = max(value, child_value)
                alpha = max(alpha, value)
                if beta <= alpha:
                    self.nodes_pruned += 1
                    break
            return value
        else:
            value = math.inf
            for child in children:
                child_value = self.search(
                    child, depth - 1, True, evaluate, get_children, alpha, beta)
                value = min(value, child_value)
                beta = min(beta, value)
                if beta <= alpha:
                    self.nodes_pruned += 1
                    break
            return value

    def best_move(
        self,
        state: Any,
        depth: int,
        evaluate: Callable[[Any], float],
        get_children: Callable[[Any, bool], list[Any]],
    ) -> tuple[Any, float]:
        """
        Возвращает лучший ход и его оценку (ищет с позиции максимизирующего).
        """
        self.nodes_explored = 0
        self.nodes_pruned = 0
        best_child = None
        best_value = -math.inf
        children = get_children(state, True)
        for child in children:
            value = self.search(
                child, depth - 1, False, evaluate, get_children,
                -math.inf, math.inf
            )
            if value > best_value:
                best_value = value
                best_child = child
        return best_child, best_value

    def stats(self) -> dict:
        return {
            'nodes_explored': self.nodes_explored,
            'nodes_pruned':   self.nodes_pruned,
            'prune_rate':     (
                round(self.nodes_pruned / max(1, self.nodes_explored + self.nodes_pruned), 3)
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Единый фасад
# ─────────────────────────────────────────────────────────────────────────────

class LogicalReasoningSystem:
    """
    Единая точка доступа ко всем компонентам логического рассуждения.

    Компоненты:
    - paradox    : ParadoxDetector
    - search     : ExhaustiveSearch
    - optimize   : OptimizationEngine (DP, CSP, scheduling, routing)
    - game       : NormalFormGame (создаётся фабрикой)
    - tree       : DecisionTree / MinimaxTree (создаются фабриками)

    Гарантии:
    - Все методы оптимизации — строгие (DP / точный перебор), не эвристики
    - Эвристики предоставляются для сравнения и анализа разрыва оптимальности
    - Полная прозрачность метода и тип гарантии (оптимум vs. приближение)
    """

    def __init__(self, max_search_nodes: int = 500_000):
        self.paradox  = ParadoxDetector()
        self.search   = ExhaustiveSearch(max_nodes=max_search_nodes)
        self.optimize = None  # инициируется лениво

    # ── Фабричные методы ──────────────────────────────────────────────────────

    @staticmethod
    def create_normal_form_game(player_names: list[str],
                                payoff_matrix: list[Any]) -> NormalFormGame:
        return NormalFormGame(player_names, payoff_matrix)

    @staticmethod
    def create_decision_tree(root: DecisionNode) -> DecisionTree:
        return DecisionTree(root)

    @staticmethod
    def create_minimax(max_depth: int = 10) -> MinimaxTree:
        return MinimaxTree(max_depth=max_depth)

    @staticmethod
    def make_decision_node(name: str, is_decision: bool = True,
                           probability: float = 1.0,
                           value: float = 0.0) -> DecisionNode:
        return DecisionNode(name, is_decision, probability, value)

    # ── Быстрые утилиты ───────────────────────────────────────────────────────

    def check_paradox(self, statement: str) -> dict:
        """Быстрая проверка одного высказывания на парадоксальность."""
        result = self.paradox.analyze_statement(statement)
        if result:
            return {'paradox': True, **result.to_dict()}
        return {'paradox': False, 'explanation': 'Парадокс не обнаружен.'}

    def enumerate(self, items: list[Any], length: int | None = None,
                  mode: str = 'combinations') -> SearchResult:
        """Полный перебор комбинаций."""
        return self.search.enumerate_combinations(items, length, mode)

    def solve(self, variables: list[str], domains: dict[str, list[Any]],
              constraints: list[Callable], find_all: bool = True) -> SearchResult:
        """Решение CSP с backtracking + AC-3."""
        return self.search.solve_csp(variables, domains, constraints, find_all)

    def solve_graph_search(self, graph: dict[str, list[tuple[str, float]]],
                           start: str, goal: str,
                           heuristics: dict[str, float] | None = None) -> dict:
        """Точный поиск пути: A* + верификация через Dijkstra."""
        solver = AStarSolver(graph, heuristics)
        a_star = solver.solve(start, goal)
        dijkstra = solver.solve_dijkstra(start, goal)
        return {
            'a_star': a_star.to_dict(),
            'dijkstra': dijkstra.to_dict(),
            'verified': (
                a_star.path == dijkstra.path
                and abs(a_star.total_cost - dijkstra.total_cost) < 1e-9
            ),
        }

    # ── Оптимизация ───────────────────────────────────────────────────────────

    def knapsack_analysis(self, items: list[Any], capacity: float) -> dict:
        """
        Комплексный анализ unbounded knapsack:
        сравнивает DP (оптимум) с Greedy (эвристика).

        items: list[Item] с полями (item_id, cost, value, probability)
        """
        from .optimization_engine import OptimizationEngine
        return OptimizationEngine.knapsack_analysis(items, capacity)
