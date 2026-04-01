# Search Backend — поиск через DuckDuckGo (бесплатно, без API-ключа)
# Подключается к SearchTool(backend=DuckDuckGoBackend())

import importlib


class DuckDuckGoBackend:
    """
    Поисковый backend на основе duckduckgo_search.

    Устанавливается: pip install duckduckgo-search

    Реализует интерфейс:
        backend.search(query, num_results=5) → list[dict]

    Каждый элемент результата:
        {'title': str, 'url': str, 'snippet': str}
    """

    def search(self, query: str, num_results: int = 5) -> list[dict]:
        """
        Выполняет текстовый поиск через DuckDuckGo.

        Args:
            query       — поисковый запрос
            num_results — максимальное число результатов

        Returns:
            Список словарей с ключами title, url, snippet.
        """
        DDGS = self._import_ddgs()
        if DDGS is None:
            return [{"error": "ddgs не установлен. Запусти: pip install duckduckgo-search",
                     "title": "", "url": "", "snippet": ""}]

        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=num_results))
            return [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in raw
            ]
        except (ValueError, TimeoutError, RuntimeError) as e:
            return [{"error": str(e), "title": "", "url": "", "snippet": ""}]

    def news(self, query: str, num_results: int = 5) -> list[dict]:
        """Поиск новостей."""
        try:
            DDGS = self._import_ddgs()
            if DDGS is None:
                return [{"error": "ddgs не установлен"}]
            with DDGS() as ddgs:
                raw = list(ddgs.news(query, max_results=num_results))
            return [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", ""),
                    "snippet": r.get("body", ""),
                    "date":    r.get("date", ""),
                    "source":  r.get("source", ""),
                }
                for r in raw
            ]
        except (ValueError, TimeoutError, RuntimeError) as e:
            return [{"error": str(e)}]

    def images(self, query: str, num_results: int = 5) -> list[dict]:
        """Поиск изображений."""
        try:
            DDGS = self._import_ddgs()
            if DDGS is None:
                return [{"error": "ddgs не установлен"}]
            with DDGS() as ddgs:
                raw = list(ddgs.images(query, max_results=num_results))
            return [
                {
                    "title": r.get("title", ""),
                    "url":   r.get("image", ""),
                    "source": r.get("url", ""),
                }
                for r in raw
            ]
        except (ValueError, TimeoutError, RuntimeError) as e:
            return [{"error": str(e)}]

    @staticmethod
    def _import_ddgs():
        try:
            DDGS = getattr(importlib.import_module('ddgs'), 'DDGS')
            return DDGS
        except ImportError:
            try:
                DDGS = getattr(importlib.import_module('duckduckgo_search'), 'DDGS')
                return DDGS
            except ImportError:
                return None
