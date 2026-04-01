# knowledge — Knowledge System (Слой 2) + Acquisition Pipeline (Слой 31)
#             Data Lifecycle (Слой 33) + Knowledge Verification (Слой 46)
# Память и знания: short-term, long-term, episodic, semantic, vector DB, граф знаний.
# Автоматическое пополнение знаний из внешних источников.
# Верификация достоверности, управление жизненным циклом данных.
from .knowledge_system import KnowledgeSystem
from .vector_store import VectorStore, VectorDocument, SearchResult
from .acquisition_pipeline import KnowledgeAcquisitionPipeline, KnowledgeSource, SourceStatus
from .data_lifecycle import DataLifecycleManager, DataAge
from .knowledge_verification import (
    KnowledgeVerificationSystem, VerificationResult, VerificationStatus,
)

__all__ = [
    'KnowledgeSystem',
    'KnowledgeAcquisitionPipeline', 'KnowledgeSource', 'SourceStatus',
    'DataLifecycleManager', 'DataAge',
    'KnowledgeVerificationSystem', 'VerificationResult', 'VerificationStatus',
    'VectorStore', 'VectorDocument', 'SearchResult',
]
