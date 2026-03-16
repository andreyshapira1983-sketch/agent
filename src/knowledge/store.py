"""
Knowledge store: add documents, persist to JSON. Optional: vector DB (Chroma) later.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_config_dir = Path(__file__).resolve().parent.parent.parent / "config"
_data_dir = _config_dir.parent / "data"
_store_path = _data_dir / "knowledge.json"
_docs: list[dict[str, Any]] = []
_loaded = False


def _load() -> None:
    global _docs, _loaded
    if _loaded:
        return
    _loaded = True
    if _store_path.exists():
        try:
            _docs = json.loads(_store_path.read_text(encoding="utf-8"))
        except Exception:
            _docs = []


def _save() -> None:
    _data_dir.mkdir(parents=True, exist_ok=True)
    _store_path.write_text(json.dumps(_docs, ensure_ascii=False, indent=2), encoding="utf-8")


def add_document(content: str, meta: dict[str, Any] | None = None) -> None:
    _load()
    _docs.append({"content": content, "meta": meta or {}})
    _save()


def list_documents() -> list[dict[str, Any]]:
    _load()
    return list(_docs)


def _has_architecture_docs() -> bool:
    _load()
    return any((d.get("meta") or {}).get("type") == "architecture" for d in _docs)


def seed_architecture_docs(root_path: str | None = None) -> int:
    """
    Добавить архитектурные документы в Knowledge Store (один раз).
    Возвращает число добавленных документов.
    """
    from pathlib import Path
    if _has_architecture_docs():
        return 0
    root = Path(root_path or _config_dir.parent)
    default_paths = [
        "README.md",
        "docs/ARCHITECTURE_PLAN.md",
        "EVOLUTION.md",
        "docs/GENETICS.md",
        "config/SELF_MODEL.md",
    ]
    added = 0
    for rel in default_paths:
        p = root / rel
        if not p.exists() or not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            add_document(content, meta={"type": "architecture", "source": rel})
            added += 1
        except Exception as e:
            logger.debug("Skip file %s: %s", rel, e)
            continue
    return added
