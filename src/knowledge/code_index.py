"""
Индекс кода проекта: разбор модулей (.py), извлечение docstring и превью.
Хранится в data/code_index.json. Embeddings в data/code_index_embeddings.json — семантический поиск.
"""
from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_root = Path(__file__).resolve().parent.parent.parent
_data_dir = _root / "data"
_index_path = _data_dir / "code_index.json"
_embeddings_path = _data_dir / "code_index_embeddings.json"
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules"}
_MAX_FILE_SIZE = 80_000
_PREVIEW_LINES = 15
_EMBED_MODEL = "text-embedding-3-small"
_BATCH_SIZE = 50


def _extract_module_docstring(content: str) -> str:
    """Извлечь module-level docstring из исходника."""
    try:
        tree = ast.parse(content)
        if ast.get_docstring(tree):
            return (ast.get_docstring(tree) or "").strip()[:500]
    except Exception:
        pass
    return ""


def _preview_content(content: str, max_lines: int = _PREVIEW_LINES) -> str:
    """Первые N строк без лишних пробелов."""
    lines = content.splitlines()[:max_lines]
    return "\n".join(lines).strip()[:800]


def build_code_index(
    root: Path | None = None,
    dirs: list[str] | None = None,
    max_file_size: int = _MAX_FILE_SIZE,
    with_embeddings: bool = False,
) -> int:
    """
    Обойти указанные каталоги, для каждого .py извлечь docstring и превью, сохранить в data/code_index.json.
    Если with_embeddings=True — после индекса вызвать embed_code_index() (нужен OPENAI_API_KEY).
    Возвращает число проиндексированных файлов.
    """
    root = root or _root
    dirs = dirs or ["src"]
    result: list[dict[str, Any]] = []
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(s in path.parts for s in _SKIP_DIRS):
                continue
            try:
                if path.stat().st_size > max_file_size:
                    result.append({
                        "path": str(path.relative_to(root)).replace("\\", "/"),
                        "module_doc": "(файл слишком большой, пропущен)",
                        "preview": "",
                        "size": path.stat().st_size,
                    })
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                rel = path.relative_to(root)
                result.append({
                    "path": str(rel).replace("\\", "/"),
                    "module_doc": _extract_module_docstring(content),
                    "preview": _preview_content(content),
                    "size": len(content),
                })
            except Exception as e:
                _log.debug("code_index skip %s: %s", path, e)
    _data_dir.mkdir(parents=True, exist_ok=True)
    _index_path.write_text(json.dumps(result, ensure_ascii=False, indent=0), encoding="utf-8")
    n = len(result)
    if with_embeddings and n > 0:
        try:
            embed_code_index()
        except Exception as e:
            _log.warning("build_code_index: embeddings failed: %s", e)
    return n


def get_code_index() -> list[dict[str, Any]]:
    """Загрузить индекс из data/code_index.json."""
    if not _index_path.exists():
        return []
    try:
        return json.loads(_index_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_code_index_summary(max_entries: int = 80) -> str:
    """
    Текстовая сводка индекса для агента: список модулей и их краткое описание (docstring).
    Позволяет «понимать» архитектуру глубже без чтения всех файлов.
    """
    index = get_code_index()
    if not index:
        return "Индекс кода не построен. Построй: build_code_index() или вызов инструмента build_code_index."
    lines = []
    for i, entry in enumerate(index):
        if i >= max_entries:
            lines.append(f"... и ещё {len(index) - max_entries} модулей.")
            break
        path = entry.get("path", "")
        doc = (entry.get("module_doc") or "").strip()
        if doc:
            lines.append(f"- {path}: {doc[:200]}{'…' if len(doc) > 200 else ''}")
        else:
            lines.append(f"- {path}")
    return "Модули проекта (path → docstring):\n" + "\n".join(lines)


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Получить эмбеддинги для списка текстов через OpenAI. Возвращает list of vectors."""
    if not texts:
        return []
    key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_KEY_API", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY (или OPEN_KEY_API) нужен для эмбеддингов.")
    from openai import OpenAI
    client = OpenAI(api_key=key)
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        r = client.embeddings.create(input=batch, model=_EMBED_MODEL)
        for d in r.data:
            out.append(d.embedding)
    return out


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Косинусное сходство между двумя векторами."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def embed_code_index() -> int:
    """
    Построить эмбеддинги для текущего индекса (path + module_doc). Сохранить в data/code_index_embeddings.json.
    Требует OPENAI_API_KEY. Возвращает число проэмбедженных записей.
    """
    index = get_code_index()
    if not index:
        return 0
    texts = []
    for entry in index:
        path = entry.get("path", "")
        doc = (entry.get("module_doc") or "").strip()
        text = f"{path}\n{doc}"[:2000]
        texts.append(text)
    vectors = _embed_texts(texts)
    if len(vectors) != len(index):
        _log.warning("embed_code_index: got %s vectors for %s entries", len(vectors), len(index))
    _data_dir.mkdir(parents=True, exist_ok=True)
    _embeddings_path.write_text(json.dumps(vectors), encoding="utf-8")
    return len(vectors)


def _load_embeddings() -> list[list[float]]:
    """Загрузить векторы из code_index_embeddings.json."""
    if not _embeddings_path.exists():
        return []
    try:
        return json.loads(_embeddings_path.read_text(encoding="utf-8"))
    except Exception:
        return []


def search_code_index(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """
    Семантический поиск по индексу: какие модули отвечают за запрос.
    Возвращает список {path, module_doc, score} отсортированный по убыванию score.
    Требует построенный индекс и embed_code_index().
    """
    index = get_code_index()
    vectors = _load_embeddings()
    if not index or not vectors or len(vectors) != len(index):
        return []
    q_embed = _embed_texts([query.strip()[:2000]])
    if not q_embed:
        return []
    qv = q_embed[0]
    scored: list[tuple[int, float]] = []
    for i, v in enumerate(vectors):
        s = _cosine_sim(qv, v)
        scored.append((i, s))
    scored.sort(key=lambda x: -x[1])
    result = []
    for i, score in scored[:top_k]:
        entry = index[i]
        result.append({
            "path": entry.get("path", ""),
            "module_doc": (entry.get("module_doc") or "").strip(),
            "score": round(score, 4),
        })
    return result


def search_code_index_summary(query: str, top_k: int = 5) -> str:
    """
    Текстовая сводка семантического поиска для агента: «какие модули отвечают за X».
    Подмешивать в контекст при вопросах об архитектуре.
    """
    index = get_code_index()
    if not index:
        return "Индекс кода не построен. Вызови build_code_index."
    if not _embeddings_path.exists():
        return "Эмбеддинги не построены. Вызови build_code_index (с with_embeddings=True) или embed_code_index."
    hits = search_code_index(query, top_k=top_k)
    if not hits:
        return "По запросу ничего не найдено."
    lines = [f"По запросу «{query[:80]}» релевантные модули:"]
    for h in hits:
        path = h.get("path", "")
        doc = (h.get("module_doc") or "")[:200]
        score = h.get("score", 0)
        lines.append(f"- {path} (score={score}): {doc}{'…' if len(h.get('module_doc') or '') > 200 else ''}")
    return "\n".join(lines)
