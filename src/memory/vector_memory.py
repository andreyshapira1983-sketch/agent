"""
Векторная память: хранение и поиск по тексту. MVP — in-memory, поиск по подстроке.
При необходимости подключают faiss/embeddings (см. импорты и bandit/mypy).
"""
from __future__ import annotations

from typing import Any

# При использовании faiss/embeddings: import faiss, затем индексация векторов
# _store: list[dict]  # {"text": str, "meta": dict, "embedding": optional}


class VectorMemory:
    """Хранилище записей с поиском по содержимому (MVP: подстрока в тексте)."""

    def __init__(self) -> None:
        self._store: list[dict[str, Any]] = []

    def add(self, text: str, meta: dict[str, Any] | None = None) -> None:
        """Добавить запись. meta — произвольные метаданные (источник, тип и т.д.)."""
        self._store.append({"text": text, "meta": meta or {}})

    def store(self, data: str | dict[str, Any]) -> None:
        """Добавить запись. data — строка или dict с ключом "text" и опционально "meta"."""
        if isinstance(data, str):
            self.add(data)
        else:
            self._store.append({"text": data.get("text", ""), "meta": data.get("meta", {})})

    def search(self, query: str, k: int = 10) -> list[dict[str, Any]]:
        """Поиск записей, содержащих query (подстрока). Возвращает до k записей."""
        query_lower = query.lower()
        out = [r for r in self._store if query_lower in (r.get("text") or "").lower()]
        return out[:k]

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        """Алиас для search: возвращает список записей, где встречается query."""
        return self.search(query)

    def clear(self) -> None:
        """Очистить хранилище."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
