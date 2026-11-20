"""Self-knowledge versus subject tasks: which memory records are NOT mixed in when reading (consumer: core/loop_memory_read.py)."""
from __future__ import annotations

import re

#: Self-knowledge is a record whose sources are only the agent's own files (no book, no network); recalled BY TOPIC.
_SELF_PATH = re.compile(
    r"(?<![\w.])(?:file:)?(?:core|data|logs|tests|knowledge|scripts|docs|config)/[\w./-]+|\bagent_tick\.py\b")
_SUBJECT_PATH = re.compile(r"math_study/|knowledge_library/|https?://")


def is_self_knowledge(content: str) -> bool:
    """A record about the agent's own structure, not about a subject."""
    return bool(_SELF_PATH.search(content or "")) and not _SUBJECT_PATH.search(content or "")


def asks_about_self(question: str) -> bool:
    """The question names the agent's own files, so self-knowledge fits."""
    return bool(_SELF_PATH.search(question or ""))


#: Subject task: book, library, network. Only there does self-knowledge interfere; a neutral question about itself without a path is left alone.
_SUBJECT_TASK = re.compile(
    r"math_study/|knowledge_library/|https?://|\bкниг[аеиуо]\w*|\bинтернет\w*|\binternet\b|\bweb\b",
    re.IGNORECASE)


def self_knowledge_off_topic(question: str) -> bool:
    """A task about a subject, not the agent's own files: do not mix in self-knowledge."""
    return bool(_SUBJECT_TASK.search(question or "")) and not asks_about_self(question)
