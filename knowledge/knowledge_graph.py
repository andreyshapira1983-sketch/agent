# KnowledgeGraph — граф знаний агента — компонент Слоя 2 (Knowledge System)
# Архитектура автономного AI-агента
#
# Направленный граф концептов и связей между ними.
# Хранит тройки (субъект, предикат, объект):
#   ("Python",   "is_a",       "programming_language")
#   ("Python",   "used_for",   "data science")
#   ("pandas",   "part_of",    "Python ecosystem")
#
# Работает БЕЗ внешних зависимостей — чистый Python.
# Поддерживает: добавление, поиск, обход, путь между концептами, слияние.

from collections import deque


class KnowledgeGraph:
    """
    In-memory направленный граф знаний.

    Хранит тройки: (subject, predicate, object).
    Поддерживает два индекса для быстрого поиска:
        _outgoing[subject] = [(predicate, object), ...]
        _incoming[object]  = [(predicate, subject), ...]

    Используется:
        - KnowledgeSystem (Слой 2)   — как встроенный граф по умолчанию
        - Causal Reasoning (Слой 41) — причинно-следственные связи
        - Cognitive Core (Слой 3)    — обогащение контекста запроса
    """

    def __init__(self):
        # subject → [(predicate, object), ...]
        self._outgoing: dict[str, list[tuple[str, str]]] = {}
        # object  → [(predicate, subject), ...]   (обратный индекс)
        self._incoming: dict[str, list[tuple[str, str]]] = {}
        self._triple_count = 0

    # ── Добавление / удаление ─────────────────────────────────────────────────

    def add_triple(self, subject: str, predicate: str, obj: str):
        """
        Добавляет тройку (subject, predicate, object) в граф.
        Дубликаты игнорируются.

        Args:
            subject   — исходный концепт
            predicate — тип связи ('is_a', 'part_of', 'used_for', ...)
            obj       — целевой концепт
        """
        s, p, o = str(subject).strip(), str(predicate).strip(), str(obj).strip()
        if not s or not p or not o:
            return

        # Проверка дубликата
        existing = self._outgoing.get(s, [])
        if (p, o) in existing:
            return

        self._outgoing.setdefault(s, []).append((p, o))
        self._incoming.setdefault(o, []).append((p, s))
        self._triple_count += 1

    def remove_triple(self, subject: str, predicate: str, obj: str):
        """Удаляет тройку из графа."""
        s, p, o = str(subject).strip(), str(predicate).strip(), str(obj).strip()

        out = self._outgoing.get(s, [])
        if (p, o) in out:
            out.remove((p, o))
            self._triple_count -= 1

        inc = self._incoming.get(o, [])
        if (p, s) in inc:
            inc.remove((p, s))

    def remove_concept(self, concept: str):
        """
        Полностью удаляет концепт и все связанные с ним тройки.
        """
        concept = str(concept).strip()

        # Удаляем все исходящие тройки
        for (p, o) in list(self._outgoing.get(concept, [])):
            self.remove_triple(concept, p, o)

        # Удаляем все входящие тройки
        for (p, s) in list(self._incoming.get(concept, [])):
            self.remove_triple(s, p, concept)

        self._outgoing.pop(concept, None)
        self._incoming.pop(concept, None)

    # ── Запросы ───────────────────────────────────────────────────────────────

    def get_related(self, concept: str,
                    predicate: str | None = None) -> list[dict]:
        """
        Возвращает концепты, связанные С данным концептом исходящими рёбрами.

        Args:
            concept   — исходный концепт
            predicate — фильтр по типу связи (None = все)

        Returns:
            Список {'object': str, 'predicate': str}
        """
        results = []
        for (p, o) in self._outgoing.get(str(concept).strip(), []):
            if predicate is None or p == predicate:
                results.append({'object': o, 'predicate': p})
        return results

    def get_incoming(self, concept: str,
                     predicate: str | None = None) -> list[dict]:
        """
        Возвращает концепты, которые УКАЗЫВАЮТ на данный концепт.

        Returns:
            Список {'subject': str, 'predicate': str}
        """
        results = []
        for (p, s) in self._incoming.get(str(concept).strip(), []):
            if predicate is None or p == predicate:
                results.append({'subject': s, 'predicate': p})
        return results

    def query(self, query_str: str) -> list[dict]:
        """
        Простой запрос по шаблону тройки.
        Используйте '?' для неизвестной части.

        Форматы:
            "Python is_a ?"          → что такое Python?
            "? is_a programming_language"  → что является языком программирования?
            "Python ? data science"  → как Python связан с data science?
            "Python ? ?"             → все связи Python

        Returns:
            Список {'subject', 'predicate', 'object'}
        """
        parts = query_str.strip().split(None, 2)
        if len(parts) != 3:
            return []

        s_q, p_q, o_q = parts

        results = []
        for subject, edges in self._outgoing.items():
            if s_q != '?' and subject.lower() != s_q.lower():
                continue
            for (pred, obj) in edges:
                if p_q != '?' and pred.lower() != p_q.lower():
                    continue
                if o_q != '?' and obj.lower() != o_q.lower():
                    continue
                results.append({'subject': subject, 'predicate': pred, 'object': obj})

        return results

    def traverse(self, start: str, max_depth: int = 2,
                 predicate: str | None = None) -> dict[str, int]:
        """
        BFS-обход графа от стартового концепта.
        Возвращает все достижимые концепты и расстояние до них.

        Args:
            start     — стартовый концепт
            max_depth — максимальная глубина обхода
            predicate — ограничить обход только этим типом связи

        Returns:
            {concept: depth, ...}
        """
        start = str(start).strip()
        visited: dict[str, int] = {start: 0}
        queue = deque([(start, 0)])

        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for (p, neighbor) in self._outgoing.get(node, []):
                if predicate and p != predicate:
                    continue
                if neighbor not in visited:
                    visited[neighbor] = depth + 1
                    queue.append((neighbor, depth + 1))

        return visited

    def find_path(self, start: str, end: str,
                  max_depth: int = 6) -> list[dict] | None:
        """
        Находит кратчайший путь между двумя концептами (BFS).

        Returns:
            Список троек на пути [{'subject', 'predicate', 'object'}, ...]
            или None если пути нет.
        """
        start, end = str(start).strip(), str(end).strip()
        if start == end:
            return []

        # BFS с отслеживанием пути
        queue = deque([(start, [])])
        visited = {start}

        while queue:
            node, path = queue.popleft()
            if len(path) >= max_depth:
                continue
            for (pred, neighbor) in self._outgoing.get(node, []):
                step = {'subject': node, 'predicate': pred, 'object': neighbor}
                new_path = path + [step]
                if neighbor == end:
                    return new_path
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, new_path))

        return None  # путь не найден

    def get_neighborhood(self, concept: str,
                         depth: int = 1) -> dict:
        """
        Возвращает окрестность концепта — все тройки в радиусе depth рёбер.

        Returns:
            {'concepts': [...], 'triples': [...]}
        """
        reachable = self.traverse(concept, max_depth=depth)
        triples = []
        for node in reachable:
            for (p, o) in self._outgoing.get(node, []):
                if o in reachable:
                    triples.append({'subject': node, 'predicate': p, 'object': o})
        return {
            'concepts': list(reachable.keys()),
            'triples':  triples,
        }

    # ── Слияние и импорт ──────────────────────────────────────────────────────

    def merge(self, other: 'KnowledgeGraph'):
        """Объединяет другой граф в текущий (без дубликатов)."""
        for subject, edges in getattr(other, '_outgoing', {}).items():
            for (pred, obj) in edges:
                self.add_triple(subject, pred, obj)

    def add_triples_bulk(self, triples: list[tuple[str, str, str]]):
        """Массовое добавление троек: [(s, p, o), ...]"""
        for s, p, o in triples:
            self.add_triple(s, p, o)

    def all_triples(self) -> list[tuple[str, str, str]]:
        """Возвращает все тройки в виде [(subject, predicate, object), ...]"""
        result = []
        for subject, edges in self._outgoing.items():
            for (pred, obj) in edges:
                result.append((subject, pred, obj))
        return result

    # ── Статистика ────────────────────────────────────────────────────────────

    def concepts(self) -> list[str]:
        """Возвращает все концепты (узлы графа)."""
        nodes = set(self._outgoing.keys()) | set(self._incoming.keys())
        return sorted(nodes)

    def predicates(self) -> list[str]:
        """Возвращает все типы связей, используемых в графе."""
        preds = set()
        for edges in self._outgoing.values():
            for (p, _) in edges:
                preds.add(p)
        return sorted(preds)

    def summary(self) -> dict:
        """Статистика графа."""
        return {
            'concepts':  len(self.concepts()),
            'triples':   self._triple_count,
            'predicates': len(self.predicates()),
            'top_concepts': sorted(
                self._outgoing.items(),
                key=lambda x: len(x[1]),
                reverse=True
            )[:5],
        }

    def __len__(self):
        return self._triple_count

    def __repr__(self):
        return (f"KnowledgeGraph("
                f"{len(self.concepts())} concepts, "
                f"{self._triple_count} triples)")
