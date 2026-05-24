"""
brain/memory/ — Memory System

Four layers of memory, all accessible through MemoryManager:

    WorkingMemory    — current session, RAM only, fast, limited
    EpisodicMemory   — conversation history, SQLite + ChromaDB, survives restarts
    SemanticMemory   — facts & knowledge, vector search, RAG pipeline
    ProceduralMemory — learned skills + action trajectories (S3.4)

Brain only imports MemoryManager. Never imports layers directly.
"""

from .memory_manager import MemoryManager
from .working_memory import WorkingMemory
from .episodic_memory import EpisodicMemory
from .semantic_memory import SemanticMemory
from .procedural_memory import ProceduralMemory

__all__ = [
    "MemoryManager",
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "ProceduralMemory",
]
