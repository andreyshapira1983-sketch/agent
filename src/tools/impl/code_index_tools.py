"""
Инструменты индекса кода: сводка модулей, пересборка индекса, эмбеддинги и семантический поиск.
Позволяют агенту «понимать» архитектуру и находить модули по смыслу («какие отвечают за X»).
"""
from __future__ import annotations

from src.tools.registry import register
from src.knowledge.code_index import (
    get_code_index_summary,
    build_code_index,
    embed_code_index,
    search_code_index_summary,
)


def _get_code_index_summary_tool(max_entries: int = 80) -> str:
    return get_code_index_summary(max_entries=max_entries)


def _build_code_index_tool(dirs: str = "src", with_embeddings: bool = False) -> str:
    """Построить или обновить индекс кода. dirs — через запятую. with_embeddings — сразу построить эмбеддинги для поиска."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    d = [s.strip() for s in (dirs or "src").split(",") if s.strip()]
    n = build_code_index(root=root, dirs=d, with_embeddings=with_embeddings)
    msg = f"Индекс обновлён: {n} модулей."
    if with_embeddings and n > 0:
        msg += " Эмбеддинги построены — можно использовать search_code_index."
    return msg


def _embed_code_index_tool() -> str:
    """Построить эмбеддинги для текущего индекса (нужен OPENAI_API_KEY)."""
    try:
        n = embed_code_index()
        return f"Эмбеддинги построены: {n} векторов. Можно использовать search_code_index."
    except Exception as e:
        return f"Ошибка: {e}"


def _search_code_index_tool(query: str, top_k: int = 5) -> str:
    """Семантический поиск: какие модули отвечают за запрос. Подмешивай результат в контекст при вопросах об архитектуре."""
    return search_code_index_summary(query=(query or "").strip(), top_k=max(1, min(20, top_k)))


def register_code_index_tools() -> None:
    register(
        "get_code_index_summary",
        {
            "type": "function",
            "function": {
                "name": "get_code_index_summary",
                "description": "Получить сводку модулей проекта (path и module docstring). Используй для понимания архитектуры без чтения всех файлов. Сначала построй индекс: build_code_index.",
                "parameters": {
                    "type": "object",
                    "properties": {"max_entries": {"type": "integer", "description": "Макс. число модулей в сводке (по умолчанию 80)."}},
                },
            },
        },
        lambda max_entries=80: _get_code_index_summary_tool(max_entries=max_entries),
    )
    register(
        "build_code_index",
        {
            "type": "function",
            "function": {
                "name": "build_code_index",
                "description": "Построить или обновить индекс кода (модули .py, docstring). Результат в data/code_index.json. С with_embeddings=true сразу строятся эмбеддинги для search_code_index.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dirs": {"type": "string", "description": "Каталоги через запятую, например src или src,tests."},
                        "with_embeddings": {"type": "boolean", "description": "Если true — после индекса построить эмбеддинги для семантического поиска (нужен OPENAI_API_KEY)."},
                    },
                },
            },
        },
        lambda dirs="src", with_embeddings=False: _build_code_index_tool(dirs=dirs, with_embeddings=with_embeddings),
    )
    register(
        "embed_code_index",
        {
            "type": "function",
            "function": {
                "name": "embed_code_index",
                "description": "Построить эмбеддинги для уже собранного индекса кода. Нужен OPENAI_API_KEY. После этого search_code_index будет возвращать релевантные модули по запросу.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        lambda: _embed_code_index_tool(),
    )
    register(
        "search_code_index",
        {
            "type": "function",
            "function": {
                "name": "search_code_index",
                "description": "Семантический поиск по индексу кода: «какие модули отвечают за X». Запрос — естественный язык (например: обработка Telegram, планировщик, эмоции). Результат подмешивай в контекст при вопросах об архитектуре. Требует построенный индекс и embed_code_index.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Запрос естественным языком: за что отвечают модули (например: отправка сообщений в Telegram, очередь задач, эмоции агента)."},
                        "top_k": {"type": "integer", "description": "Сколько модулей вернуть (по умолчанию 5, макс. 20)."},
                    },
                    "required": ["query"],
                },
            },
        },
        lambda query="", top_k=5: _search_code_index_tool(query=query, top_k=top_k),
    )
