# Search Backend — поиск через DuckDuckGo (бесплатно, без API-ключа)
# Подключается к SearchTool(backend=DuckDuckGoBackend())

import importlib

try:
    from safety.content_fence import sanitize_external
except ImportError:
    import logging as _sb_log
    _sb_log.getLogger(__name__).warning(
        "safety.content_fence не доступен — sanitize_external будет обрезать текст без инъекционной фильтрации"
    )

    def sanitize_external(text: str, source: str = 'unknown',
                          max_len: int = 5000,
                          strip_injections: bool = True) -> tuple[str, list[str]]:
        # Минимальная защита: ограничение длины, даже без полной фильтрации
        truncated = text[:max_len] if len(text) > max_len else text
        warnings = ['content_fence unavailable: only length-truncation applied']
        return truncated, warnings


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
            results = []
            for r in raw:
                snippet = r.get("body", "")
                title = r.get("title", "")
                fenced_snippet, _ = sanitize_external(snippet, source='duckduckgo_search')
                fenced_title, _ = sanitize_external(title, source='duckduckgo_search')
                results.append({
                    "title":   fenced_title,
                    "url":     r.get("href", ""),
                    "snippet": fenced_snippet,
                })
            return results
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
            results = []
            for r in raw:
                fenced_title, _ = sanitize_external(r.get("title", ""), source='duckduckgo_news')
                fenced_snippet, _ = sanitize_external(r.get("body", ""), source='duckduckgo_news')
                results.append({
                    "title":   fenced_title,
                    "url":     r.get("url", ""),
                    "snippet": fenced_snippet,
                    "date":    r.get("date", ""),
                    "source":  r.get("source", ""),
                })
            return results
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
